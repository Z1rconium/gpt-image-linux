from pathlib import Path

import pytest

from backend.app.core import settings as config
from backend.app.core.validators import normalize_r2_endpoint_url
from backend.app.integrations import r2_sync
from backend.app.repositories import storage


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        self.client.paginated.append((Bucket, Prefix))
        yield {
            "Contents": [
                {"Key": key}
                for key in sorted(self.client.keys)
                if key.startswith(Prefix)
            ]
        }


class FakeNotFoundError(Exception):
    response = {
        "Error": {"Code": "404"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeS3Client:
    def __init__(self, keys=None, fail_upload_keys=None, fail_delete=False):
        self.keys = set(keys or [])
        self.fail_upload_keys = set(fail_upload_keys or [])
        self.fail_delete = fail_delete
        self.uploaded: list[tuple[str, str, dict]] = []
        self.put_objects: list[str] = []
        self.deleted: list[str] = []
        self.paginated: list[tuple[str, str]] = []
        self.headed: list[str] = []

    def head_bucket(self, *, Bucket):
        assert Bucket

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        assert Bucket
        assert MaxKeys == 1
        return {"Contents": [{"Key": next(iter(self.keys))}]} if self.keys else {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        assert Bucket
        assert Body == b"ok"
        assert ContentType == "text/plain"
        self.keys.add(Key)
        self.put_objects.append(Key)

    def head_object(self, *, Bucket, Key):
        assert Bucket
        self.headed.append(Key)
        if Key not in self.keys:
            raise FakeNotFoundError()
        return {}

    def delete_object(self, *, Bucket, Key):
        assert Bucket
        self.deleted.append(Key)
        if self.fail_delete:
            raise RuntimeError("delete denied")
        self.keys.discard(Key)

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)

    def upload_file(self, filename, bucket, key, ExtraArgs):
        assert Path(filename).exists()
        assert bucket
        if key in self.fail_upload_keys:
            raise RuntimeError("upload denied")
        self.keys.add(key)
        self.uploaded.append((filename, key, ExtraArgs))


def r2_settings(**overrides):
    settings = {
        "enabled": True,
        "endpoint_url": "https://account.r2.cloudflarestorage.com",
        "bucket_name": "image-backups",
        "region": "auto",
        "key_prefix": "gallery/",
        "access_key_id": "key-id",
        "secret_access_key": "secret-key",
    }
    settings.update(overrides)
    return settings


@pytest.fixture()
def image_dir(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(config, "IMAGES_DIR", str(images))
    return images


@pytest.fixture()
def storage_runtime(tmp_path, monkeypatch):
    images = tmp_path / "images"
    data = tmp_path / "data"
    images.mkdir()
    monkeypatch.setattr(config, "IMAGES_DIR", str(images))
    monkeypatch.setattr(config, "THUMBNAILS_DIR", str(images / "thumbs"))
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "DATABASE_FILE", str(data / "app.sqlite3"))
    storage.close_database_connections()
    storage._db_initialized = False
    storage._dirs_initialized = False
    yield
    storage.close_database_connections()
    storage._db_initialized = False
    storage._dirs_initialized = False


def write_image(image_dir: Path, filename: str, data: bytes = PNG_BYTES):
    path = image_dir / filename
    path.write_bytes(data)
    return path


def insert_gallery_row(image_id: str, filename: str, *, byte_size: int, sha256: str):
    storage._ensure_database()
    with storage._connect() as conn:
        with storage._transaction(conn):
            conn.execute(
                """
                INSERT INTO gallery_entries (
                    id, prompt, size, filename, created_at, bytes, sha256
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (image_id, "prompt", "1024x1024", filename, "2026-06-05T00:00:00Z", byte_size, sha256),
            )


def test_r2_sync_state_filters_incremental_candidates(storage_runtime):
    insert_gallery_row("image-a", "a.png", byte_size=4, sha256="sha-a")

    assert storage.count_gallery_r2_sync_rows(key_prefix="gallery/") == 1
    assert [
        row["filename"]
        for row in storage.iter_gallery_r2_sync_rows(key_prefix="gallery/")
    ] == ["a.png"]

    storage.mark_gallery_r2_sync_state(
        [
            {
                "filename": "a.png",
                "sha256": "sha-a",
                "bytes": 4,
                "key": "gallery/a.png",
                "etag": "etag-a",
            }
        ]
    )

    assert storage.count_gallery_r2_sync_rows(key_prefix="gallery/") == 0
    assert list(storage.iter_gallery_r2_sync_rows(key_prefix="gallery/")) == []
    with storage._connect() as conn:
        state = conn.execute(
            "SELECT etag, last_remote_seen_at FROM r2_sync_state WHERE filename = ?",
            ("a.png",),
        ).fetchone()
    assert state["etag"] == "etag-a"
    assert state["last_remote_seen_at"]
    assert storage.count_gallery_r2_sync_rows(key_prefix="other/") == 1
    assert storage.count_gallery_r2_sync_rows(
        key_prefix="gallery/",
        full_reconcile=True,
    ) == 1

    with storage._connect() as conn:
        with storage._transaction(conn):
            conn.execute(
                """
                UPDATE gallery_entries
                SET sha256 = ?
                WHERE id = ?
                """,
                ("sha-b", "image-a"),
            )

    rows = list(storage.iter_gallery_r2_sync_rows(key_prefix="gallery/"))
    assert len(rows) == 1
    assert rows[0]["filename"] == "a.png"
    assert rows[0]["sha256"] == "sha-b"


def test_r2_sync_rows_support_start_after_checkpoint(storage_runtime):
    insert_gallery_row("image-a", "a.png", byte_size=4, sha256="sha-a")
    insert_gallery_row("image-b", "b.png", byte_size=4, sha256="sha-b")
    insert_gallery_row("image-c", "c.png", byte_size=4, sha256="sha-c")

    assert storage.count_gallery_r2_sync_rows(
        key_prefix="gallery/",
        full_reconcile=True,
        start_after_filename="a.png",
    ) == 2
    assert [
        row["filename"]
        for row in storage.iter_gallery_r2_sync_rows(
            key_prefix="gallery/",
            full_reconcile=True,
            start_after_filename="a.png",
        )
    ] == ["b.png", "c.png"]


def test_r2_health_probe_success_and_cleanup_warning():
    client = FakeS3Client(fail_delete=True)

    health = r2_sync.probe_r2_settings(
        r2_settings(),
        client_factory=lambda _effective: client,
    )

    assert health["status"] == "warning"
    assert [check["name"] for check in health["checks"]] == [
        "configuration",
        "head_bucket",
        "list_prefix",
        "write_probe",
        "delete_probe",
    ]
    assert client.put_objects[0].startswith("gallery/.r2-sync-probe-")
    assert client.deleted == client.put_objects


def test_r2_endpoint_rejects_ip_literal(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "")

    with pytest.raises(ValueError, match="must use a hostname"):
        normalize_r2_endpoint_url("https://127.0.0.1")


def test_r2_endpoint_rejects_non_r2_hostname_without_allowlist(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "")

    with pytest.raises(ValueError, match="R2_ENDPOINT_HOST_ALLOWLIST"):
        normalize_r2_endpoint_url("https://storage.example.com")


def test_r2_endpoint_allows_cloudflare_r2_hostname(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "")
    monkeypatch.setattr(
        "backend.app.core.validators.resolve_hostname",
        lambda hostname: (hostname, ["104.18.0.1"]),
    )

    assert (
        normalize_r2_endpoint_url("https://ACCOUNT.r2.cloudflarestorage.com/")
        == "https://account.r2.cloudflarestorage.com"
    )


def test_r2_endpoint_rejects_cloudflare_suffix_without_account_label(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "")

    with pytest.raises(ValueError, match="R2_ENDPOINT_HOST_ALLOWLIST"):
        normalize_r2_endpoint_url("https://r2.cloudflarestorage.com")


def test_r2_endpoint_allowlist_still_blocks_private_dns(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "storage.example.com")
    monkeypatch.setattr(
        "backend.app.core.validators.resolve_hostname",
        lambda hostname: (hostname, ["10.0.0.5"]),
    )

    with pytest.raises(ValueError, match="private/internal IP"):
        normalize_r2_endpoint_url("https://storage.example.com")


def test_r2_endpoint_allowlist_allows_public_custom_hostname(monkeypatch):
    monkeypatch.setattr(config, "R2_ENDPOINT_HOST_ALLOWLIST", "storage.example.com")
    monkeypatch.setattr(
        "backend.app.core.validators.resolve_hostname",
        lambda hostname: (hostname, ["203.0.113.10"]),
    )

    assert normalize_r2_endpoint_url("https://storage.example.com") == "https://storage.example.com"


def test_r2_sync_uploads_only_missing_and_leaves_bucket_only_keys(image_dir):
    write_image(image_dir, "a.png")
    write_image(image_dir, "b.png", b"bbbb")
    client = FakeS3Client(keys={"gallery/a.png", "gallery/bucket-only.png"})

    result = r2_sync.sync_gallery_to_r2(
        r2_settings(),
        [
            {"id": "a", "filename": "a.png", "bytes": len(PNG_BYTES), "sha256": "sha-a"},
            {"id": "b", "filename": "b.png", "bytes": 4, "sha256": "sha-b"},
        ],
        total_count=2,
        client_factory=lambda _effective: client,
    )

    assert result.uploaded_count == 1
    assert result.skipped_existing_count == 1
    assert result.missing_local_count == 0
    assert result.failed_count == 0
    assert client.keys == {"gallery/a.png", "gallery/b.png", "gallery/bucket-only.png"}
    assert client.uploaded[0][1] == "gallery/b.png"
    assert client.uploaded[0][2]["ContentType"] == "image/png"
    assert client.uploaded[0][2]["Metadata"] == {
        "gallery-id": "b",
        "sha256": "sha-b",
        "bytes": "4",
    }


def test_r2_sync_dry_run_counts_pending_without_uploading(image_dir):
    write_image(image_dir, "a.png")
    write_image(image_dir, "b.png", b"bbbb")
    client = FakeS3Client(keys={"gallery/a.png"})
    progress: list[dict] = []

    result = r2_sync.sync_gallery_to_r2(
        r2_settings(),
        [
            {"id": "a", "filename": "a.png", "bytes": len(PNG_BYTES), "sha256": "sha-a"},
            {"id": "b", "filename": "b.png", "bytes": 4, "sha256": "sha-b"},
        ],
        total_count=2,
        dry_run=True,
        progress_cb=progress.append,
        client_factory=lambda _effective: client,
    )

    assert result.compared_count == 2
    assert result.pending_upload_count == 1
    assert result.uploaded_count == 0
    assert result.skipped_existing_count == 1
    assert client.uploaded == []
    assert client.keys == {"gallery/a.png"}
    assert progress[-1]["stage"] == "completed"
    assert any(update.get("last_filename") == "b.png" for update in progress)


def test_r2_sync_skips_missing_local_files(image_dir):
    write_image(image_dir, "present.png")
    client = FakeS3Client()

    result = r2_sync.sync_gallery_to_r2(
        r2_settings(),
        [
            {"id": "present", "filename": "present.png", "bytes": len(PNG_BYTES)},
            {"id": "missing", "filename": "missing.png", "bytes": 10},
        ],
        total_count=2,
        client_factory=lambda _effective: client,
    )

    assert result.compared_count == 2
    assert result.uploaded_count == 1
    assert result.missing_local_count == 1
    assert client.keys == {"gallery/present.png"}


def test_r2_sync_upload_failure_raises_with_counts_preserved(image_dir):
    write_image(image_dir, "bad.png")
    client = FakeS3Client(fail_upload_keys={"gallery/bad.png"})

    with pytest.raises(r2_sync.R2SyncError) as exc_info:
        r2_sync.sync_gallery_to_r2(
            r2_settings(),
            [{"id": "bad", "filename": "bad.png", "bytes": len(PNG_BYTES)}],
            total_count=1,
            client_factory=lambda _effective: client,
        )

    result = exc_info.value.result
    assert result.compared_count == 1
    assert result.uploaded_count == 0
    assert result.failed_count == 1
    assert result.bytes_uploaded == 0
    assert "gallery/bad.png" not in client.keys
