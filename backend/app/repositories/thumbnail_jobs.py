"""Thumbnail queue persistence and thumbnail generation orchestration."""

from .db import *
from .coordination import acquire_background_slot, release_background_slot


def _attach_gallery_thumbnail_url(entry: dict[str, Any]) -> dict[str, Any]:
    if "image_url" not in entry:
        entry["image_url"] = image_url_for_filename(str(entry.get("filename") or ""))
    if "thumbnail_url" not in entry:
        entry["thumbnail_url"] = _thumbnail_url_for_filename(
            str(entry.get("filename") or "")
        )
    return entry


def _prepare_gallery_file(image_bytes: bytes, filename: str) -> _PreparedGalleryFile:
    image_temp_path, image_format, width, height = (
        _save_image_temp_with_metadata_unlocked(image_bytes, filename)
    )
    return _PreparedGalleryFile(
        filename=filename,
        image_temp_path=image_temp_path,
        image_format=image_format,
        image_width=width,
        image_height=height,
    )


def _cleanup_prepared_gallery_files(prepared_files: Iterable[_PreparedGalleryFile]):
    for prepared in prepared_files:
        prepared.image_temp_path.unlink(missing_ok=True)
        if prepared.thumbnail_temp_path:
            prepared.thumbnail_temp_path.unlink(missing_ok=True)


def _promote_prepared_images(prepared_files: Sequence[_PreparedGalleryFile]):
    for prepared in prepared_files:
        _promote_image_temp_unlocked(prepared.filename, prepared.image_temp_path)


def _promote_prepared_thumbnails(prepared_files: Sequence[_PreparedGalleryFile]):
    for prepared in prepared_files:
        if prepared.thumbnail_filename and prepared.thumbnail_temp_path:
            if _promote_thumbnail_temp_unlocked(
                prepared.thumbnail_filename,
                prepared.thumbnail_temp_path,
            ):
                _add_verified_thumbnail(prepared.thumbnail_filename)


@contextmanager
def _thumbnail_cpu_slot() -> Iterator[None]:
    owner = f"thumbnail-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4()}"
    slot_name: str | None = None
    try:
        while slot_name is None:
            now_dt = datetime.now(timezone.utc)
            slot_name = acquire_background_slot(
                name_prefix="thumbnail_cpu",
                owner=owner,
                slot_count=config.THUMBNAIL_CPU_CONCURRENCY,
                lease_expires_at=(
                    now_dt + timedelta(seconds=THUMBNAIL_CPU_SLOT_LEASE_SECONDS)
                ).isoformat(),
                now=now_dt.isoformat(),
            )
            if slot_name is None:
                time.sleep(0.05)
        yield
    finally:
        if slot_name is not None:
            release_background_slot(name=slot_name, owner=owner)


def _enqueue_thumbnail_job_on_conn(
    conn: sqlite3.Connection,
    filename: str,
    *,
    force: bool = False,
) -> bool:
    normalized = str(filename or "").strip()
    image_path = safe_image_path(normalized)
    if not normalized or not image_path or not image_path.is_file():
        return False

    now = utc_now()
    existing = conn.execute(
        """
        SELECT status, lease_expires_at
        FROM thumbnail_jobs
        WHERE filename = ?
        """,
        (normalized,),
    ).fetchone()
    if existing and not force:
        if existing["status"] == "success":
            return False
        if (
            existing["status"] == "running"
            and str(existing["lease_expires_at"] or "") > now
        ):
            return False

    conn.execute(
        """
        INSERT INTO thumbnail_jobs (
            filename,
            status,
            attempts,
            lease_owner,
            lease_expires_at,
            created_at,
            updated_at,
            error
        )
        VALUES (?, 'queued', 0, NULL, NULL, ?, ?, NULL)
        ON CONFLICT(filename) DO UPDATE SET
            status = 'queued',
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = excluded.updated_at,
            error = NULL
        """,
        (normalized, now, now),
    )
    return True


def enqueue_thumbnail_job(filename: str, *, force: bool = False) -> bool:
    _ensure_database()
    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return False
    with _connect() as conn:
        with _transaction(conn):
            return _enqueue_thumbnail_job_on_conn(conn, filename, force=force)


def get_pending_thumbnail_job_count() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM thumbnail_jobs
            WHERE status = 'queued'
               OR (status = 'running' AND lease_expires_at <= ?)
            """,
            (utc_now(),),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def claim_next_thumbnail_job(
    *,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    current_time = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT filename, status, attempts, lease_owner, lease_expires_at,
                    created_at, updated_at, error
                FROM thumbnail_jobs
                WHERE status = 'queued'
                   OR (status = 'running' AND lease_expires_at <= ?)
                ORDER BY created_at ASC, filename ASC
                LIMIT 1
                """,
                (current_time,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    updated_at = ?,
                    error = NULL
                WHERE filename = ?
                """,
                (owner, lease_expires_at, current_time, row["filename"]),
            )
            updated = conn.execute(
                """
                SELECT filename, status, attempts, lease_owner, lease_expires_at,
                    created_at, updated_at, error
                FROM thumbnail_jobs
                WHERE filename = ?
                """,
                (row["filename"],),
            ).fetchone()
    return dict(updated) if updated else None


def complete_thumbnail_job(filename: str, *, owner: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                "DELETE FROM thumbnail_jobs WHERE filename = ? AND lease_owner = ?",
                (filename, owner),
            )
            return cursor.rowcount > 0


def fail_thumbnail_job(
    filename: str,
    *,
    owner: str,
    error: str,
    max_attempts: int = THUMBNAIL_JOB_MAX_ATTEMPTS,
) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT attempts
                FROM thumbnail_jobs
                WHERE filename = ? AND lease_owner = ?
                """,
                (filename, owner),
            ).fetchone()
            if not row:
                return False
            next_status = (
                "error"
                if int(row["attempts"] or 0) >= max_attempts
                else "queued"
            )
            if next_status == "error":
                cursor = conn.execute(
                    "DELETE FROM thumbnail_jobs WHERE filename = ? AND lease_owner = ?",
                    (filename, owner),
                )
                return cursor.rowcount > 0
            conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?,
                    error = ?
                WHERE filename = ? AND lease_owner = ?
                """,
                (next_status, utc_now(), str(error or "")[:1000], filename, owner),
            )
            return True


def cleanup_auxiliary_state(*, now: datetime | None = None) -> dict[str, int]:
    _ensure_database()
    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(seconds=WORKER_METRIC_SNAPSHOT_TTL_SECONDS)).isoformat()
    with _connect() as conn:
        with _transaction(conn):
            thumbnail_cursor = conn.execute(
                """
                DELETE FROM thumbnail_jobs
                WHERE NOT EXISTS (
                    SELECT 1 FROM gallery_entries
                    WHERE gallery_entries.filename = thumbnail_jobs.filename
                )
                """
            )
            r2_cursor = conn.execute(
                """
                DELETE FROM r2_sync_state
                WHERE NOT EXISTS (
                    SELECT 1 FROM gallery_entries
                    WHERE gallery_entries.filename = r2_sync_state.filename
                )
                """
            )
            heartbeat_cursor = conn.execute(
                "DELETE FROM worker_heartbeats WHERE last_seen_at <= ?",
                (cutoff,),
            )
            snapshot_cursor = conn.execute(
                "DELETE FROM worker_metric_snapshots WHERE updated_at <= ?",
                (cutoff,),
            )
    return {
        "thumbnail_jobs": max(0, thumbnail_cursor.rowcount),
        "r2_sync_state": max(0, r2_cursor.rowcount),
        "worker_heartbeats": max(0, heartbeat_cursor.rowcount),
        "worker_metric_snapshots": max(0, snapshot_cursor.rowcount),
    }


def _dedupe_gallery_filename(
    filename: str,
    used_filenames: set[str],
    next_suffix: dict[tuple[str, str], int] | None = None,
) -> str:
    if filename not in used_filenames:
        return filename

    path_name = Path(filename)
    base = path_name.stem
    ext = path_name.suffix
    key = (base, ext)
    counter = (next_suffix or {}).get(key, 1)
    while True:
        candidate = f"{base}_{counter}{ext}"
        if candidate not in used_filenames:
            if next_suffix is not None:
                next_suffix[key] = counter + 1
            return candidate
        counter += 1


def _dedupe_import_entries_on_conn(
    conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
    prepared_files: list[_PreparedGalleryFile],
):
    used_filenames: set[str] = set()
    used_ids: set[str] = set()
    conflicts_possible = (
        conn.execute("SELECT 1 FROM gallery_entries LIMIT 1").fetchone() is not None
    )

    if conflicts_possible:
        incoming_filenames = [str(e["filename"]) for e in entries]
        incoming_ids = [str(e["id"]) for e in entries]
        for chunk in _iter_sqlite_in_chunks(incoming_filenames):
            placeholders_fn = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({placeholders_fn})
                """,
                tuple(chunk),
            ).fetchall()
            used_filenames.update(row["filename"] for row in rows if row["filename"])
        for chunk in _iter_sqlite_in_chunks(incoming_ids):
            placeholders_id = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id FROM gallery_entries WHERE id IN ({placeholders_id})",
                tuple(chunk),
            ).fetchall()
            used_ids.update(row["id"] for row in rows if row["id"])

    next_suffix: dict[tuple[str, str], int] = {}
    for entry, prepared in zip(entries, prepared_files):
        image_id = str(entry["id"])
        while image_id in used_ids:
            image_id = generate_image_id()
        entry["id"] = image_id
        used_ids.add(image_id)

        filename = str(entry["filename"])
        deduped_filename = _dedupe_gallery_filename(
            filename,
            used_filenames,
            next_suffix,
        )
        entry["filename"] = deduped_filename
        prepared.filename = deduped_filename
        used_filenames.add(deduped_filename)

        if deduped_filename != filename:
            entry.pop("thumbnail_filename", None)
            if prepared.thumbnail_temp_path:
                prepared.thumbnail_temp_path.unlink(missing_ok=True)
            prepared.thumbnail_filename = None
            prepared.thumbnail_temp_path = None


def ensure_thumbnail_for_image(filename: str) -> str | None:
    thumbnail_filename = _thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return None

    with _verified_thumbnails_lock:
        if thumbnail_filename in _verified_thumbnails:
            return thumbnail_filename

    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if thumbnail_path and thumbnail_path.is_file():
        _add_verified_thumbnail(thumbnail_filename)
        _set_thumbnail_filename_for_image(filename, thumbnail_filename)
        return thumbnail_filename

    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return None

    enqueue_thumbnail_job(filename, force=True)
    return None


def generate_thumbnail_for_image(filename: str) -> str | None:
    thumbnail_filename = _thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return None

    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if thumbnail_path and thumbnail_path.is_file():
        _add_verified_thumbnail(thumbnail_filename)
        _set_thumbnail_filename_for_image(filename, thumbnail_filename)
        return thumbnail_filename

    image_path = safe_image_path(filename)
    if not image_path or not image_path.is_file():
        return None

    for _ in range(3):
        if thumbnail_path and thumbnail_path.is_file():
            _add_verified_thumbnail(thumbnail_filename)
            _set_thumbnail_filename_for_image(filename, thumbnail_filename)
            return thumbnail_filename
        try:
            image_stat = image_path.stat()
        except OSError as e:
            logger.warning("Failed to stat image for thumbnail %s: %s", filename, e)
            return None

        with _thumbnail_cpu_slot():
            prepared_thumbnail = _create_thumbnail_temp_from_path_unlocked(image_path, filename)
        if not prepared_thumbnail:
            return None

        created_thumbnail, temp_path = prepared_thumbnail
        if thumbnail_path and thumbnail_path.is_file():
            temp_path.unlink(missing_ok=True)
            _add_verified_thumbnail(thumbnail_filename)
            return thumbnail_filename

        with _storage_lock:
            if thumbnail_path and thumbnail_path.is_file():
                temp_path.unlink(missing_ok=True)
                _add_verified_thumbnail(thumbnail_filename)
                return thumbnail_filename
            try:
                current_stat = image_path.stat()
            except OSError:
                temp_path.unlink(missing_ok=True)
                return None
            if (
                current_stat.st_mtime_ns != image_stat.st_mtime_ns
                or current_stat.st_size != image_stat.st_size
            ):
                temp_path.unlink(missing_ok=True)
                continue

            if _promote_thumbnail_temp_unlocked(created_thumbnail, temp_path):
                _set_thumbnail_filename_for_image(filename, created_thumbnail)
                _add_verified_thumbnail(created_thumbnail)
                return created_thumbnail
            return None

    return None


def _set_thumbnail_filename_for_image(filename: str, thumbnail_filename: str):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE gallery_entries
                SET thumbnail_filename = ?
                WHERE filename = ?
                """,
                (thumbnail_filename, filename),
            )



__all__ = [name for name in globals() if not name.startswith("__")]
