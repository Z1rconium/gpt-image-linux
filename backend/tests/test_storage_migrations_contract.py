from backend.tests.support.contract import *  # noqa: F403

def test_storage_connect_reuses_nested_sqlite_handle_and_closes_on_exit(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    closed_paths: list[str] = []
    real_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            closed_paths.append(config.DATABASE_FILE)
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(db_repo.sqlite3, "connect", tracked_connect)

    with db_repo._connect() as conn:
        first_conn = conn
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        with db_repo._connect() as nested_conn:
            assert nested_conn is first_conn
            assert nested_conn.execute("SELECT 1").fetchone()[0] == 1
        assert closed_paths == []

    assert closed_paths == [config.DATABASE_FILE]
    with pytest.raises(sqlite3.ProgrammingError):
        first_conn.execute("SELECT 1")

    with db_repo._connect() as conn:
        assert conn is not first_conn
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    assert closed_paths == [config.DATABASE_FILE, config.DATABASE_FILE]
def test_storage_enqueue_image_job_rejects_queue_full_atomically(tmp_path):
    _configure_runtime(tmp_path)

    image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "capacity-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "fills queue", "n": 2},
        image_units=2,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=1,
        max_queued_generate_jobs=1,
        max_pending_edit_source_bytes=1024 * 1024,
    )

    with pytest.raises(image_jobs_repo.ImageJobQueueFullError):
        image_jobs_repo.enqueue_image_job(
            parent_job={"job_id": "overflow-parent", "status": "queued"},
            operation="generation",
            request={"prompt": "overflow", "n": 1},
            image_units=1,
            api_preset_id="default",
            api_preset_name="Default",
            api_path="/v1/images/generations",
            max_active_generate_jobs=1,
            max_queued_generate_jobs=1,
            max_pending_edit_source_bytes=1024 * 1024,
        )

    assert image_jobs_repo.get_generate_job("overflow-parent") is None
    assert image_jobs_repo.count_pending_image_job_units() == (0, 2)


def test_storage_claim_prefers_expired_running_unit_over_queued_unit(tmp_path):
    _configure_runtime(tmp_path)

    _parent, units = image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "claim-parent", "status": "queued"},
        operation="generation",
        request={"prompt": "claim order", "n": 2},
        image_units=2,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/generations",
        max_active_generate_jobs=2,
        max_queued_generate_jobs=2,
        max_pending_edit_source_bytes=1024 * 1024,
    )

    first = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-a",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        running_limit=2,
    )
    assert first is not None
    assert first["unit_id"] == units[0]["unit_id"]

    image_jobs_repo.update_image_job_unit_progress(
        str(first["unit_id"]),
        stage="waiting_for_api",
        message="expired lease",
        claim_expires_at="2026-01-01T00:00:01+00:00",
    )

    reclaimed = image_jobs_repo.claim_next_image_job_unit(
        worker_id="worker-b",
        lease_expires_at="2099-01-01T00:00:00+00:00",
        now="2026-01-01T00:00:02+00:00",
        running_limit=2,
    )

    assert reclaimed is not None
    assert reclaimed["unit_id"] == first["unit_id"]
    assert reclaimed["claimed_by"] == "worker-b"


def test_storage_sse_slots_enforce_global_and_per_ip_leases(tmp_path):
    _configure_runtime(tmp_path)
    now = "2099-01-01T00:00:00+00:00"
    expires = "2099-01-01T00:01:00+00:00"

    assert coordination_repo.acquire_sse_slot(
        client_ip="203.0.113.10",
        connection_id="sse-1",
        lease_expires_at=expires,
        max_global=2,
        max_per_ip=1,
        now=now,
    ) == (True, "acquired")
    assert coordination_repo.acquire_sse_slot(
        client_ip="203.0.113.10",
        connection_id="sse-2",
        lease_expires_at=expires,
        max_global=2,
        max_per_ip=1,
        now=now,
    ) == (False, "per_ip_limit")
    assert coordination_repo.acquire_sse_slot(
        client_ip="203.0.113.11",
        connection_id="sse-3",
        lease_expires_at=expires,
        max_global=2,
        max_per_ip=2,
        now=now,
    ) == (True, "acquired")
    assert coordination_repo.acquire_sse_slot(
        client_ip="203.0.113.12",
        connection_id="sse-4",
        lease_expires_at=expires,
        max_global=2,
        max_per_ip=2,
        now=now,
    ) == (False, "global_limit")

    assert coordination_repo.count_active_sse_slots() == 2
    assert coordination_repo.refresh_sse_slot(
        connection_id="sse-1",
        lease_expires_at="2099-01-01T00:02:00+00:00",
        now=now,
    )
    assert coordination_repo.release_sse_slot("sse-3", now=now)
    assert coordination_repo.count_active_sse_slots() == 1

    assert coordination_repo.acquire_sse_slot(
        client_ip="203.0.113.12",
        connection_id="sse-4",
        lease_expires_at="2099-01-01T00:04:00+00:00",
        max_global=2,
        max_per_ip=2,
        now="2099-01-01T00:03:00+00:00",
    ) == (True, "acquired")


def test_storage_background_lease_completion_blocks_startup_storm(tmp_path):
    _configure_runtime(tmp_path)

    assert coordination_repo.acquire_background_lease(
        name="startup_maintenance",
        owner="worker-a",
        lease_expires_at="2026-01-01T00:01:00+00:00",
        now="2026-01-01T00:00:00+00:00",
        completed_ttl_seconds=600,
    )
    assert not coordination_repo.acquire_background_lease(
        name="startup_maintenance",
        owner="worker-b",
        lease_expires_at="2026-01-01T00:01:00+00:00",
        now="2026-01-01T00:00:10+00:00",
        completed_ttl_seconds=600,
    )
    assert coordination_repo.complete_background_lease(
        name="startup_maintenance",
        owner="worker-a",
        now="2026-01-01T00:00:20+00:00",
    )
    assert not coordination_repo.acquire_background_lease(
        name="startup_maintenance",
        owner="worker-b",
        lease_expires_at="2026-01-01T00:02:00+00:00",
        now="2026-01-01T00:00:30+00:00",
        completed_ttl_seconds=600,
    )
    assert coordination_repo.acquire_background_lease(
        name="startup_maintenance",
        owner="worker-b",
        lease_expires_at="2026-01-01T00:12:00+00:00",
        now="2026-01-01T00:11:00+00:00",
        completed_ttl_seconds=600,
    )


def test_storage_edit_source_reservation_is_global_and_released_on_terminal(tmp_path):
    _configure_runtime(tmp_path)
    source_bytes = 600 * 1024
    max_pending_bytes = 1024 * 1024

    image_jobs_repo.enqueue_image_job(
        parent_job={"job_id": "edit-parent", "status": "queued"},
        operation="edit",
        request={"prompt": "edit", "n": 1},
        image_units=1,
        api_preset_id="default",
        api_preset_name="Default",
        api_path="/v1/images/edits",
        pending_edit_source_bytes=source_bytes,
        max_active_generate_jobs=1,
        max_queued_generate_jobs=20,
        max_pending_edit_source_bytes=max_pending_bytes,
    )

    with pytest.raises(image_jobs_repo.EditSourceQueueFullError):
        image_jobs_repo.enqueue_image_job(
            parent_job={"job_id": "edit-overflow", "status": "queued"},
            operation="edit",
            request={"prompt": "overflow", "n": 1},
            image_units=1,
            api_preset_id="default",
            api_preset_name="Default",
            api_path="/v1/images/edits",
            pending_edit_source_bytes=source_bytes,
            max_active_generate_jobs=1,
            max_queued_generate_jobs=20,
            max_pending_edit_source_bytes=max_pending_bytes,
        )

    assert image_jobs_repo.get_pending_edit_source_bytes() == source_bytes
    assert image_jobs_repo.get_generate_job("edit-overflow") is None

    image_jobs_repo.upsert_generate_job({"job_id": "edit-parent", "status": "cancelled"})

    assert image_jobs_repo.get_pending_edit_source_bytes() == 0


def test_schema_migrations_are_recorded_and_idempotent(tmp_path):
    _configure_runtime(tmp_path)
    db_repo.verify_storage_writable()
    with db_repo._connect() as conn:
        versions = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        gallery_version = conn.execute(
            "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
        ).fetchone()
        anchor_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'gallery_page_anchors'"
        ).fetchone()
    assert [row["version"] for row in versions] == list(range(1, len(db_repo.SCHEMA_MIGRATIONS) + 1))
    assert gallery_version["value"] == 0
    assert anchor_table is not None

    db_repo.close_database_connections()
    db_repo.verify_storage_writable()
    with db_repo._connect() as conn:
        repeated_versions = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [(row["version"], row["name"]) for row in repeated_versions] == [
        (row["version"], row["name"]) for row in versions
    ]


def test_gallery_and_access_migrations_own_only_their_schema():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        db_repo._migration_gallery_page_anchors(conn)
        tables_after_gallery = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        gallery_version = conn.execute(
            "SELECT value FROM gallery_meta WHERE key = 'gallery_version'"
        ).fetchone()

        assert {"gallery_meta", "gallery_page_anchors"}.issubset(tables_after_gallery)
        assert "access_failures" not in tables_after_gallery
        assert gallery_version["value"] == 0

    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        db_repo._migration_access_failures(conn)
        tables_after_access = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        assert "access_failures" in tables_after_access
        assert "gallery_meta" not in tables_after_access
        assert "gallery_page_anchors" not in tables_after_access


def test_generate_job_count_migration_preserves_legacy_rows_as_null():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE generate_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO generate_jobs (job_id, status, created_at, updated_at)
            VALUES ('legacy-job', 'success', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z')
            """
        )

        db_repo._migration_generate_job_counts(conn)
        columns = db_repo._table_columns(conn, "generate_jobs")
        row = conn.execute(
            """
            SELECT completed_count, success_count, failure_count
            FROM generate_jobs
            WHERE job_id = 'legacy-job'
            """
        ).fetchone()

    assert {"completed_count", "success_count", "failure_count"}.issubset(columns)
    assert row["completed_count"] is None
    assert row["success_count"] is None
    assert row["failure_count"] is None


def test_background_column_migration_adds_column_idempotently():
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE gallery_entries (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                size TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE generate_jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        db_repo._migration_background_column(conn)
        assert "background" in db_repo._table_columns(conn, "gallery_entries")
        assert "background" in db_repo._table_columns(conn, "generate_jobs")

        # Calling again should be idempotent without error
        db_repo._migration_background_column(conn)
        assert "background" in db_repo._table_columns(conn, "gallery_entries")
        assert "background" in db_repo._table_columns(conn, "generate_jobs")


def test_schema_migrations_upgrade_legacy_gallery_schema(tmp_path):
    _configure_runtime(tmp_path)
    db_path = Path(config.DATABASE_FILE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE gallery_entries (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                size TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO gallery_entries (id, prompt, size, filename, created_at)
            VALUES ('legacy-1', 'legacy prompt', '1024x1024', 'legacy-1.png', '2026-01-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            CREATE TABLE api_presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                api_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                api_path TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    db_repo.verify_storage_writable()
    with db_repo._connect() as conn:
        versions = [
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        gallery_columns = db_repo._table_columns(conn, "gallery_entries")
        preset_columns = db_repo._table_columns(conn, "api_presets")
        row = conn.execute(
            "SELECT favorite, sort_seq, bytes, thumbnail_filename, completed_at, sha256 FROM gallery_entries WHERE id = 'legacy-1'"
        ).fetchone()

    assert versions == list(range(1, len(db_repo.SCHEMA_MIGRATIONS) + 1))
    assert {"favorite", "sort_seq", "bytes", "thumbnail_filename", "completed_at", "sha256", "background"}.issubset(gallery_columns)
    assert {"default_model", "default_response_format"}.issubset(preset_columns)
    assert row["favorite"] == 0
    assert row["sort_seq"] is not None
