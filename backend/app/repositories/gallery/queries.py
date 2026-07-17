"""Gallery pagination, selection, and read queries."""

from ..db import *
from ..thumbnail_jobs import _attach_gallery_thumbnail_url
from .filters import _get_gallery_filter_options_on_conn


def _get_gallery_count_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
) -> int:
    cache_key = (config.DATABASE_FILE, where_sql, tuple(params))
    now = time.monotonic()
    with _gallery_count_cache_lock:
        cached = _gallery_count_cache.get(cache_key)
        if cached and (now - cached[0]) < GALLERY_COUNT_CACHE_SECONDS:
            _gallery_count_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            _gallery_count_cache.pop(cache_key, None)

    row = conn.execute(
        f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
        tuple(params),
    ).fetchone()
    total = int(row[0]) if row else 0

    with _gallery_count_cache_lock:
        _gallery_count_cache[cache_key] = (now, total)
        _gallery_count_cache.move_to_end(cache_key)
        while len(_gallery_count_cache) > _GALLERY_COUNT_CACHE_MAX_SIZE:
            _gallery_count_cache.popitem(last=False)

    return total


def get_gallery_count(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_count_on_conn(conn, where_sql, params)


def get_gallery_ids(filters: dict[str, Any] | None = None) -> list[str]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        rows = conn.execute(
            f"""
            SELECT id
            FROM gallery_entries
            {where_sql}
            ORDER BY sort_seq DESC, id DESC
            """,
            tuple(params),
        ).fetchall()
    return [str(row["id"]) for row in rows if row["id"]]


def get_gallery_selection_snapshot(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        boundary = conn.execute(
            f"""
            SELECT id, sort_seq
            FROM gallery_entries
            {where_sql}
            ORDER BY sort_seq DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if not boundary:
            return {"count": 0, "boundary": None}

        boundary_sort_seq = int(boundary["sort_seq"] or 0)
        boundary_id = str(boundary["id"] or "")
        bounded_where = _combine_gallery_where(
            where_sql,
            "(sort_seq < ? OR (sort_seq = ? AND id <= ?))",
        )
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM gallery_entries{bounded_where}",
            (*params, boundary_sort_seq, boundary_sort_seq, boundary_id),
        ).fetchone()
    return {
        "count": int(count_row[0] or 0) if count_row else 0,
        "boundary": {
            "sort_seq": boundary_sort_seq,
            "id": boundary_id,
        },
    }


def get_gallery_id_batch(
    filters: dict[str, Any] | None = None,
    *,
    after_sort_seq: int | None = None,
    after_id: str | None = None,
    before_or_at_sort_seq: int | None = None,
    before_or_at_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return a keyset-paginated batch of gallery ids for background jobs."""
    _ensure_database()
    batch_limit = max(1, int(limit or 1))
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        if before_or_at_sort_seq is not None and before_or_at_id is not None:
            where_sql = _combine_gallery_where(
                where_sql,
                "(sort_seq < ? OR (sort_seq = ? AND id <= ?))",
            )
            params = [
                *params,
                int(before_or_at_sort_seq),
                int(before_or_at_sort_seq),
                str(before_or_at_id),
            ]
        rows = _get_gallery_row_batch_after_cursor_on_conn(
            conn,
            where_sql,
            params,
            last_sort_seq=after_sort_seq,
            last_id=after_id,
            limit=batch_limit,
            columns=("id", "sort_seq"),
        )
    return [
        {"id": str(row["id"]), "sort_seq": int(row["sort_seq"] or 0)}
        for row in rows
        if row["id"]
    ]


def _get_gallery_total_bytes_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
) -> int:
    cache_key = (config.DATABASE_FILE, where_sql, tuple(params))
    now = time.monotonic()
    with _gallery_total_bytes_cache_lock:
        cached = _gallery_total_bytes_cache.get(cache_key)
        if cached and (now - cached[0]) < GALLERY_TOTAL_BYTES_CACHE_SECONDS:
            _gallery_total_bytes_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            _gallery_total_bytes_cache.pop(cache_key, None)

    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(bytes), 0) AS total_bytes
        FROM (
            SELECT filename, MAX(bytes) AS bytes
            FROM gallery_entries
            {where_sql}
            GROUP BY filename
        )
        WHERE bytes IS NOT NULL
        """,
        tuple(params),
    ).fetchone()
    total_bytes = int(row["total_bytes"] or 0) if row else 0

    with _gallery_total_bytes_cache_lock:
        _gallery_total_bytes_cache[cache_key] = (now, total_bytes)
        _gallery_total_bytes_cache.move_to_end(cache_key)
        while len(_gallery_total_bytes_cache) > _GALLERY_BYTES_CACHE_MAX_SIZE:
            _gallery_total_bytes_cache.popitem(last=False)

    return total_bytes


def get_gallery_total_bytes(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        return _get_gallery_total_bytes_on_conn(conn, where_sql, params)


def encode_gallery_cursor(sort_seq: int, image_id: str) -> str:
    payload = json.dumps(
        {"sort_seq": int(sort_seq), "id": str(image_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_gallery_cursor(cursor: str) -> tuple[int, str]:
    raw_cursor = str(cursor or "").strip()
    if not raw_cursor:
        raise ValueError("Gallery cursor is required")
    try:
        padded = raw_cursor + ("=" * (-len(raw_cursor) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        sort_seq = int(payload["sort_seq"])
        image_id = str(payload["id"])
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as e:
        raise ValueError("Invalid gallery cursor") from e
    if not image_id:
        raise ValueError("Invalid gallery cursor")
    return sort_seq, image_id


def _gallery_cursor_from_row(row: sqlite3.Row) -> str:
    return encode_gallery_cursor(int(row["sort_seq"] or 0), str(row["id"]))


def _combine_gallery_where(where_sql: str, clause: str) -> str:
    if where_sql:
        return f"{where_sql} AND {clause}"
    return f" WHERE {clause}"


def _gallery_has_row_before_cursor(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    row: sqlite3.Row,
) -> bool:
    sort_seq = int(row["sort_seq"] or 0)
    image_id = str(row["id"])
    cursor_where = _combine_gallery_where(
        where_sql,
        "(sort_seq > ? OR (sort_seq = ? AND id > ?))",
    )
    found = conn.execute(
        f"SELECT 1 FROM gallery_entries{cursor_where} LIMIT 1",
        (*params, sort_seq, sort_seq, image_id),
    ).fetchone()
    return found is not None


def _gallery_has_row_after_cursor(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    row: sqlite3.Row,
) -> bool:
    sort_seq = int(row["sort_seq"] or 0)
    image_id = str(row["id"])
    cursor_where = _combine_gallery_where(
        where_sql,
        "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
    )
    found = conn.execute(
        f"SELECT 1 FROM gallery_entries{cursor_where} LIMIT 1",
        (*params, sort_seq, sort_seq, image_id),
    ).fetchone()
    return found is not None


def _get_gallery_thumbnail_status_map_on_conn(
    conn: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
) -> dict[str, str]:
    filenames = _unique_sqlite_values(row["filename"] for row in rows if row["filename"])
    if not filenames:
        return {}

    now = utc_now()
    queued_filenames: set[str] = set()
    for chunk in _iter_sqlite_in_chunks(filenames):
        placeholders = ", ".join("?" for _ in chunk)
        jobs = conn.execute(
            f"""
            SELECT filename, status, lease_expires_at
            FROM thumbnail_jobs
            WHERE filename IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        for job in jobs:
            status = str(job["status"] or "")
            if status == "queued" or (
                status == "running" and str(job["lease_expires_at"] or "") > now
            ):
                queued_filenames.add(str(job["filename"]))

    return {filename: "queued" for filename in queued_filenames}


def _get_gallery_rows_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[sqlite3.Row]:
    sql = f"""
        SELECT {", ".join(GALLERY_COLUMNS)}
        FROM gallery_entries
        {where_sql}
        ORDER BY sort_seq DESC, id DESC
    """
    query_params: list[Any] = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        query_params.append(limit)
        if offset is not None:
            sql += " OFFSET ?"
            query_params.append(offset)
    return conn.execute(sql, query_params).fetchall()


def _get_gallery_page_rows_by_offset_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    page: int,
    limit: int,
) -> list[sqlite3.Row]:
    offset = (page - 1) * components.page_size
    return _get_gallery_rows_on_conn(
        conn,
        components.where_sql,
        components.params,
        limit=limit,
        offset=offset,
    )


def _get_gallery_anchor_for_page_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    gallery_version: int,
    page: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT page, sort_seq, image_id
        FROM gallery_page_anchors
        WHERE query_key = ?
          AND page_size = ?
          AND gallery_version = ?
          AND page <= ?
        ORDER BY page DESC
        LIMIT 1
        """,
        (components.query_key, components.page_size, gallery_version, page),
    ).fetchone()


def _store_gallery_page_anchor_best_effort(
    components: _GalleryQueryComponents,
    *,
    gallery_version: int,
    page: int,
    row: sqlite3.Row,
):
    if page < 1:
        return
    conn = _open_connection(timeout=0.0, busy_timeout_ms=0)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            message = str(e).lower()
            if "locked" in message or "busy" in message:
                return
            raise
        current_version = _get_gallery_version_on_conn(conn)
        if current_version != gallery_version:
            conn.commit()
            return
        now = utc_now()
        conn.execute(
            """
            INSERT INTO gallery_page_anchors (
                query_key,
                page_size,
                page,
                sort_seq,
                image_id,
                created_at,
                updated_at,
                gallery_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(query_key, page_size, page) DO UPDATE SET
                sort_seq = excluded.sort_seq,
                image_id = excluded.image_id,
                updated_at = excluded.updated_at,
                gallery_version = excluded.gallery_version
            """,
            (
                components.query_key,
                components.page_size,
                page,
                int(row["sort_seq"] or 0),
                str(row["id"]),
                now,
                now,
                gallery_version,
            ),
        )
        stale_rows = conn.execute(
            """
            SELECT page
            FROM gallery_page_anchors
            WHERE query_key = ?
              AND page_size = ?
              AND gallery_version = ?
            ORDER BY updated_at DESC, page DESC
            LIMIT -1 OFFSET ?
            """,
            (
                components.query_key,
                components.page_size,
                gallery_version,
                GALLERY_PAGE_ANCHOR_MAX_PER_QUERY,
            ),
        ).fetchall()
        stale_pages = [int(stale["page"]) for stale in stale_rows]
        if stale_pages:
            placeholders = ", ".join("?" for _ in stale_pages)
            conn.execute(
                f"""
                DELETE FROM gallery_page_anchors
                WHERE query_key = ?
                  AND page_size = ?
                  AND gallery_version = ?
                  AND page IN ({placeholders})
                """,
                (
                    components.query_key,
                    components.page_size,
                    gallery_version,
                    *stale_pages,
                ),
            )
        conn.commit()
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.rollback()
        logger.debug("Failed to store gallery page anchor: %s", e)
    finally:
        conn.close()


def _get_gallery_rows_after_anchor_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    sort_seq: int,
    image_id: str,
    limit: int,
) -> list[sqlite3.Row]:
    cursor_where = _combine_gallery_where(
        components.where_sql,
        "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
    )
    return conn.execute(
        f"""
        SELECT {", ".join(GALLERY_COLUMNS)}
        FROM gallery_entries
        {cursor_where}
        ORDER BY sort_seq DESC, id DESC
        LIMIT ?
        """,
        (*components.params, sort_seq, sort_seq, image_id, limit),
    ).fetchall()


def _get_gallery_page_rows_by_anchor_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    *,
    page: int,
    limit: int,
    timings_ms: dict[str, float],
) -> list[sqlite3.Row]:
    anchor_started_at = time.perf_counter()
    gallery_version = _get_gallery_version_on_conn(conn)
    anchor = _get_gallery_anchor_for_page_on_conn(
        conn,
        components,
        gallery_version=gallery_version,
        page=page,
    )
    interval = max(1, int(GALLERY_PAGE_ANCHOR_INTERVAL_PAGES))
    anchored_by_offset = False
    anchor_gap = page - int(anchor["page"]) if anchor is not None else interval + 1
    if anchor is None or anchor_gap > interval:
        anchor_page = max(1, ((page - 1) // interval) * interval)
        if anchor_page >= page:
            anchor_page = max(1, page - 1)
        anchor_rows = _get_gallery_page_rows_by_offset_on_conn(
            conn,
            components,
            page=anchor_page,
            limit=1,
        )
        if not anchor_rows:
            timings_ms["anchor_ms"] = round(
                (time.perf_counter() - anchor_started_at) * 1000,
                2,
            )
            timings_ms["anchor_scan_rows"] = 0.0
            return []
        _store_gallery_page_anchor_best_effort(
            components,
            gallery_version=gallery_version,
            page=anchor_page,
            row=anchor_rows[0],
        )
        anchor_page = int(anchor_page)
        anchor_sort_seq = int(anchor_rows[0]["sort_seq"] or 0)
        anchor_id = str(anchor_rows[0]["id"])
        anchored_by_offset = True
    else:
        anchor_page = int(anchor["page"])
        anchor_sort_seq = int(anchor["sort_seq"] or 0)
        anchor_id = str(anchor["image_id"])

    page_delta = max(0, page - anchor_page)
    if page_delta == 0:
        anchor_row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            {_combine_gallery_where(components.where_sql, "sort_seq = ? AND id = ?")}
            LIMIT 1
            """,
            (*components.params, anchor_sort_seq, anchor_id),
        ).fetchone()
        scanned_rows = ([anchor_row] if anchor_row else []) + _get_gallery_rows_after_anchor_on_conn(
            conn,
            components,
            sort_seq=anchor_sort_seq,
            image_id=anchor_id,
            limit=max(0, limit - (1 if anchor_row else 0)),
        )
        result_rows = scanned_rows[:limit]
    else:
        rows_to_skip = page_delta * components.page_size - 1
        scan_limit = rows_to_skip + limit
        scanned_rows = _get_gallery_rows_after_anchor_on_conn(
            conn,
            components,
            sort_seq=anchor_sort_seq,
            image_id=anchor_id,
            limit=scan_limit,
        )
        result_rows = scanned_rows[rows_to_skip : rows_to_skip + limit]

    if result_rows:
        _store_gallery_page_anchor_best_effort(
            components,
            gallery_version=gallery_version,
            page=page,
            row=result_rows[0],
        )

    timings_ms["anchor_ms"] = round(
        (time.perf_counter() - anchor_started_at) * 1000,
        2,
    )
    timings_ms["anchor_scan_rows"] = float(len(scanned_rows))
    if anchored_by_offset:
        timings_ms["anchor_seeded_by_offset"] = 1.0
    return result_rows


def _get_gallery_row_batch_after_cursor_on_conn(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Sequence[Any],
    *,
    last_sort_seq: int | None,
    last_id: str | None,
    limit: int,
    columns: Sequence[str] = GALLERY_COLUMNS,
) -> list[sqlite3.Row]:
    if last_sort_seq is None or last_id is None:
        sql = f"""
            SELECT {", ".join(columns)}
            FROM gallery_entries
            {where_sql}
            ORDER BY sort_seq DESC, id DESC
            LIMIT ?
        """
        query_params = list(params) + [limit]
    else:
        combined_where = _combine_gallery_where(
            where_sql,
            "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
        )
        sql = f"""
            SELECT {", ".join(columns)}
            FROM gallery_entries
            {combined_where}
            ORDER BY sort_seq DESC, id DESC
            LIMIT ?
        """
        query_params = list(params) + [last_sort_seq, last_sort_seq, last_id, limit]
    return conn.execute(sql, query_params).fetchall()


def get_gallery(
    limit: int | None = None,
    offset: int | None = None,
    filters: dict[str, Any] | None = None,
) -> list[GalleryEntry]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        rows = _get_gallery_rows_on_conn(
            conn,
            where_sql,
            params,
            limit=limit,
            offset=offset,
        )
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, rows)
    return [
        GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))
        for row in rows
    ]


def iter_gallery_export_rows(
    filters: dict[str, Any] | None = None,
    *,
    batch_size: int = 200,
) -> Iterator[dict[str, Any]]:
    """Yield gallery entries as plain dicts for export use cases.

    Uses cursor-based (keyset) pagination to avoid O(n^2) OFFSET scanning.
    """
    _ensure_database()
    where_sql, params = _build_gallery_filter_where(filters)
    last_sort_seq: int | None = None
    last_id: str | None = None
    while True:
        with _connect() as conn:
            rows = _get_gallery_row_batch_after_cursor_on_conn(
                conn,
                where_sql,
                params,
                last_sort_seq=last_sort_seq,
                last_id=last_id,
                limit=batch_size,
            )
            thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(conn, rows)
        if not rows:
            return
        for row in rows:
            yield _gallery_entry_from_row(row, thumbnail_status_map)
        if len(rows) < batch_size:
            return
        last_row = rows[-1]
        last_sort_seq = int(last_row["sort_seq"] or 0)
        last_id = str(last_row["id"])


def _normalize_gallery_page_components(
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any] | None,
    include_total_bytes: bool,
    include_counts: bool,
    include_filter_options: bool,
    cursor: str | None,
    direction: str,
) -> _GalleryQueryComponents:
    requested_page = max(int(page), 1)
    normalized_page_size = max(int(page_size), 1)
    normalized_cursor = str(cursor or "").strip()
    normalized_direction = str(direction or "next").strip().lower()
    if normalized_direction not in {"next", "prev"}:
        raise ValueError("Invalid gallery cursor direction")

    decoded_cursor = (
        decode_gallery_cursor(normalized_cursor) if normalized_cursor else None
    )
    where_sql, params = _build_gallery_filter_where(filters)
    query_key = _gallery_query_key_from_components(where_sql, params)
    return _GalleryQueryComponents(
        where_sql=where_sql,
        params=params,
        query_key=query_key,
        requested_page=requested_page,
        page_size=normalized_page_size,
        include_counts=include_counts,
        include_filter_options=include_filter_options,
        include_total_bytes=include_total_bytes,
        decoded_cursor=decoded_cursor,
        direction=normalized_direction,
        has_filters=bool(where_sql),
    )


def _get_gallery_page_rows_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> _GalleryPaginationState:
    effective_page = components.requested_page
    total = 0
    total_pages = 1
    page_has_sentinel = False

    if components.decoded_cursor is None and components.include_counts:
        count_started_at = time.perf_counter()
        total = _get_gallery_count_on_conn(
            conn, components.where_sql, components.params
        )
        timings_ms["count_ms"] = round(
            (time.perf_counter() - count_started_at) * 1000,
            2,
        )
        total_pages = max((total + components.page_size - 1) // components.page_size, 1)
        effective_page = min(components.requested_page, total_pages)

    rows_started_at = time.perf_counter()
    if components.decoded_cursor is None:
        offset = (effective_page - 1) * components.page_size
        if (
            effective_page > 1
            and offset > GALLERY_PAGE_ANCHOR_SMALL_OFFSET_THRESHOLD
        ):
            rows = _get_gallery_page_rows_by_anchor_on_conn(
                conn,
                components,
                page=effective_page,
                limit=components.page_size + 1,
                timings_ms=timings_ms,
            )
        else:
            rows = _get_gallery_page_rows_by_offset_on_conn(
                conn,
                components,
                page=effective_page,
                limit=components.page_size + 1,
            )
        page_has_sentinel = len(rows) > components.page_size
        has_next = page_has_sentinel
        if has_next:
            rows = rows[: components.page_size]
        if components.include_counts:
            has_next = effective_page < total_pages
        has_prev = effective_page > 1
    else:
        cursor_sort_seq, cursor_id = components.decoded_cursor
        if components.direction == "prev":
            cursor_where = _combine_gallery_where(
                components.where_sql,
                "(sort_seq > ? OR (sort_seq = ? AND id > ?))",
            )
            raw_rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                {cursor_where}
                ORDER BY sort_seq ASC, id ASC
                LIMIT ?
                """,
                (
                    *components.params,
                    cursor_sort_seq,
                    cursor_sort_seq,
                    cursor_id,
                    components.page_size + 1,
                ),
            ).fetchall()
            has_prev = len(raw_rows) > components.page_size
            if has_prev:
                raw_rows = raw_rows[: components.page_size]
            rows = list(reversed(raw_rows))
            has_next = (
                False
                if not rows
                else _gallery_has_row_after_cursor(
                    conn, components.where_sql, components.params, rows[-1]
                )
            )
        else:
            cursor_where = _combine_gallery_where(
                components.where_sql,
                "(sort_seq < ? OR (sort_seq = ? AND id < ?))",
            )
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                {cursor_where}
                ORDER BY sort_seq DESC, id DESC
                LIMIT ?
                """,
                (
                    *components.params,
                    cursor_sort_seq,
                    cursor_sort_seq,
                    cursor_id,
                    components.page_size + 1,
                ),
            ).fetchall()
            has_next = len(rows) > components.page_size
            if has_next:
                rows = rows[: components.page_size]
            has_prev = (
                False
                if not rows
                else _gallery_has_row_before_cursor(
                    conn, components.where_sql, components.params, rows[0]
                )
            )
    timings_ms["rows_ms"] = round((time.perf_counter() - rows_started_at) * 1000, 2)

    if components.include_counts and components.decoded_cursor is not None:
        count_started_at = time.perf_counter()
        total = _get_gallery_count_on_conn(conn, components.where_sql, components.params)
        timings_ms["count_ms"] = round(
            (time.perf_counter() - count_started_at) * 1000,
            2,
        )
        total_pages = max((total + components.page_size - 1) // components.page_size, 1)
        effective_page = min(components.requested_page, total_pages)
    elif not components.include_counts:
        total_pages = max(components.requested_page + (1 if has_next else 0), 1)
        effective_page = components.requested_page

    if (
        components.decoded_cursor is None
        and components.requested_page == 1
        and not components.has_filters
    ):
        has_prev = False
        if not components.include_counts:
            has_next = page_has_sentinel

    return _GalleryPaginationState(
        rows=rows,
        has_prev=has_prev,
        has_next=has_next,
        effective_page=effective_page,
        total=total,
        total_pages=total_pages,
    )


def _get_gallery_page_total_bytes_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> int:
    if not components.include_total_bytes:
        return 0
    total_bytes_started_at = time.perf_counter()
    total_bytes = _get_gallery_total_bytes_on_conn(
        conn, components.where_sql, components.params
    )
    timings_ms["total_bytes_ms"] = round(
        (time.perf_counter() - total_bytes_started_at) * 1000,
        2,
    )
    return total_bytes


def _get_gallery_page_filter_options_on_conn(
    conn: sqlite3.Connection,
    components: _GalleryQueryComponents,
    timings_ms: dict[str, float],
) -> GalleryFilterOptions:
    if not components.include_filter_options:
        return GalleryFilterOptions()
    filter_options_started_at = time.perf_counter()
    filter_options = _get_gallery_filter_options_on_conn(conn)
    timings_ms["filter_options_ms"] = round(
        (time.perf_counter() - filter_options_started_at) * 1000,
        2,
    )
    return filter_options


def get_gallery_page(
    *,
    page: int = 1,
    page_size: int = 9,
    filters: dict[str, Any] | None = None,
    include_total_bytes: bool = False,
    include_counts: bool = True,
    include_filter_options: bool = True,
    cursor: str | None = None,
    direction: str = "next",
) -> GalleryPage:
    _ensure_database()
    query_started_at = time.perf_counter()
    timings_ms: dict[str, float] = {
        "rows_ms": 0.0,
        "count_ms": 0.0,
        "total_bytes_ms": 0.0,
        "filter_options_ms": 0.0,
    }
    with _connect() as conn:
        components = _normalize_gallery_page_components(
            page=page,
            page_size=page_size,
            filters=filters,
            include_total_bytes=include_total_bytes,
            include_counts=include_counts,
            include_filter_options=include_filter_options,
            cursor=cursor,
            direction=direction,
        )
        pagination = _get_gallery_page_rows_on_conn(conn, components, timings_ms)
        total_bytes = _get_gallery_page_total_bytes_on_conn(conn, components, timings_ms)
        filter_options = _get_gallery_page_filter_options_on_conn(conn, components, timings_ms)
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, pagination.rows
        )
        prev_cursor = (
            _gallery_cursor_from_row(pagination.rows[0]) if pagination.rows and pagination.has_prev else None
        )
        next_cursor = (
            _gallery_cursor_from_row(pagination.rows[-1]) if pagination.rows and pagination.has_next else None
        )
        images = [
            GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))
            for row in pagination.rows
        ]
    query_elapsed_ms = (time.perf_counter() - query_started_at) * 1000

    return GalleryPage(
        total=pagination.total,
        total_bytes=total_bytes,
        page=pagination.effective_page,
        page_size=components.page_size,
        total_pages=pagination.total_pages,
        has_prev=pagination.has_prev,
        has_next=pagination.has_next,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        images=images,
        filter_options=filter_options,
        query_elapsed_ms=round(query_elapsed_ms, 2),
        timings_ms=timings_ms,
        counts_included=include_counts,
        filter_options_included=include_filter_options,
    )


def get_gallery_entry(image_id: str) -> GalleryEntry | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            WHERE id = ?
            """,
            (image_id,),
        ).fetchone()
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, [row] if row else []
        )
    if not row:
        return None
    return GalleryEntry(**_gallery_entry_from_row(row, thumbnail_status_map))


def get_gallery_entries_by_ids(image_ids: Sequence[str]) -> list[GalleryEntry]:
    """Fetch gallery entries for many ids in one query, preserving input order.

    Duplicate or missing ids are dropped.
    """
    _ensure_database()
    unique_ids = _unique_sqlite_values(image_ids)
    if not unique_ids:
        return []

    rows_by_id: dict[str, sqlite3.Row] = {}
    with _connect() as conn:
        for chunk in _iter_sqlite_in_chunks(unique_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                WHERE id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            rows_by_id.update({row["id"]: row for row in rows})
        thumbnail_status_map = _get_gallery_thumbnail_status_map_on_conn(
            conn, rows_by_id.values()
        )

    return [
        GalleryEntry(**_gallery_entry_from_row(rows_by_id[image_id], thumbnail_status_map))
        for image_id in unique_ids
        if image_id in rows_by_id
    ]


def _get_all_filenames_on_conn(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT filename FROM gallery_entries WHERE filename IS NOT NULL"
    ).fetchall()
    return [row["filename"] for row in rows if row["filename"]]


def get_all_filenames() -> list[str]:
    """Return all filenames in the gallery without loading full entry objects."""
    _ensure_database()
    with _connect() as conn:
        return _get_all_filenames_on_conn(conn)


def get_all_gallery_ids() -> list[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM gallery_entries").fetchall()
        return [row["id"] for row in rows if row["id"]]


__all__ = [name for name in globals() if not name.startswith("__")]
