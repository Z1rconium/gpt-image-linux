"""Gallery filter option maintenance and reads."""

from ..db import *


def rebuild_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _rebuild_gallery_filter_options_on_conn(conn)
        return _get_gallery_filter_options_on_conn(conn)


def _get_gallery_filter_options_on_conn(conn: sqlite3.Connection) -> GalleryFilterOptions:
    global _filter_options_cache
    cache_version = _get_filter_options_cache_version()
    with _filter_options_cache_lock:
        cached = _filter_options_cache
        if cached is not None and cached.version == cache_version:
            return cached.options

    options: dict[str, list[str]] = {}
    for key, kind in (
        ("models", "model"),
        ("presets", "preset"),
        ("sizes", "size"),
    ):
        rows = conn.execute(
            """
            SELECT value
            FROM gallery_filter_options
            WHERE kind = ? AND ref_count > 0
            ORDER BY LOWER(value) ASC
            """,
            (kind,),
        ).fetchall()
        options[key] = [row["value"] for row in rows if row["value"]]

    result = GalleryFilterOptions(**options)
    with _filter_options_cache_lock:
        _filter_options_cache = _GalleryFilterOptionsCacheEntry(
            version=cache_version,
            options=result,
        )
    return result


def get_gallery_filter_options() -> GalleryFilterOptions:
    _ensure_database()
    with _connect() as conn:
        return _get_gallery_filter_options_on_conn(conn)


__all__ = [name for name in globals() if not name.startswith("_")]

