"""Gallery repository API.

storage.py keeps the legacy facade. New code can import gallery operations from
this module while the implementation is migrated incrementally.
"""

from .storage import (  # noqa: F401
    GalleryPage,
    add_to_gallery_async,
    add_to_gallery_sync,
    backfill_missing_gallery_bytes,
    count_gallery_r2_sync_rows,
    decode_gallery_cursor,
    delete_all_gallery_images,
    delete_gallery_image,
    delete_gallery_images,
    encode_gallery_cursor,
    get_all_filenames,
    get_all_gallery_ids,
    get_gallery,
    get_gallery_count,
    get_gallery_entries_by_ids,
    get_gallery_entry,
    get_gallery_filter_options,
    get_gallery_page,
    get_gallery_total_bytes,
    image_url_for_filename,
    import_gallery_entries,
    is_gallery_filename_referenced,
    iter_gallery_export_rows,
    iter_gallery_r2_sync_rows,
    mark_gallery_r2_sync_state,
    rebuild_gallery_filter_options,
    sync_gallery_with_image_files,
    update_gallery_entries_favorite,
    update_gallery_entry,
    update_gallery_entry_hash,
)
