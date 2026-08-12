"""Gallery image insertion, updates, deletion, and file reconciliation."""

from ..db import *
from ..thumbnail_jobs import *
from .queries import *
from ...services.blocking import run_db_operation, run_image_operation


def _insert_gallery_entries_on_conn(
    conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
):
    normalized_entries = [
        normalized
        for entry in entries
        if (normalized := _normalize_gallery_entry(entry)) is not None
    ]
    if not normalized_entries:
        return

    incoming_ids = [entry["id"] for entry in normalized_entries]
    existing_by_id: dict[str, sqlite3.Row] = {}
    for chunk in _iter_sqlite_in_chunks(incoming_ids):
        placeholders = ", ".join("?" for _ in chunk)
        existing_rows = conn.execute(
            f"""
            SELECT id, model, api_preset_name, size
            FROM gallery_entries
            WHERE id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        existing_by_id.update({row["id"]: row for row in existing_rows})

    row = conn.execute(
        "SELECT COALESCE(MAX(sort_seq), 0) FROM gallery_entries"
    ).fetchone()
    next_seq = int(row[0]) + 1 if row else 1
    for entry in normalized_entries:
        if entry.get("sort_seq") is None:
            entry["sort_seq"] = next_seq
            next_seq += 1

    columns_sql = ", ".join(GALLERY_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_COLUMNS)
    updates_sql = ", ".join(
        f"{column} = excluded.{column}"
        for column in GALLERY_COLUMNS
        if column != "id"
    )
    conn.executemany(
        f"""
        INSERT INTO gallery_entries ({columns_sql})
        VALUES ({placeholders_sql})
        ON CONFLICT(id) DO UPDATE SET {updates_sql}
        """,
        [_gallery_row_values(entry) for entry in normalized_entries],
    )
    filter_option_deltas: dict[tuple[str, str], int] = {}
    for entry in normalized_entries:
        existing = existing_by_id.get(entry["id"])
        if existing is not None:
            _add_gallery_filter_option_deltas(filter_option_deltas, existing, -1)
        _add_gallery_filter_option_deltas(filter_option_deltas, entry, 1)
        _enqueue_thumbnail_job_on_conn(conn, str(entry.get("filename") or ""))
    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)

def _build_gallery_entry(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": utc_now(),
    }
    if image_bytes:
        entry.update(_image_dimension_metadata(image_bytes))
    if image_bytes is not None:
        entry["bytes"] = len(image_bytes)
        entry["sha256"] = hashlib.sha256(image_bytes).hexdigest()
    if metadata:
        entry.update(
            {
                key: value
                for key, value in metadata.items()
                if key in GALLERY_COLUMNS
                and key not in REQUIRED_GALLERY_COLUMNS
                and value is not None
            }
        )
    return entry


def _save_images_and_insert_gallery_entries(
    entries_data: list[tuple[bytes, str]],
    gallery_entries: list[dict[str, Any]],
):
    _ensure_database()
    prepared_files: list[_PreparedGalleryFile] = []
    try:
        for index, (image_bytes, filename) in enumerate(entries_data):
            prepared = _prepare_gallery_file(image_bytes, filename)
            prepared_files.append(prepared)
            if prepared.thumbnail_filename and index < len(gallery_entries):
                gallery_entries[index]["thumbnail_filename"] = (
                    prepared.thumbnail_filename
                )

        with _gallery_file_write_lock:
            with _storage_lock:
                _promote_prepared_images(prepared_files)
                with _connect() as conn:
                    with _transaction(conn):
                        with observe_job_stage("db_insert"):
                            _insert_gallery_entries_on_conn(conn, gallery_entries)
                _promote_prepared_thumbnails(prepared_files)
    except BaseException:
        _cleanup_prepared_gallery_files(prepared_files)
        raise


def import_gallery_entries(
    entries_data: Iterable[tuple[bytes, dict[str, Any]]],
) -> int:
    total_imported = 0
    batch: list[tuple[bytes, dict[str, Any]]] = []
    for item in entries_data:
        batch.append(item)
        if len(batch) >= GALLERY_IMPORT_BATCH_SIZE:
            total_imported += _import_gallery_entries_batch(batch)
            batch = []
    if batch:
        total_imported += _import_gallery_entries_batch(batch)
    return total_imported


def _import_gallery_entries_batch(
    entries_data: Iterable[tuple[bytes, dict[str, Any]]],
) -> int:
    _ensure_database()
    prepared_files: list[_PreparedGalleryFile] = []
    normalized_entries: list[dict[str, Any]] = []
    try:
        for image_bytes, entry in entries_data:
            normalized = _normalize_gallery_entry(entry)
            if not normalized:
                continue
            normalized["bytes"] = len(image_bytes)
            normalized["sha256"] = hashlib.sha256(image_bytes).hexdigest()
            normalized.pop("thumbnail_filename", None)

            prepared = _prepare_gallery_file(image_bytes, normalized["filename"])
            prepared_files.append(prepared)
            if prepared.thumbnail_filename:
                normalized["thumbnail_filename"] = prepared.thumbnail_filename
            normalized_entries.append(normalized)

        if not normalized_entries:
            return 0

        with _gallery_file_write_lock:
            with _connect() as conn:
                _dedupe_import_entries_on_conn(conn, normalized_entries, prepared_files)

            _promote_prepared_images(prepared_files)

            with _storage_lock:
                with _connect() as conn:
                    with _transaction(conn):
                        with observe_job_stage("db_insert"):
                            _insert_gallery_entries_on_conn(conn, normalized_entries)

            _promote_prepared_thumbnails(prepared_files)
        return len(normalized_entries)
    except BaseException:
        _cleanup_prepared_gallery_files(prepared_files)
        raise


async def add_to_gallery_async(
    image_bytes: bytes,
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> GalleryEntry:
    def prepare() -> tuple[_PreparedGalleryFile, dict[str, Any]]:
        with observe_job_stage("validate"):
            prepared = _prepare_gallery_file(image_bytes, filename)
        entry = _build_gallery_entry(
            image_id=image_id,
            prompt=prompt,
            size=size,
            filename=filename,
            metadata=metadata,
            image_bytes=None,
        )
        entry["bytes"] = len(image_bytes)
        entry["sha256"] = hashlib.sha256(image_bytes).hexdigest()
        if prepared.image_width and prepared.image_height:
            entry["image_width"] = prepared.image_width
            entry["image_height"] = prepared.image_height
        if prepared.image_format:
            entry["output_format"] = prepared.image_format
        return prepared, entry

    prepared, entry = await run_image_operation(
        prepare,
        metric_name="prepare_gallery_image",
    )

    def commit() -> None:
        _ensure_database()
        with _gallery_file_write_lock:
            with _storage_lock:
                with _connect() as conn:
                    with _transaction(conn):
                        _promote_prepared_images([prepared])
                        with observe_job_stage("db_insert"):
                            _insert_gallery_entries_on_conn(conn, [entry])

    try:
        await run_db_operation(commit, metric_name="insert_gallery_image")
    except BaseException:
        await run_image_operation(
            _cleanup_prepared_gallery_files,
            [prepared],
            metric_name="cleanup_gallery_image",
        )
        raise
    return GalleryEntry(**_attach_gallery_thumbnail_url(entry))


def _stat_image_bytes(filename: str) -> int | None:
    path = safe_image_path(filename)
    if not path:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _backfill_gallery_bytes_from_known_rows_on_conn(conn: sqlite3.Connection) -> int:
    before_changes = conn.total_changes
    with _transaction(conn):
        conn.execute(
            """
            UPDATE gallery_entries
            SET bytes = (
                SELECT MAX(known.bytes)
                FROM gallery_entries AS known
                WHERE known.filename = gallery_entries.filename
                  AND known.bytes IS NOT NULL
            )
            WHERE bytes IS NULL
              AND filename IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM gallery_entries AS known
                  WHERE known.filename = gallery_entries.filename
                    AND known.bytes IS NOT NULL
              )
            """
        )
    return conn.total_changes - before_changes


def _backfill_gallery_bytes_from_filenames() -> int:
    total_updated = 0
    batch_size = 200
    last_filename = ""
    while True:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT filename
                FROM gallery_entries
                WHERE filename IS NOT NULL
                  AND TRIM(filename) != ''
                  AND bytes IS NULL
                  AND filename > ?
                GROUP BY filename
                ORDER BY filename ASC
                LIMIT ?
                """,
                (last_filename, batch_size),
            ).fetchall()
        if not rows:
            break

        backfills: list[tuple[int, str]] = []
        for row in rows:
            stored_filename = str(row["filename"] or "")
            filename = stored_filename.strip()
            if not filename:
                continue
            last_filename = stored_filename
            size = _stat_image_bytes(filename)
            if size is None:
                continue
            backfills.append((size, stored_filename))

        if backfills:
            with _connect() as conn:
                before_changes = conn.total_changes
                with _transaction(conn):
                    conn.executemany(
                        """
                        UPDATE gallery_entries
                        SET bytes = ?
                        WHERE filename = ? AND bytes IS NULL
                        """,
                        backfills,
                    )
                total_updated += conn.total_changes - before_changes

        if len(rows) < batch_size:
            break

    return total_updated


def backfill_missing_gallery_bytes() -> int:
    """Backfill missing gallery byte sizes outside the gallery request path."""
    _ensure_database()
    with _connect() as conn:
        updated = _backfill_gallery_bytes_from_known_rows_on_conn(conn)
    updated += _backfill_gallery_bytes_from_filenames()
    if updated:
        _invalidate_gallery_total_bytes_cache()
    return updated


def add_to_gallery_sync(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> GalleryEntry:
    """Synchronous gallery insert — used only in tests."""
    entry = _build_gallery_entry(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata=metadata,
        image_bytes=image_bytes,
    )
    if image_bytes is not None:
        _save_images_and_insert_gallery_entries([(image_bytes, filename)], [entry])
    else:
        _ensure_database()
        with _connect() as conn:
            with _transaction(conn):
                _insert_gallery_entries_on_conn(conn, [entry])
    return GalleryEntry(**_attach_gallery_thumbnail_url(entry))


def update_gallery_entry(image_id: str, updates: dict[str, Any]) -> GalleryEntry | None:
    allowed_updates = {
        key: _normalize_gallery_favorite(value) if key == "favorite" else value
        for key, value in updates.items()
        if key in GALLERY_COLUMNS and key != "id" and value is not None
    }

    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                WHERE id = ?
                """,
                (image_id,),
            ).fetchone()
            if not row:
                return None

            if allowed_updates:
                previous_filter_values = {
                    "model": row["model"],
                    "api_preset_name": row["api_preset_name"],
                    "size": row["size"],
                }
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"UPDATE gallery_entries SET {assignments} WHERE id = ?",
                    (*allowed_updates.values(), image_id),
                )
                if allowed_updates.keys() & GALLERY_PAGE_ANCHOR_INVALIDATING_UPDATE_FIELDS:
                    _invalidate_gallery_query_caches_on_conn(conn)
                elif "bytes" in allowed_updates:
                    _invalidate_gallery_total_bytes_cache()
                row = conn.execute(
                    f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    WHERE id = ?
                    """,
                    (image_id,),
                ).fetchone()
                if allowed_updates.keys() & {"model", "api_preset_name", "size"}:
                    _increment_gallery_filter_options_on_conn(
                        conn,
                        previous_filter_values,
                        -1,
                    )
                    _increment_gallery_filter_options_on_conn(conn, row, 1)

    with _connect() as conn:
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, [row])
    return GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))


def update_gallery_entries_favorite(image_ids: list[str], favorite: bool) -> int:
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return 0

    with _connect() as conn:
        with _transaction(conn):
            found_ids: list[str] = []
            for chunk in _iter_sqlite_in_chunks(unique_ids):
                select_placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id FROM gallery_entries WHERE id IN ({select_placeholders})",
                    tuple(chunk),
                ).fetchall()
                found_ids.extend(str(row["id"]) for row in rows if row["id"])

            if not found_ids:
                return 0

            normalized_favorite = _normalize_gallery_favorite(favorite)
            for chunk in _iter_sqlite_in_chunks(found_ids):
                update_placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"UPDATE gallery_entries SET favorite = ? WHERE id IN ({update_placeholders})",
                    (normalized_favorite, *chunk),
                )
            _invalidate_gallery_query_caches_on_conn(conn)
            return len(found_ids)


def _update_gallery_entries_favorite_by_where_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    normalized_favorite: int,
) -> tuple[int, int]:
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
        tuple(params),
    ).fetchone()
    matched_count = int(count_row[0] or 0) if count_row else 0
    if matched_count <= 0:
        return 0, 0

    update_where_sql = _combine_gallery_where(where_sql, "favorite != ?")
    conn.execute(
        f"""
        UPDATE gallery_entries
        SET favorite = ?
        {update_where_sql}
        """,
        (normalized_favorite, *params, normalized_favorite),
    )
    row = conn.execute("SELECT changes()").fetchone()
    updated_count = int(row[0] or 0) if row else 0
    return matched_count, updated_count


def update_gallery_entries_favorite_by_filters(
    filters: dict[str, Any] | None,
    favorite: bool,
    *,
    batch_size: int = 500,
) -> int:
    _ensure_database()
    normalized_favorite = _normalize_gallery_favorite(favorite)
    where_sql, params = _build_gallery_filter_where(filters)

    with _connect() as conn:
        with _transaction(conn):
            matched_count, updated_count = _update_gallery_entries_favorite_by_where_on_conn(
                conn,
                where_sql,
                params,
                normalized_favorite,
            )
            if updated_count:
                _invalidate_gallery_query_caches_on_conn(conn)

    return matched_count

def sync_gallery_with_image_files() -> int:
    _ensure_database()
    with _storage_lock:
        image_filenames = _scan_image_files()
        removed_count = 0
        with _connect() as conn:
            with _transaction(conn):
                last_id = ""
                filter_option_deltas: dict[tuple[str, str], int] = {}
                while True:
                    rows = conn.execute(
                        """
                        SELECT id, filename, model, api_preset_name, size
                        FROM gallery_entries
                        WHERE id > ?
                        ORDER BY id
                        LIMIT ?
                        """,
                        (last_id, GALLERY_SYNC_BATCH_SIZE),
                    ).fetchall()
                    if not rows:
                        break

                    last_id = str(rows[-1]["id"])
                    stale_ids = [
                        row["id"]
                        for row in rows
                        if row["filename"] and row["filename"] not in image_filenames
                    ]
                    if not stale_ids:
                        continue

                    conn.executemany(
                        "DELETE FROM gallery_entries WHERE id = ?",
                        [(entry_id,) for entry_id in stale_ids],
                    )
                    for row in rows:
                        if row["id"] in stale_ids:
                            _add_gallery_filter_option_deltas(
                                filter_option_deltas,
                                row,
                                -1,
                            )
                    removed_count += len(stale_ids)

                if removed_count:
                    _apply_gallery_filter_option_deltas_on_conn(
                        conn,
                        filter_option_deltas,
                    )
                    _invalidate_gallery_query_caches_on_conn(conn)
                    _clear_verified_thumbnails()
                return removed_count


def _delete_gallery_entries_by_ids(
    conn: sqlite3.Connection,
    image_ids: Sequence[str],
) -> tuple[list[str], set[str]]:
    """Delete gallery entries and return (removed_ids, filenames_to_delete).

    File deletion is NOT performed here — caller handles it after commit.
    """
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return [], set()

    rows: list[sqlite3.Row] = []
    for chunk in _iter_sqlite_in_chunks(unique_ids):
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                f"""
                SELECT id, filename, model, api_preset_name, size
                FROM gallery_entries
                WHERE id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
        )
    if not rows:
        return [], set()

    removed_ids = [row["id"] for row in rows]
    removed_filenames = {row["filename"] for row in rows if row["filename"]}
    for chunk in _iter_sqlite_in_chunks(removed_ids):
        delete_placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"DELETE FROM gallery_entries WHERE id IN ({delete_placeholders})",
            tuple(chunk),
        )
    filter_option_deltas: dict[tuple[str, str], int] = {}
    for row in rows:
        _add_gallery_filter_option_deltas(filter_option_deltas, row, -1)
    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)

    remaining_filenames: set[str] = set()
    if removed_filenames:
        for chunk in _iter_sqlite_in_chunks(removed_filenames):
            filename_placeholders = ", ".join("?" for _ in chunk)
            remaining_rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({filename_placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            remaining_filenames.update(
                row["filename"] for row in remaining_rows if row["filename"]
            )

    filenames_to_delete = removed_filenames - remaining_filenames
    _delete_auxiliary_rows_for_filenames_on_conn(conn, filenames_to_delete)
    return removed_ids, filenames_to_delete


def _delete_gallery_entries_by_filters(
    conn: sqlite3.Connection,
    filters: dict[str, Any] | None,
    *,
    batch_size: int = 500,
) -> tuple[int, set[str]]:
    where_sql, params = _build_gallery_filter_where(filters)
    normalized_batch_size = max(1, int(batch_size or 1))
    last_sort_seq: int | None = None
    last_id: str | None = None
    removed_count = 0
    removed_filenames: set[str] = set()
    filter_option_deltas: dict[tuple[str, str], int] = {}

    while True:
        rows = _get_gallery_row_batch_after_cursor_on_conn(
            conn,
            where_sql,
            params,
            last_sort_seq=last_sort_seq,
            last_id=last_id,
            limit=normalized_batch_size,
            columns=("id", "filename", "model", "api_preset_name", "size", "sort_seq"),
        )
        if not rows:
            break

        ids = [str(row["id"]) for row in rows if row["id"]]
        removed_filenames.update(str(row["filename"]) for row in rows if row["filename"])
        for chunk in _iter_sqlite_in_chunks(ids):
            placeholders = ", ".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM gallery_entries WHERE id IN ({placeholders})",
                tuple(chunk),
            )
        for row in rows:
            _add_gallery_filter_option_deltas(filter_option_deltas, row, -1)
        removed_count += len(ids)

        if len(rows) < normalized_batch_size:
            break
        last_row = rows[-1]
        last_sort_seq = int(last_row["sort_seq"] or 0)
        last_id = str(last_row["id"])

    if not removed_count:
        return 0, set()

    _apply_gallery_filter_option_deltas_on_conn(conn, filter_option_deltas)
    _invalidate_gallery_query_caches_on_conn(conn)

    remaining_filenames: set[str] = set()
    if removed_filenames:
        for chunk in _iter_sqlite_in_chunks(removed_filenames):
            filename_placeholders = ", ".join("?" for _ in chunk)
            remaining_rows = conn.execute(
                f"""
                SELECT DISTINCT filename
                FROM gallery_entries
                WHERE filename IN ({filename_placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            remaining_filenames.update(
                row["filename"] for row in remaining_rows if row["filename"]
            )

    filenames_to_delete = removed_filenames - remaining_filenames
    _delete_auxiliary_rows_for_filenames_on_conn(conn, filenames_to_delete)
    return removed_count, filenames_to_delete


def _delete_auxiliary_rows_for_filenames_on_conn(
    conn: sqlite3.Connection,
    filenames: Iterable[str],
) -> None:
    for chunk in _iter_sqlite_in_chunks(filenames):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"DELETE FROM thumbnail_jobs WHERE filename IN ({placeholders})",
            tuple(chunk),
        )
        conn.execute(
            f"DELETE FROM r2_sync_state WHERE filename IN ({placeholders})",
            tuple(chunk),
        )


def delete_gallery_image(image_id: str) -> tuple[bool, int]:
    deleted_entries, deleted_files = delete_gallery_images([image_id])
    return deleted_entries > 0, deleted_files


def _delete_gallery_files_after_commit(filenames: Iterable[str]) -> int:
    deleted_count = 0
    for filename in filenames:
        try:
            if _delete_image_unlocked(filename):
                deleted_count += 1
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete image file %s: %s", filename, e)
        try:
            _delete_thumbnail_unlocked(filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete thumbnail for %s: %s", filename, e)
        thumbnail_filename = _thumbnail_filename_for_image(filename)
        if thumbnail_filename:
            _remove_verified_thumbnail(thumbnail_filename)
    return deleted_count


def delete_gallery_images(image_ids: Sequence[str]) -> tuple[int, int]:
    _ensure_database()
    if not image_ids:
        return 0, 0

    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                removed_ids, filenames_to_delete = _delete_gallery_entries_by_ids(conn, image_ids)

    deleted_count = _delete_gallery_files_after_commit(filenames_to_delete)
    return len(removed_ids), deleted_count


def delete_gallery_images_by_filters(
    filters: dict[str, Any] | None,
    *,
    batch_size: int = 500,
) -> tuple[int, int]:
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                removed_count, filenames_to_delete = _delete_gallery_entries_by_filters(
                    conn,
                    filters,
                    batch_size=batch_size,
                )

    deleted_count = _delete_gallery_files_after_commit(filenames_to_delete)
    return removed_count, deleted_count


def _is_gallery_filename_referenced_on_conn(
    conn: sqlite3.Connection,
    filename: str,
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM gallery_entries WHERE filename = ? LIMIT 1",
        (filename,),
    ).fetchone()
    return row is not None


def is_gallery_filename_referenced(filename: str) -> bool:
    _ensure_database()
    normalized = str(filename or "").strip()
    if not normalized or not safe_image_path(normalized):
        return False
    with _connect() as conn:
        return _is_gallery_filename_referenced_on_conn(conn, normalized)


def _delete_gallery_file_if_unreferenced(filename: str) -> bool:
    with _storage_lock:
        with _connect() as conn:
            if _is_gallery_filename_referenced_on_conn(conn, filename):
                return False

        deleted = False
        try:
            deleted = _delete_image_unlocked(filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery image file %s: %s", filename, e)

        try:
            _delete_thumbnail_unlocked(filename)
            thumbnail_filename = _thumbnail_filename_for_image(filename)
            if thumbnail_filename:
                _remove_verified_thumbnail(thumbnail_filename)
        except OSError as e:
            metrics.increment("gallery.orphan_cleanup_pending")
            logger.warning("Failed to delete gallery thumbnail for %s: %s", filename, e)

        return deleted


def _file_older_than(path: Path, cutoff_epoch_seconds: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff_epoch_seconds
    except OSError:
        return False


def cleanup_orphan_gallery_files(
    *,
    ttl_seconds: int = GALLERY_ORPHAN_FILE_TTL_SECONDS,
    batch_size: int = GALLERY_ORPHAN_GC_BATCH_SIZE,
) -> dict[str, int]:
    """Delete unreferenced image and thumbnail files after a short TTL."""
    _ensure_database()
    cutoff = time.time() - max(0, int(ttl_seconds))
    limit = max(1, int(batch_size))
    removed_images = 0
    removed_thumbnails = 0
    failed = 0
    scanned = 0

    with _storage_lock:
        with _connect() as conn:
            referenced_filenames = set(_get_all_filenames_on_conn(conn))
        referenced_thumbnails = {
            thumbnail
            for filename in referenced_filenames
            if (thumbnail := _thumbnail_filename_for_image(filename))
        }

        images_dir = Path(config.IMAGES_DIR)
        if images_dir.exists():
            for path in images_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() not in IMAGE_FILE_EXTENSIONS:
                    continue
                scanned += 1
                filename = path.name
                if filename in referenced_filenames or not _file_older_than(path, cutoff):
                    continue
                try:
                    if _delete_image_unlocked(filename):
                        removed_images += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery image %s: %s", filename, e)

        thumbnails_dir = Path(config.THUMBNAILS_DIR)
        same_dir_as_images = False
        try:
            same_dir_as_images = thumbnails_dir.resolve() == images_dir.resolve()
        except OSError:
            same_dir_as_images = False

        if thumbnails_dir.exists() and scanned < limit:
            protected_thumbnail_names = set(referenced_thumbnails)
            if same_dir_as_images:
                protected_thumbnail_names.update(referenced_filenames)
            for path in thumbnails_dir.iterdir():
                if scanned >= limit:
                    break
                if not path.is_file() or path.suffix.lower() != THUMBNAIL_EXTENSION:
                    continue
                scanned += 1
                thumbnail_filename = path.name
                if (
                    thumbnail_filename in protected_thumbnail_names
                    or not safe_thumbnail_path(thumbnail_filename)
                    or not _file_older_than(path, cutoff)
                ):
                    continue
                try:
                    path.unlink()
                    _remove_verified_thumbnail(thumbnail_filename)
                    removed_thumbnails += 1
                except OSError as e:
                    failed += 1
                    metrics.increment("gallery.orphan_cleanup_pending")
                    logger.warning("Failed to GC orphan gallery thumbnail %s: %s", thumbnail_filename, e)

    if removed_images or removed_thumbnails or failed:
        logger.info(
            "Gallery file GC scanned=%d removed_images=%d removed_thumbnails=%d failed=%d",
            scanned,
            removed_images,
            removed_thumbnails,
            failed,
        )
    return {
        "scanned": scanned,
        "removed_images": removed_images,
        "removed_thumbnails": removed_thumbnails,
        "failed": failed,
    }


def delete_all_gallery_images() -> tuple[int, int]:
    """Delete all gallery entries and their image files.

    Returns (total_deleted, file_count) where total_deleted is the number of
    gallery entries removed and file_count is the number of image files deleted.
    The SQLite delete is committed before files are removed, keeping the write
    transaction short; failed file deletes are logged for later cleanup.
    """
    _ensure_database()
    with _storage_lock:
        disk_filenames = _scan_image_files()
        with _connect() as conn:
            with _transaction(conn):
                row = conn.execute(
                    "SELECT COUNT(*) FROM gallery_entries"
                ).fetchone()
                total = int(row[0]) if row else 0

                referenced_filenames = set(_get_all_filenames_on_conn(conn))

                conn.execute("DELETE FROM gallery_entries")
                conn.execute("DELETE FROM gallery_filter_options")
                conn.execute("DELETE FROM thumbnail_jobs")
                conn.execute("DELETE FROM r2_sync_state")
                _invalidate_filter_options_cache()
                _invalidate_gallery_query_caches_on_conn(conn)

    filenames_to_delete = referenced_filenames | disk_filenames
    deleted_count = 0
    for filename in filenames_to_delete:
        if _delete_gallery_file_if_unreferenced(filename):
            deleted_count += 1
    _clear_verified_thumbnails()
    return total, deleted_count


def invalidate_thumbnail_cache(thumbnail_filename: str) -> None:
    """从内存缩略图验证缓存中移除指定文件名，供路由层在检测到磁盘文件丢失时调用。"""
    _remove_verified_thumbnail(thumbnail_filename)

__all__ = [name for name in globals() if not name.startswith("__")]
