"""Image generation and edit job queue persistence."""

from .db import *


def upsert_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_generate_job(job)

    with _connect() as conn:
        with _transaction(conn):
            _upsert_generate_job_on_conn(conn, normalized)
    return normalized


def get_generate_job(job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GENERATE_JOB_COLUMNS)}
            FROM generate_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return _generate_job_from_row(row)


def pop_generate_job_webhook(job_id: str) -> str:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                "SELECT webhook_url FROM generate_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            webhook_url = str(row["webhook_url"] or "") if row else ""
            if not webhook_url:
                return ""
            cursor = conn.execute(
                """
                UPDATE generate_jobs
                SET webhook_url = NULL
                WHERE job_id = ? AND webhook_url = ?
                """,
                (job_id, webhook_url),
            )
            return webhook_url if cursor.rowcount > 0 else ""


def get_generate_job_updated_at_edge(job_id: str) -> str | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM generate_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return str(row["updated_at"]) if row else None


def get_generate_jobs_list_updated_at_edge(
    *,
    statuses: set[str] | None = None,
) -> tuple[int, str]:
    _ensure_database()
    params: list[Any] = []
    sql = "SELECT COUNT(*) AS row_count, MAX(updated_at) AS updated_at FROM generate_jobs"
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        sql += f" WHERE status IN ({placeholders})"
        params.extend(sorted(statuses))
    with _connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return 0, ""
    return int(row["row_count"] or 0), str(row["updated_at"] or "")


def get_generate_jobs_updated_at_edges(
    *,
    statuses: set[str] | None = None,
    job_ids: set[str] | None = None,
) -> dict[str, str]:
    _ensure_database()
    params: list[Any] = []
    where: list[str] = []
    unique_statuses = _unique_sqlite_values(statuses or []) if statuses else []
    if unique_statuses:
        placeholders = ", ".join("?" for _ in unique_statuses)
        where.append(f"status IN ({placeholders})")
        params.extend(unique_statuses)

    if job_ids is not None:
        unique_job_ids = _unique_sqlite_values(job_ids)
        if not unique_job_ids:
            return {}
        rows_by_job_id: dict[str, str] = {}
        base_sql = "SELECT job_id, updated_at FROM generate_jobs"
        if where:
            base_sql += " WHERE " + " AND ".join(where)
        with _connect() as conn:
            for chunk in _iter_sqlite_in_chunks(unique_job_ids):
                placeholders = ", ".join("?" for _ in chunk)
                sql = f"{base_sql}{' AND' if where else ' WHERE'} job_id IN ({placeholders})"
                rows = conn.execute(sql, [*params, *chunk]).fetchall()
                rows_by_job_id.update(
                    {str(row["job_id"]): str(row["updated_at"]) for row in rows}
                )
        return rows_by_job_id

    sql = "SELECT job_id, updated_at FROM generate_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {str(row["job_id"]): str(row["updated_at"]) for row in rows}


def count_active_image_job_units() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM image_job_units WHERE status IN ('queued', 'running')"
        ).fetchone()
    return int(row[0] or 0) if row else 0


def count_pending_image_job_units() -> tuple[int, int]:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_count
            FROM image_job_units
            WHERE status IN ('queued', 'running')
            """
        ).fetchone()
    if not row:
        return 0, 0
    return int(row["running_count"] or 0), int(row["queued_count"] or 0)


def get_pending_edit_source_bytes() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(byte_count), 0) FROM edit_source_reservations"
        ).fetchone()
    return int(row[0] or 0) if row else 0


def release_edit_source_reservation(job_id: str) -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                "SELECT byte_count FROM edit_source_reservations WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return 0
            conn.execute(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                (job_id,),
            )
    return int(row["byte_count"] or 0)


def _build_image_job_units(
    *,
    parent_job_id: str,
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    now = utc_now()
    return [
        {
            "unit_id": str(uuid.uuid4()),
            "parent_job_id": parent_job_id,
            "operation": operation,
            "unit_index": index,
            "status": "queued",
            "stage": "queued",
            "message": "Queued image unit",
            "created_at": now,
            "updated_at": now,
            "request": {**request, "n": 1},
            "edit_sources": edit_sources or [],
            "api_preset_id": api_preset_id,
            "api_preset_name": api_preset_name,
            "api_path": api_path,
        }
        for index in range(max(1, int(image_units or 1)))
    ]


def enqueue_image_job(
    *,
    parent_job: dict[str, Any],
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
    pending_edit_source_bytes: int = 0,
    max_active_generate_jobs: int,
    max_queued_generate_jobs: int,
    max_pending_edit_source_bytes: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _ensure_database()
    normalized_job = _normalize_generate_job(parent_job)
    units = _build_image_job_units(
        parent_job_id=str(normalized_job["job_id"]),
        operation=operation,
        request=request,
        image_units=image_units,
        api_preset_id=api_preset_id,
        api_preset_name=api_preset_name,
        api_path=api_path,
        edit_sources=edit_sources,
    )
    requested_units = len(units)
    capacity = max(1, int(max_active_generate_jobs or 1)) + max(
        0,
        int(max_queued_generate_jobs or 0),
    )
    reserved_bytes = max(0, int(pending_edit_source_bytes or 0))
    max_reserved_bytes = max(0, int(max_pending_edit_source_bytes or 0))
    unit_columns_sql = ", ".join(IMAGE_JOB_UNIT_COLUMNS)
    unit_placeholders_sql = ", ".join("?" for _ in IMAGE_JOB_UNIT_COLUMNS)
    now = utc_now()

    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0) AS running_count,
                    COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count
                FROM image_job_units
                WHERE status IN ('queued', 'running')
                """
            ).fetchone()
            running_count = int(row["running_count"] or 0) if row else 0
            queued_count = int(row["queued_count"] or 0) if row else 0
            if running_count + queued_count + requested_units > capacity:
                raise ImageJobQueueFullError("Generation job queue is full")

            existing_reservation = conn.execute(
                "SELECT byte_count FROM edit_source_reservations WHERE job_id = ?",
                (normalized_job["job_id"],),
            ).fetchone()
            existing_bytes = (
                int(existing_reservation["byte_count"] or 0)
                if existing_reservation
                else 0
            )
            pending_row = conn.execute(
                "SELECT COALESCE(SUM(byte_count), 0) AS byte_count FROM edit_source_reservations"
            ).fetchone()
            current_reserved_bytes = (
                int(pending_row["byte_count"] or 0) if pending_row else 0
            )
            if (
                reserved_bytes > 0
                and max_reserved_bytes > 0
                and current_reserved_bytes - existing_bytes + reserved_bytes > max_reserved_bytes
            ):
                raise EditSourceQueueFullError("Edit source queue is full")

            _upsert_generate_job_on_conn(conn, normalized_job)
            if reserved_bytes > 0:
                conn.execute(
                    """
                    INSERT INTO edit_source_reservations (job_id, byte_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        byte_count = excluded.byte_count,
                        updated_at = excluded.updated_at
                    """,
                    (normalized_job["job_id"], reserved_bytes, now, now),
                )
            elif existing_bytes > 0:
                conn.execute(
                    "DELETE FROM edit_source_reservations WHERE job_id = ?",
                    (normalized_job["job_id"],),
                )
            conn.executemany(
                f"""
                INSERT INTO image_job_units ({unit_columns_sql})
                VALUES ({unit_placeholders_sql})
                """,
                [_image_job_unit_values(unit) for unit in units],
            )
    return normalized_job, units


def create_image_job_units(
    *,
    parent_job_id: str,
    operation: str,
    request: dict[str, Any],
    image_units: int,
    api_preset_id: str,
    api_preset_name: str,
    api_path: str,
    edit_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    units = _build_image_job_units(
        parent_job_id=parent_job_id,
        operation=operation,
        request=request,
        image_units=image_units,
        api_preset_id=api_preset_id,
        api_preset_name=api_preset_name,
        api_path=api_path,
        edit_sources=edit_sources,
    )
    columns_sql = ", ".join(IMAGE_JOB_UNIT_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in IMAGE_JOB_UNIT_COLUMNS)
    with _connect() as conn:
        with _transaction(conn):
            conn.executemany(
                f"INSERT INTO image_job_units ({columns_sql}) VALUES ({placeholders_sql})",
                [_image_job_unit_values(unit) for unit in units],
            )
    return units


def get_image_job_unit(unit_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
            FROM image_job_units
            WHERE unit_id = ?
            """,
            (unit_id,),
        ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def claim_next_image_job_unit(
    *,
    worker_id: str,
    lease_expires_at: str,
    now: str,
    running_limit: int,
) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                WITH
                    running_count(value) AS (
                        SELECT COUNT(*)
                        FROM image_job_units
                        WHERE status = 'running'
                    ),
                    expired_candidate(unit_id, priority) AS (
                        SELECT unit_id, 0
                        FROM image_job_units
                        WHERE status = 'running'
                            AND claim_expires_at IS NOT NULL
                            AND claim_expires_at <= ?
                        ORDER BY claim_expires_at ASC, created_at ASC, unit_index ASC
                        LIMIT 1
                    ),
                    queued_candidate(unit_id, priority) AS (
                        SELECT unit_id, 1
                        FROM image_job_units
                        WHERE status = 'queued'
                        ORDER BY created_at ASC, unit_index ASC
                        LIMIT 1
                    ),
                    candidate(unit_id, priority) AS (
                        SELECT unit_id, priority FROM expired_candidate
                        UNION ALL
                        SELECT unit_id, priority FROM queued_candidate
                        ORDER BY priority ASC
                        LIMIT 1
                    )
                UPDATE image_job_units
                SET status = 'running',
                    claimed_by = ?,
                    claim_expires_at = ?,
                    stage = COALESCE(NULLIF(stage, 'queued'), stage),
                    message = COALESCE(message, 'Running image unit'),
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE unit_id = (SELECT unit_id FROM candidate)
                    AND (SELECT value FROM running_count) < ?
                RETURNING {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                """,
                (
                    now,
                    worker_id,
                    lease_expires_at,
                    now,
                    now,
                    max(1, int(running_limit or 1)),
                ),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def update_image_job_unit_progress(
    unit_id: str,
    *,
    stage: str,
    message: str,
    claim_expires_at: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET stage = ?,
                    message = ?,
                    claim_expires_at = COALESCE(?, claim_expires_at),
                    updated_at = ?
                WHERE unit_id = ? AND status = 'running'
                """,
                (stage, message, claim_expires_at, now, unit_id),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def complete_image_job_unit(
    unit_id: str,
    *,
    result: dict[str, Any],
    stage_timings: dict[str, float],
    duration: str,
    completed_at: str,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = 'success',
                    stage = 'completed',
                    message = 'Image unit completed',
                    result_json = ?,
                    stage_timings_json = ?,
                    duration = ?,
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE unit_id = ?
                """,
                (
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(stage_timings, ensure_ascii=False, sort_keys=True),
                    duration,
                    completed_at,
                    now,
                    unit_id,
                ),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def fail_image_job_unit(
    unit_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    error: str,
    stage_timings: dict[str, float] | None = None,
    duration: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                """
                UPDATE image_job_units
                SET status = ?,
                    stage = ?,
                    message = ?,
                    error = ?,
                    stage_timings_json = ?,
                    duration = ?,
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE unit_id = ?
                """,
                (
                    status,
                    stage,
                    _sanitize_persisted_job_text(message),
                    _sanitize_persisted_job_text(error),
                    json.dumps(stage_timings or {}, ensure_ascii=False, sort_keys=True),
                    duration,
                    completed_at or now,
                    now,
                    unit_id,
                ),
            )
            row = conn.execute(
                f"""
                SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
                FROM image_job_units
                WHERE unit_id = ?
                """,
                (unit_id,),
            ).fetchone()
    return _image_job_unit_from_row(row) if row else None


def cancel_image_job_units(parent_job_id: str) -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                """
                UPDATE image_job_units
                SET status = 'cancelled',
                    stage = 'cancelled',
                    message = 'Generation job cancelled',
                    error = 'Generation job cancelled',
                    completed_at = ?,
                    updated_at = ?,
                    claim_expires_at = NULL
                WHERE parent_job_id = ? AND status IN ('queued', 'running')
                """,
                (now, now, parent_job_id),
            )
            return cursor.rowcount


def aggregate_image_job_units(parent_job_id: str) -> dict[str, Any]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {", ".join(IMAGE_JOB_UNIT_COLUMNS)}
            FROM image_job_units
            WHERE parent_job_id = ?
            ORDER BY unit_index ASC
            """,
            (parent_job_id,),
        ).fetchall()
    units = [_image_job_unit_from_row(row) for row in rows]
    total = len(units)
    terminal_statuses = {"success", "error", "upstream_error", "cancelled", "interrupted"}
    completed = sum(1 for unit in units if unit.get("status") in terminal_statuses)
    successes = [unit for unit in units if unit.get("status") == "success"]
    failures = [unit for unit in units if unit.get("status") in {"error", "upstream_error"}]
    cancelled = [unit for unit in units if unit.get("status") == "cancelled"]
    running = [unit for unit in units if unit.get("status") == "running"]
    queued = [unit for unit in units if unit.get("status") == "queued"]
    images: list[dict[str, Any]] = []
    stage_timings: dict[str, float] = {}
    for unit in successes:
        result = unit.get("result") or {}
        unit_images = result.get("images") if isinstance(result, dict) else None
        if isinstance(unit_images, list):
            images.extend(image for image in unit_images if isinstance(image, dict))
        for key, value in (unit.get("stage_timings") or {}).items():
            try:
                stage_timings[key] = stage_timings.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                continue

    return {
        "total": total,
        "completed": completed,
        "success_count": len(successes),
        "failure_count": len(failures),
        "cancelled_count": len(cancelled),
        "running_count": len(running),
        "queued_count": len(queued),
        "all_terminal": total > 0 and completed == total,
        "all_failed": total > 0 and len(failures) == total,
        "all_cancelled": total > 0 and len(cancelled) == total,
        "images": images,
        "failures": failures,
        "stage_timings": stage_timings,
        "units": units,
    }


def _get_generate_job_rows_on_conn(
    conn: sqlite3.Connection,
    *,
    statuses: set[str] | None = None,
    limit: int | None = None,
    before_updated_at: str | None = None,
    before_job_id: str | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where: list[str] = []
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        where.append(f"status IN ({placeholders})")
        params.extend(sorted(statuses))
    normalized_before_updated_at = str(before_updated_at or "").strip()
    normalized_before_job_id = str(before_job_id or "").strip()
    if normalized_before_updated_at and normalized_before_job_id:
        where.append("(updated_at < ? OR (updated_at = ? AND job_id < ?))")
        params.extend(
            [
                normalized_before_updated_at,
                normalized_before_updated_at,
                normalized_before_job_id,
            ]
        )

    sql = f"""
        SELECT {", ".join(GENERATE_JOB_COLUMNS)}
        FROM generate_jobs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, job_id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def list_generate_jobs(
    statuses: set[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    before_updated_at: str | None = None,
    before_job_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    normalized_offset = max(0, int(offset or 0))
    seek_updated_at = str(before_updated_at or "").strip() or None
    seek_job_id = str(before_job_id or "").strip() or None
    if bool(seek_updated_at) != bool(seek_job_id):
        seek_updated_at = None
        seek_job_id = None

    with _connect() as conn:
        remaining_offset = normalized_offset
        while remaining_offset > 0:
            skip_limit = min(remaining_offset, 500)
            skipped_rows = _get_generate_job_rows_on_conn(
                conn,
                statuses=statuses,
                limit=skip_limit,
                before_updated_at=seek_updated_at,
                before_job_id=seek_job_id,
            )
            if not skipped_rows:
                return []
            remaining_offset -= len(skipped_rows)
            last_skipped = skipped_rows[-1]
            seek_updated_at = str(last_skipped["updated_at"] or "")
            seek_job_id = str(last_skipped["job_id"] or "")
            if len(skipped_rows) < skip_limit:
                return []

        rows = _get_generate_job_rows_on_conn(
            conn,
            statuses=statuses,
            limit=limit,
            before_updated_at=seek_updated_at,
            before_job_id=seek_job_id,
        )
    return [_generate_job_from_row(row) for row in rows]


def clear_generate_job_history() -> int:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM generate_jobs
                WHERE status NOT IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            ).fetchall()
            if not rows:
                return 0
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            cursor = conn.execute(
                f"""
                DELETE FROM generate_jobs
                WHERE status NOT IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            )
            return cursor.rowcount


def mark_active_generate_jobs_interrupted() -> int:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM generate_jobs
                WHERE status IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            ).fetchall()
            if not rows:
                return 0
            job_ids = [row["job_id"] for row in rows]
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(job_id,) for job_id in job_ids],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(job_id,) for job_id in job_ids],
            )

            conn.execute(
                f"""
                UPDATE generate_jobs
                SET status = 'interrupted',
                    stage = 'interrupted',
                    message = 'Job interrupted by server restart',
                    error = 'Job interrupted by server restart',
                    completed_at = ?,
                    updated_at = ?
                WHERE status IN ({placeholders})
                """,
                (now, now, *tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES))),
            )
            return len(rows)


def trim_generate_jobs(max_jobs: int):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute("SELECT COUNT(*) FROM generate_jobs").fetchone()
            total = int(row[0]) if row else 0
            if total <= max_jobs:
                return

            removable_count = total - max_jobs
            rows = conn.execute(
                """
                SELECT job_id
                FROM generate_jobs
                WHERE status NOT IN ('queued', 'running')
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (removable_count,),
            ).fetchall()
            if not rows:
                return
            conn.executemany(
                "DELETE FROM edit_source_reservations WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM image_job_units WHERE parent_job_id = ?",
                [(row["job_id"],) for row in rows],
            )
            conn.executemany(
                "DELETE FROM generate_jobs WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )


__all__ = [name for name in globals() if not name.startswith("_")]

