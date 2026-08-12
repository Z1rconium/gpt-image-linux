"""SQLite-backed leases, worker coordination, and gallery task records."""

import ipaddress
import time

from .db import *


def _normalize_access_client_ip(client_ip: str) -> str:
    value = str(client_ip or "unknown").strip()[:256] or "unknown"
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def _cleanup_expired_access_failures_on_conn(
    conn: sqlite3.Connection,
    now: float,
    lockout_seconds: int,
) -> int:
    cutoff = float(now) - max(1, int(lockout_seconds or 1))
    cursor = conn.execute(
        "DELETE FROM access_failures WHERE last_failed_at <= ?",
        (cutoff,),
    )
    return int(cursor.rowcount or 0)


def get_access_lockout(
    client_ip: str,
    *,
    max_failures: int,
    lockout_seconds: int,
    now: float | None = None,
) -> int:
    """Return remaining lockout seconds for a client IP, or 0 when allowed."""

    _ensure_database()
    current_time = float(time.time() if now is None else now)
    max_count = max(1, int(max_failures or 1))
    lockout = max(1, int(lockout_seconds or 1))
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT failure_count, last_failed_at
            FROM access_failures
            WHERE client_ip = ?
            """,
            (normalized_ip,),
        ).fetchone()
        if not row:
            return 0
        elapsed = current_time - float(row["last_failed_at"] or 0)
        if elapsed < lockout and int(row["failure_count"] or 0) >= max_count:
            return max(1, int(lockout - elapsed))
    return 0


def record_access_failure(
    client_ip: str,
    *,
    lockout_seconds: int,
    max_entries: int,
    now: float | None = None,
) -> int:
    """Record one failed access attempt and return the updated count."""

    _ensure_database()
    current_time = float(time.time() if now is None else now)
    lockout = max(1, int(lockout_seconds or 1))
    max_size = max(1, int(max_entries or 1))
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_access_failures_on_conn(conn, current_time, lockout)
            row = conn.execute(
                """
                SELECT failure_count, first_failed_at
                FROM access_failures
                WHERE client_ip = ?
                """,
                (normalized_ip,),
            ).fetchone()
            count = 1 if not row else int(row["failure_count"] or 0) + 1
            first_failed_at = current_time if not row else float(row["first_failed_at"] or current_time)
            conn.execute(
                """
                INSERT INTO access_failures (
                    client_ip,
                    failure_count,
                    first_failed_at,
                    last_failed_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    failure_count = excluded.failure_count,
                    first_failed_at = excluded.first_failed_at,
                    last_failed_at = excluded.last_failed_at
                """,
                (normalized_ip, count, first_failed_at, current_time),
            )
            overflow_row = conn.execute(
                "SELECT COUNT(*) - ? FROM access_failures",
                (max_size,),
            ).fetchone()
            overflow = max(0, int(overflow_row[0] or 0) if overflow_row else 0)
            if overflow:
                conn.execute(
                    """
                    DELETE FROM access_failures
                    WHERE client_ip IN (
                        SELECT client_ip
                        FROM access_failures
                        ORDER BY last_failed_at ASC, client_ip ASC
                        LIMIT ?
                    )
                    """,
                    (overflow,),
                )
    return count


def clear_access_failure(client_ip: str) -> bool:
    _ensure_database()
    normalized_ip = _normalize_access_client_ip(client_ip)
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                "DELETE FROM access_failures WHERE client_ip = ?",
                (normalized_ip,),
            )
    return int(cursor.rowcount or 0) > 0


def list_access_failures() -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT client_ip, failure_count, first_failed_at, last_failed_at
            FROM access_failures
            ORDER BY last_failed_at ASC, client_ip ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_gallery_job(**job: Any) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def get_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_JOB_COLUMNS)}
            FROM gallery_jobs
            WHERE kind = ? AND job_id = ?
            """,
            (kind, job_id),
        ).fetchone()
    return _gallery_job_from_row(row) if row else None


def get_gallery_jobs_updated_at_edges(kind: str, job_ids: set[str]) -> dict[str, str]:
    _ensure_database()
    unique_job_ids = _unique_sqlite_values(job_ids)
    if not unique_job_ids:
        return {}
    rows_by_job_id: dict[str, str] = {}
    with _connect() as conn:
        for chunk in _iter_sqlite_in_chunks(unique_job_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT job_id, updated_at
                FROM gallery_jobs
                WHERE kind = ? AND job_id IN ({placeholders})
                """,
                (kind, *chunk),
            ).fetchall()
            rows_by_job_id.update({str(row["job_id"]): str(row["updated_at"]) for row in rows})
    return rows_by_job_id


_GALLERY_JOB_UPDATE_COLUMNS = set(GALLERY_JOB_COLUMNS) - {"job_id", "kind", "created_at"}
_GALLERY_JOB_INTEGER_UPDATE_COLUMNS = {
    "progress",
    "requested_count",
    "processed_count",
    "exported_count",
    "missing_count",
    "total_count",
    "compared_count",
    "uploaded_count",
    "pending_upload_count",
    "skipped_existing_count",
    "missing_local_count",
    "failed_count",
    "bytes_total",
    "bytes_written",
    "bytes_uploaded",
}


def _normalize_gallery_job_updates(updates: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in updates.items():
        if key == "payload":
            key = "payload_json"
        if key not in _GALLERY_JOB_UPDATE_COLUMNS:
            continue
        if key == "payload_json":
            try:
                normalized[key] = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value or {}, ensure_ascii=False, sort_keys=True)
                )
            except TypeError:
                continue
            continue
        if key in _GALLERY_JOB_INTEGER_UPDATE_COLUMNS:
            normalized[key] = _coerce_nonnegative_int(value, 0)
        else:
            normalized[key] = None if value is None else str(value)
    normalized["updated_at"] = str(updates.get("updated_at") or utc_now())
    return normalized


def update_gallery_job(
    job_id: str,
    updates: dict[str, Any],
    *,
    lease_owner: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    if not updates:
        with _connect() as conn:
            if lease_owner:
                row = conn.execute(
                    f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ? AND lease_owner = ?",
                    (job_id, lease_owner),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
        return _gallery_job_from_row(row) if row else None

    normalized = _normalize_gallery_job_updates(updates)

    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            if lease_owner:
                cursor = conn.execute(
                    f"""
                    UPDATE gallery_jobs
                    SET {assignments}
                    WHERE job_id = ?
                        AND lease_owner = ?
                        AND status = 'running'
                        AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at > ?
                        )
                    """,
                    (*normalized.values(), job_id, lease_owner, utc_now()),
                )
                if int(cursor.rowcount or 0) <= 0:
                    return None
            else:
                conn.execute(
                    f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                    (*normalized.values(), job_id),
                )
            row = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    return _gallery_job_from_row(row) if row else None


def update_gallery_job_progress(
    job_id: str,
    updates: dict[str, Any],
    *,
    lease_owner: str | None = None,
) -> bool:
    """Update a gallery job without fetching the row back.

    Used for high-frequency export/sync progress writes where SSE only needs the
    updated_at edge and will read the full row on its own poll.
    """
    _ensure_database()
    normalized = _normalize_gallery_job_updates(updates)
    if not normalized:
        return False
    assignments = ", ".join(f"{key} = ?" for key in normalized)
    with _connect() as conn:
        with _transaction(conn):
            if lease_owner:
                cursor = conn.execute(
                    f"""
                    UPDATE gallery_jobs
                    SET {assignments}
                    WHERE job_id = ?
                        AND lease_owner = ?
                        AND status = 'running'
                        AND (
                            lease_expires_at IS NULL
                            OR lease_expires_at > ?
                        )
                    """,
                    (*normalized.values(), job_id, lease_owner, utc_now()),
                )
            else:
                cursor = conn.execute(
                    f"UPDATE gallery_jobs SET {assignments} WHERE job_id = ?",
                    (*normalized.values(), job_id),
                )
    return cursor.rowcount > 0


def renew_gallery_job_lease(
    *,
    job_id: str,
    lease_owner: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    current_time = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE gallery_jobs
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                    AND status = 'running'
                    AND lease_owner = ?
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at > ?
                    )
                """,
                (lease_expires_at, current_time, job_id, lease_owner, current_time),
            )
    return cursor.rowcount > 0


def count_active_gallery_jobs(kind: str) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM gallery_jobs
            WHERE kind = ?
                AND (
                    status = 'queued'
                    OR (
                        status = 'running'
                        AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                    )
                )
            """,
            (kind, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _cleanup_expired_import_upload_reservations_on_conn(
    conn: sqlite3.Connection,
    now: str,
) -> int:
    cursor = conn.execute(
        "DELETE FROM import_upload_reservations WHERE lease_expires_at <= ?",
        (now,),
    )
    return int(cursor.rowcount or 0)


def reserve_import_upload_capacity(
    *,
    reservation_id: str,
    client_ip: str,
    byte_count: int,
    max_total_bytes: int,
    per_ip_limit: int,
    lease_expires_at: str,
    now: str | None = None,
) -> tuple[bool, str]:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    normalized_ip = str(client_ip or "unknown").strip()[:256] or "unknown"
    reserved_bytes = _coerce_nonnegative_int(byte_count, 0)
    total_limit = max(1, int(max_total_bytes or 1))
    ip_limit = max(1, int(per_ip_limit or 1))
    if not normalized_id or reserved_bytes <= 0:
        return False, "invalid"
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(byte_count), 0)
                FROM import_upload_reservations
                WHERE lease_expires_at > ?
                """,
                (now,),
            ).fetchone()
            current_bytes = int(total_row[0] or 0) if total_row else 0
            if current_bytes + reserved_bytes > total_limit:
                return False, "temp_space"

            window_start = (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).isoformat()
            ip_row = conn.execute(
                """
                SELECT COUNT(*)
                FROM import_upload_reservations
                WHERE client_ip = ? AND created_at > ?
                """,
                (normalized_ip, window_start),
            ).fetchone()
            if int(ip_row[0] or 0) >= ip_limit:
                return False, "ip_rate"

            conn.execute(
                """
                INSERT INTO import_upload_reservations (
                    reservation_id,
                    client_ip,
                    byte_count,
                    created_at,
                    updated_at,
                    lease_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    normalized_ip,
                    reserved_bytes,
                    now,
                    now,
                    lease_expires_at,
                ),
            )
    return True, "reserved"


def resize_import_upload_reservation(
    reservation_id: str,
    byte_count: int,
    *,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    if not normalized_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            cursor = conn.execute(
                """
                UPDATE import_upload_reservations
                SET byte_count = ?,
                    updated_at = ?
                WHERE reservation_id = ?
                    AND lease_expires_at > ?
                """,
                (
                    _coerce_nonnegative_int(byte_count, 0),
                    now,
                    normalized_id,
                    now,
                ),
            )
    return int(cursor.rowcount or 0) > 0


def release_import_upload_reservation(
    reservation_id: str,
    *,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_id = str(reservation_id or "").strip()
    if not normalized_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_import_upload_reservations_on_conn(conn, now)
            cursor = conn.execute(
                "DELETE FROM import_upload_reservations WHERE reservation_id = ?",
                (normalized_id,),
            )
    return int(cursor.rowcount or 0) > 0


def reserve_gallery_job_capacity(
    *,
    job: dict[str, Any],
    counted_kinds: Sequence[str],
    max_active: int,
) -> dict[str, Any] | None:
    _ensure_database()
    normalized = _normalize_gallery_job(job)
    kinds = [str(kind) for kind in counted_kinds if str(kind)]
    if normalized["kind"] not in kinds:
        kinds.append(normalized["kind"])
    placeholders = ", ".join("?" for _ in kinds)
    now = utc_now()
    columns_sql = ", ".join(GALLERY_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_JOB_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({placeholders})
                    AND (
                        status = 'queued'
                        OR (
                            status = 'running'
                            AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                        )
                    )
                """,
                (*kinds, now),
            ).fetchone()
            active_count = int(row[0] or 0) if row else 0
            if active_count >= max(1, int(max_active or 1)):
                return None
            conn.execute(
                f"INSERT INTO gallery_jobs ({columns_sql}) VALUES ({placeholders_sql})",
                _gallery_job_values(normalized),
            )
    return normalized | {"payload": _json_loads_dict(normalized.get("payload_json"))}


def claim_next_gallery_job(
    *,
    kind: str,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    running_limit: int,
    counted_kinds: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    active_kinds = [str(value) for value in (counted_kinds or (kind,)) if str(value)]
    if kind not in active_kinds:
        active_kinds.append(kind)
    active_placeholders = ", ".join("?" for _ in active_kinds)
    with _connect() as conn:
        with _transaction(conn):
            running_row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM gallery_jobs
                WHERE kind IN ({active_placeholders}) AND status = 'running'
                    AND (lease_expires_at IS NULL OR lease_expires_at > ?)
                """,
                (*active_kinds, now),
            ).fetchone()
            if int(running_row[0] or 0) >= max(1, int(running_limit or 1)):
                return None

            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND (
                        status = 'queued'
                        OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                    )
                ORDER BY
                    CASE WHEN status = 'running' THEN 0 ELSE 1 END,
                    created_at ASC
                LIMIT 1
                """,
                (kind, now),
            ).fetchone()
            if not row:
                return None

            started_at = row["started_at"] or now
            conn.execute(
                """
                UPDATE gallery_jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, lease_expires_at, started_at, now, row["job_id"]),
            )
            claimed = conn.execute(
                f"SELECT {', '.join(GALLERY_JOB_COLUMNS)} FROM gallery_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
    return _gallery_job_from_row(claimed) if claimed else None


def cleanup_expired_gallery_jobs(kind: str) -> list[dict[str, Any]]:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ?
                    AND status = 'running'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                """,
                (kind, now),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def cleanup_stale_gallery_jobs(kind: str, ttl_seconds: int) -> list[dict[str, Any]]:
    _ensure_database()
    cutoff = datetime.fromtimestamp(
        time.time() - max(0, int(ttl_seconds or 0)),
        tz=timezone.utc,
    ).isoformat()
    with _connect() as conn:
        with _transaction(conn):
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND status IN ('success', 'error')
                    AND updated_at <= ?
                """,
                (kind, cutoff),
            ).fetchall()
            if rows:
                conn.executemany(
                    "DELETE FROM gallery_jobs WHERE job_id = ?",
                    [(row["job_id"],) for row in rows],
                )
    return [_gallery_job_from_row(row) for row in rows]


def delete_gallery_job(kind: str, job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_JOB_COLUMNS)}
                FROM gallery_jobs
                WHERE kind = ? AND job_id = ?
                """,
                (kind, job_id),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM gallery_jobs WHERE job_id = ?", (job_id,))
    return _gallery_job_from_row(row) if row else None


def list_gallery_job_ids_with_files(kind: str) -> set[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT job_id
            FROM gallery_jobs
            WHERE kind = ? AND path IS NOT NULL
            """,
            (kind,),
        ).fetchall()
    return {str(row["job_id"]) for row in rows}


def _cleanup_expired_sse_slots_on_conn(conn: sqlite3.Connection, now: str) -> int:
    cursor = conn.execute(
        "DELETE FROM sse_slots WHERE lease_expires_at <= ?",
        (now,),
    )
    return int(cursor.rowcount if cursor.rowcount is not None else 0)


def acquire_sse_slot(
    *,
    client_ip: str,
    connection_id: str,
    lease_expires_at: str,
    max_global: int,
    max_per_ip: int,
    now: str | None = None,
) -> tuple[bool, str]:
    _ensure_database()
    normalized_ip = str(client_ip or "unknown")[:256]
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False, "invalid_connection"
    now = now or utc_now()
    global_limit = max(1, int(max_global or 1))
    ip_limit = max(1, int(max_per_ip or 1))
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            row = conn.execute("SELECT COUNT(*) FROM sse_slots").fetchone()
            global_count = int(row[0] or 0) if row else 0
            if global_count >= global_limit:
                return False, "global_limit"

            row = conn.execute(
                "SELECT COUNT(*) FROM sse_slots WHERE client_ip = ?",
                (normalized_ip,),
            ).fetchone()
            per_ip_count = int(row[0] or 0) if row else 0
            if per_ip_count >= ip_limit:
                return False, "per_ip_limit"

            conn.execute(
                """
                INSERT INTO sse_slots (
                    connection_id,
                    client_ip,
                    acquired_at,
                    lease_expires_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (normalized_connection_id, normalized_ip, now, lease_expires_at),
            )
    return True, "acquired"


def refresh_sse_slot(
    *,
    connection_id: str,
    lease_expires_at: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                """
                UPDATE sse_slots
                SET lease_expires_at = ?
                WHERE connection_id = ?
                """,
                (lease_expires_at, normalized_connection_id),
            )
    return int(cursor.rowcount or 0) > 0


def release_sse_slot(connection_id: str, *, now: str | None = None) -> bool:
    _ensure_database()
    normalized_connection_id = str(connection_id or "").strip()
    if not normalized_connection_id:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            cursor = conn.execute(
                "DELETE FROM sse_slots WHERE connection_id = ?",
                (normalized_connection_id,),
            )
    return int(cursor.rowcount or 0) > 0


def _count_active_sse_slots_on_conn(
    conn: sqlite3.Connection,
    *,
    now: str,
    client_ip: str | None = None,
) -> int:
    if client_ip is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM sse_slots WHERE lease_expires_at > ?",
            (now,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM sse_slots
            WHERE client_ip = ? AND lease_expires_at > ?
            """,
            (client_ip, now),
        ).fetchone()
    return int(row[0] or 0) if row else 0


def count_active_sse_slots(client_ip: str | None = None) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            _cleanup_expired_sse_slots_on_conn(conn, now)
            return _count_active_sse_slots_on_conn(conn, now=now, client_ip=client_ip)


def _acquire_background_lease_on_conn(
    conn: sqlite3.Connection,
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str,
    completed_ttl_seconds: int | None = None,
) -> bool:
    row = conn.execute(
        """
        SELECT owner, lease_expires_at, completed_at
        FROM background_leases
        WHERE name = ?
        """,
        (name,),
    ).fetchone()
    if row:
        completed_at = str(row["completed_at"] or "")
        if completed_ttl_seconds is not None and completed_at:
            now_dt = _coerce_iso_datetime(now) or datetime.now(timezone.utc)
            cutoff = (now_dt - timedelta(seconds=max(0, int(completed_ttl_seconds or 0)))).isoformat()
            if completed_at > cutoff:
                return False

        active_owner = str(row["owner"] or "")
        active_until = str(row["lease_expires_at"] or "")
        if active_until > now and active_owner != owner:
            return False

    conn.execute(
        """
        INSERT INTO background_leases (
            name,
            owner,
            lease_expires_at,
            updated_at,
            completed_at
        )
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT(name) DO UPDATE SET
            owner = excluded.owner,
            lease_expires_at = excluded.lease_expires_at,
            updated_at = excluded.updated_at,
            completed_at = NULL
        """,
        (name, owner, lease_expires_at, now),
    )
    return True


def acquire_background_lease(
    *,
    name: str,
    owner: str,
    lease_expires_at: str,
    now: str | None = None,
    completed_ttl_seconds: int | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            return _acquire_background_lease_on_conn(
                conn,
                name=normalized_name,
                owner=normalized_owner,
                lease_expires_at=lease_expires_at,
                now=now,
                completed_ttl_seconds=completed_ttl_seconds,
            )


def complete_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def release_background_lease(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    _ensure_database()
    normalized_name = str(name or "").strip()
    normalized_owner = str(owner or "").strip()
    if not normalized_name or not normalized_owner:
        return False
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE background_leases
                SET lease_expires_at = ?,
                    updated_at = ?
                WHERE name = ? AND owner = ?
                """,
                (now, now, normalized_name, normalized_owner),
            )
    return int(cursor.rowcount or 0) > 0


def acquire_background_slot(
    *,
    name_prefix: str,
    owner: str,
    slot_count: int,
    lease_expires_at: str,
    now: str | None = None,
) -> str | None:
    _ensure_database()
    normalized_prefix = str(name_prefix or "").strip().rstrip(":")
    normalized_owner = str(owner or "").strip()
    if not normalized_prefix or not normalized_owner:
        return None
    slots = max(1, int(slot_count or 1))
    now = now or utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for index in range(slots):
                name = f"{normalized_prefix}:{index}"
                if _acquire_background_lease_on_conn(
                    conn,
                    name=name,
                    owner=normalized_owner,
                    lease_expires_at=lease_expires_at,
                    now=now,
                ):
                    return name
    return None


def release_background_slot(
    *,
    name: str,
    owner: str,
    now: str | None = None,
) -> bool:
    return release_background_lease(name=name, owner=owner, now=now)


def mark_worker_heartbeat(worker_id: str, active_units: int = 0) -> None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                INSERT INTO worker_heartbeats (worker_id, last_seen_at, active_units)
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    active_units = excluded.active_units
                """,
                (worker_id, now, max(0, int(active_units or 0))),
            )


def record_worker_metrics_snapshot(worker_id: str, snapshot: dict[str, Any]) -> None:
    _ensure_database()
    normalized_worker_id = str(worker_id or "").strip()
    if not normalized_worker_id:
        return
    now = utc_now()
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                INSERT INTO worker_metric_snapshots (
                    worker_id,
                    snapshot_json,
                    updated_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (normalized_worker_id, payload, now),
            )


def _worker_metric_snapshots_on_conn(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    cutoff = (now - timedelta(seconds=WORKER_METRIC_SNAPSHOT_TTL_SECONDS)).isoformat()
    rows = conn.execute(
        """
        SELECT worker_id, snapshot_json, updated_at
        FROM worker_metric_snapshots
        WHERE updated_at > ?
        ORDER BY updated_at DESC
        """,
        (cutoff,),
    ).fetchall()
    workers: list[dict[str, Any]] = []
    for row in rows:
        updated_at = str(row["updated_at"] or "")
        updated_dt = _coerce_iso_datetime(updated_at)
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            snapshot = {}
        age_seconds = (
            max(0.0, (now - updated_dt).total_seconds())
            if updated_dt is not None
            else None
        )
        workers.append(
            {
                "worker_id": str(row["worker_id"]),
                "updated_at": updated_at,
                "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
                "snapshot": snapshot if isinstance(snapshot, dict) else {},
            }
        )
    return workers


def get_runtime_coordination_metrics() -> dict[str, Any]:
    _ensure_database()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with _connect() as conn:
        active_sse_slots = _count_active_sse_slots_on_conn(conn, now=now)

        heartbeat_rows = conn.execute(
            """
            SELECT worker_id, last_seen_at, active_units
            FROM worker_heartbeats
            """
        ).fetchall()
        heartbeat_ages = []
        active_worker_count = 0
        worker_active_units = 0
        for row in heartbeat_rows:
            seen_at = _coerce_iso_datetime(str(row["last_seen_at"] or ""))
            if seen_at is None:
                continue
            age_seconds = max(0.0, (now_dt - seen_at).total_seconds())
            heartbeat_ages.append(age_seconds)
            if age_seconds <= WORKER_METRIC_SNAPSHOT_TTL_SECONDS:
                active_worker_count += 1
                worker_active_units += max(0, int(row["active_units"] or 0))

        lease_rows = conn.execute(
            """
            SELECT name, owner, lease_expires_at, updated_at, completed_at
            FROM background_leases
            ORDER BY name
            """
        ).fetchall()
        active_leases = [
            {
                "name": str(row["name"]),
                "owner": str(row["owner"]),
                "lease_expires_at": str(row["lease_expires_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "completed_at": str(row["completed_at"] or "") or None,
            }
            for row in lease_rows
            if str(row["lease_expires_at"] or "") > now
        ]
        worker_snapshots = _worker_metric_snapshots_on_conn(conn, now_dt)

    return {
        "gauges": {
            "sse.active_connections": active_sse_slots,
            "sse.expired_slots_cleaned": 0,
            "workers.active": active_worker_count,
            "workers.heartbeat_age_max_seconds": round(max(heartbeat_ages), 3)
            if heartbeat_ages
            else 0.0,
            "workers.active_units": worker_active_units,
            "background_leases.active": len(active_leases),
        },
        "background_leases": active_leases,
        "workers": worker_snapshots,
    }


def refresh_runtime_coordination_metrics(
    worker_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Perform low-frequency coordination maintenance and snapshot publication."""
    _ensure_database()
    normalized_worker_id = str(worker_id or "").strip()
    now = utc_now()
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    with _connect() as conn:
        with _transaction(conn):
            expired_sse_slots = _cleanup_expired_sse_slots_on_conn(conn, now)
            if normalized_worker_id:
                conn.execute(
                    """
                    INSERT INTO worker_metric_snapshots (
                        worker_id, snapshot_json, updated_at
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        updated_at = excluded.updated_at
                    """,
                    (normalized_worker_id, payload, now),
                )
        runtime = get_runtime_coordination_metrics()
    runtime.setdefault("gauges", {})["sse.expired_slots_cleaned"] = expired_sse_slots
    return runtime


__all__ = [name for name in globals() if not name.startswith("_")]
