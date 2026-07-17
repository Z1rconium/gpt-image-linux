"""Incremental gallery-to-object-storage synchronization state."""

from ..db import *


def _gallery_r2_sync_changed_condition() -> str:
    return """
        (
            state.filename IS NULL
            OR (local.sha256 IS NOT NULL AND COALESCE(state.sha256, '') != local.sha256)
            OR (local.bytes IS NOT NULL AND COALESCE(state.bytes, 0) != local.bytes)
            OR state.key != (? || local.filename)
        )
    """


def count_gallery_r2_sync_rows(
    *,
    key_prefix: str = "",
    full_reconcile: bool = False,
    start_after_filename: str = "",
) -> int:
    """Count unique local filenames that R2 sync should compare."""
    _ensure_database()
    start_after = str(start_after_filename or "")
    with _connect() as conn:
        if full_reconcile:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT filename
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                )
                """,
                (start_after,),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                WITH local AS (
                    SELECT
                        MIN(id) AS id,
                        filename,
                        MAX(bytes) AS bytes,
                        MAX(NULLIF(sha256, '')) AS sha256
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                )
                SELECT COUNT(*)
                FROM local
                LEFT JOIN r2_sync_state state ON state.filename = local.filename
                WHERE {_gallery_r2_sync_changed_condition()}
                """,
                (start_after, key_prefix),
            ).fetchone()
    return int(row[0] or 0) if row else 0


def iter_gallery_r2_sync_rows(
    *,
    key_prefix: str = "",
    full_reconcile: bool = False,
    start_after_filename: str = "",
    batch_size: int = GALLERY_SYNC_BATCH_SIZE,
) -> Iterator[dict[str, Any]]:
    """Yield minimal, filename-unique rows for R2 sync.

    The default path only yields local filenames that are new or changed relative
    to r2_sync_state. full_reconcile ignores that cache so remote deletions can
    be detected without using export-shaped rows.
    """
    _ensure_database()
    normalized_batch_size = max(1, int(batch_size or GALLERY_SYNC_BATCH_SIZE))
    last_filename = str(start_after_filename or "")
    while True:
        with _connect() as conn:
            if full_reconcile:
                rows = conn.execute(
                    """
                    SELECT
                        MIN(id) AS id,
                        filename,
                        MAX(bytes) AS bytes,
                        MAX(NULLIF(sha256, '')) AS sha256
                    FROM gallery_entries
                    WHERE filename IS NOT NULL
                        AND trim(filename) != ''
                        AND filename > ?
                    GROUP BY filename
                    ORDER BY filename ASC
                    LIMIT ?
                    """,
                    (last_filename, normalized_batch_size),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    WITH local AS (
                        SELECT
                            MIN(id) AS id,
                            filename,
                            MAX(bytes) AS bytes,
                            MAX(NULLIF(sha256, '')) AS sha256
                        FROM gallery_entries
                        WHERE filename IS NOT NULL
                            AND trim(filename) != ''
                            AND filename > ?
                        GROUP BY filename
                    )
                    SELECT local.id, local.filename, local.bytes, local.sha256
                    FROM local
                    LEFT JOIN r2_sync_state state ON state.filename = local.filename
                    WHERE {_gallery_r2_sync_changed_condition()}
                    ORDER BY local.filename ASC
                    LIMIT ?
                    """,
                    (last_filename, key_prefix, normalized_batch_size),
                ).fetchall()
        if not rows:
            return
        for row in rows:
            yield {
                "id": row["id"],
                "filename": row["filename"],
                "bytes": row["bytes"],
                "sha256": row["sha256"],
            }
        if len(rows) < normalized_batch_size:
            return
        last_filename = str(rows[-1]["filename"] or "")


def mark_gallery_r2_sync_state(rows: Iterable[dict[str, Any]]) -> None:
    """Mark local filenames as confirmed in R2."""
    prepared: list[tuple[str, str | None, int, str, str | None, str, str]] = []
    synced_at = utc_now()
    for row in rows:
        filename = str(row.get("filename") or "").strip()
        key = str(row.get("key") or "").strip()
        if not filename or not key:
            continue
        sha256 = str(row.get("sha256") or "").strip() or None
        byte_size = _coerce_nonnegative_int(row.get("bytes"), 0)
        etag = str(row.get("etag") or "").strip() or None
        last_remote_seen_at = str(row.get("last_remote_seen_at") or "").strip() or synced_at
        prepared.append((filename, sha256, byte_size, key, etag, last_remote_seen_at, synced_at))
    if not prepared:
        return

    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            conn.executemany(
                """
                INSERT INTO r2_sync_state (
                    filename, sha256, bytes, key, etag, last_remote_seen_at, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    sha256 = excluded.sha256,
                    bytes = excluded.bytes,
                    key = excluded.key,
                    etag = excluded.etag,
                    last_remote_seen_at = excluded.last_remote_seen_at,
                    synced_at = excluded.synced_at
                """,
                prepared,
            )


def update_gallery_entry_hash(filename: str, sha256: str, byte_size: int) -> None:
    """Backfill sha256/bytes for entries sharing a filename. Best-effort."""
    if not filename or not sha256:
        return
    _ensure_database()
    try:
        with _connect() as conn:
            with _transaction(conn):
                conn.execute(
                    """
                    UPDATE gallery_entries
                    SET sha256 = CASE
                            WHEN sha256 IS NULL OR sha256 = '' THEN ?
                            ELSE sha256
                        END,
                        bytes = COALESCE(bytes, ?)
                    WHERE filename = ?
                      AND (
                          sha256 IS NULL OR sha256 = ''
                          OR bytes IS NULL
                      )
                    """,
                    (sha256, byte_size, filename),
                )
                _invalidate_gallery_total_bytes_cache()
    except sqlite3.Error as e:
        logger.warning("Failed to persist sha256 for %s: %s", filename, e)


__all__ = [name for name in globals() if not name.startswith("_")]

