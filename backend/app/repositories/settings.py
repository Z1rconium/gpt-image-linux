"""Settings, assistant metadata, and prompt snippet persistence."""

from .db import *


def _migrate_credential_reference(
    value: object,
    *,
    purpose: str,
    target_url: str,
    host_allowlist: str,
) -> tuple[str, str]:
    normalized = str(value or "").strip()
    if not normalized:
        return "", "unchanged"
    if normalized in configured_secret_ids() or get_env_var_ref_name(normalized):
        return normalized, "unchanged"
    matched_id = match_secret_id_for_value(
        normalized,
        purpose=purpose,
        target_url=target_url,
        host_allowlist=host_allowlist,
    )
    return (matched_id, "replaced") if matched_id else ("", "cleared")


def _load_json_object(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def migrate_legacy_secret_references() -> dict[str, dict[str, int]]:
    """Atomically replace or clear legacy literal credentials in persisted settings."""

    _ensure_database()
    counts: dict[str, dict[str, int]] = {}

    def record(category: str, outcome: str, amount: int = 1) -> None:
        if outcome == "unchanged":
            return
        category_counts = counts.setdefault(
            category,
            {"replaced": 0, "cleared": 0, "disabled": 0},
        )
        category_counts[outcome] += amount

    with _connect() as conn:
        with _transaction(conn):
            preset_rows = conn.execute(
                "SELECT id, api_url, api_key FROM api_presets ORDER BY id"
            ).fetchall()
            for row in preset_rows:
                migrated, outcome = _migrate_credential_reference(
                    row["api_key"],
                    purpose="upstream_api",
                    target_url=str(row["api_url"] or ""),
                    host_allowlist=config.UPSTREAM_HOST_ALLOWLIST,
                )
                if outcome != "unchanged":
                    conn.execute(
                        "UPDATE api_presets SET api_key = ?, updated_at = ? WHERE id = ?",
                        (migrated, utc_now(), row["id"]),
                    )
                    record("api_presets", outcome)

            for key, purpose, allowlist, category in (
                (
                    UPSTREAM_SOCKS5_PROXY_KEY,
                    "upstream_proxy",
                    config.UPSTREAM_PROXY_HOST_ALLOWLIST,
                    "upstream_proxy",
                ),
                (
                    WEBHOOK_URL_KEY,
                    "webhook_url",
                    config.WEBHOOK_HOST_ALLOWLIST,
                    "webhook",
                ),
            ):
                raw = _get_setting_value(conn, key)
                migrated, outcome = _migrate_credential_reference(
                    raw,
                    purpose=purpose,
                    target_url=str(raw or ""),
                    host_allowlist=allowlist,
                )
                if outcome != "unchanged":
                    _set_setting_value(conn, key, migrated)
                    record(category, outcome)

            optimizer = _load_json_object(
                _get_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY)
            )
            optimizer_cleared = False
            if optimizer is not None:
                migrated, outcome = _migrate_credential_reference(
                    optimizer.get("api_key"),
                    purpose="prompt_optimizer",
                    target_url=str(optimizer.get("api_url") or ""),
                    host_allowlist=config.PROMPT_OPTIMIZER_HOST_ALLOWLIST,
                )
                if outcome != "unchanged":
                    optimizer["api_key"] = migrated
                    record("prompt_optimizer", outcome)
                    if outcome == "cleared":
                        optimizer["enabled"] = False
                        optimizer_cleared = True
                        record("prompt_optimizer", "disabled")
                    _set_setting_value(
                        conn,
                        PROMPT_OPTIMIZER_SETTINGS_KEY,
                        json.dumps(optimizer),
                    )

            if optimizer_cleared:
                assistant = _load_json_object(
                    _get_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY)
                ) or {}
                if assistant.get("enabled") is not False:
                    assistant["enabled"] = False
                    _set_setting_value(
                        conn,
                        AI_ASSISTANT_SETTINGS_KEY,
                        json.dumps(assistant),
                    )
                    record("ai_assistant", "disabled")

            r2 = _load_json_object(_get_setting_value(conn, R2_BACKUP_SETTINGS_KEY))
            if r2 is not None:
                r2_cleared = False
                for field, purpose in (
                    ("access_key_id", "r2_access_key_id"),
                    ("secret_access_key", "r2_secret_access_key"),
                ):
                    migrated, outcome = _migrate_credential_reference(
                        r2.get(field),
                        purpose=purpose,
                        target_url=str(r2.get("endpoint_url") or ""),
                        host_allowlist=config.R2_ENDPOINT_HOST_ALLOWLIST,
                    )
                    if outcome != "unchanged":
                        r2[field] = migrated
                        record("r2", outcome)
                        r2_cleared = r2_cleared or outcome == "cleared"
                if r2_cleared:
                    r2["enabled"] = False
                    r2[CREDENTIAL_MIGRATION_CLEARED_KEY] = True
                    record("r2", "disabled")
                if r2_cleared or any(
                    counts.get("r2", {}).get(key, 0) for key in ("replaced", "cleared")
                ):
                    _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(r2))

            nodeimage = _load_json_object(
                _get_setting_value(conn, NODEIMAGE_SETTINGS_KEY)
            )
            if nodeimage is not None:
                migrated, outcome = _migrate_credential_reference(
                    nodeimage.get("api_key"),
                    purpose="nodeimage_api_key",
                    target_url="https://api.nodeimage.com",
                    host_allowlist="api.nodeimage.com",
                )
                if outcome != "unchanged":
                    nodeimage["api_key"] = migrated
                    record("nodeimage", outcome)
                    if outcome == "cleared":
                        nodeimage["enabled"] = False
                        nodeimage[CREDENTIAL_MIGRATION_CLEARED_KEY] = True
                        record("nodeimage", "disabled")
                    _set_setting_value(conn, NODEIMAGE_SETTINGS_KEY, json.dumps(nodeimage))

    if counts:
        _secure_data_storage_permissions()
    for category, category_counts in sorted(counts.items()):
        logger.info(
            "Credential migration category=%s replaced=%d cleared=%d disabled=%d",
            category,
            category_counts["replaced"],
            category_counts["cleared"],
            category_counts["disabled"],
        )
    return counts


def load_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        settings = _load_settings_from_conn(conn)
        if settings:
            return settings

        settings = _default_settings()
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)
        _secure_data_storage_permissions()
        return settings


def save_settings(settings: dict):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)
    _secure_data_storage_permissions()


def load_prompt_optimizer_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        raw = _get_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY)
        if raw:
            try:
                return _normalize_prompt_optimizer_settings(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return _default_prompt_optimizer_settings()
        return _default_prompt_optimizer_settings()


def save_prompt_optimizer_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_prompt_optimizer_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, PROMPT_OPTIMIZER_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def load_ai_assistant_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        raw = _get_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY)
        if raw:
            try:
                return _normalize_ai_assistant_settings(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                return _default_ai_assistant_settings()
        return _default_ai_assistant_settings()


def save_ai_assistant_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_ai_assistant_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, AI_ASSISTANT_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def _gallery_ai_metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
    analysis = _json_loads_dict(row["analysis_json"])
    return {
        "image_id": str(row["image_id"]),
        "description": str(row["description"] or ""),
        "prompt": str(row["prompt"] or ""),
        "analysis": analysis,
        "model": str(row["model"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def get_gallery_ai_metadata(image_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT image_id, description, prompt, analysis_json, model, created_at, updated_at
            FROM gallery_ai_metadata
            WHERE image_id = ?
            """,
            (image_id,),
        ).fetchone()
    return _gallery_ai_metadata_from_row(row) if row else None


def upsert_gallery_ai_metadata(
    *,
    image_id: str,
    description: str = "",
    prompt: str = "",
    analysis: dict[str, Any] | None = None,
    model: str = "",
) -> dict[str, Any]:
    _ensure_database()
    now = utc_now()
    normalized_analysis = analysis if isinstance(analysis, dict) else {}
    normalized_description = str(description or "")[:2000]
    normalized_prompt = str(prompt or "")[:4000]
    analysis_json = json.dumps(normalized_analysis, ensure_ascii=False, sort_keys=True)[:12000]
    with _connect() as conn:
        with _transaction(conn):
            exists = conn.execute(
                "SELECT 1 FROM gallery_entries WHERE id = ? LIMIT 1",
                (image_id,),
            ).fetchone()
            if not exists:
                raise KeyError("Gallery entry not found")
            conn.execute(
                """
                INSERT INTO gallery_ai_metadata (
                    image_id,
                    description,
                    prompt,
                    analysis_json,
                    model,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_id) DO UPDATE SET
                    description = excluded.description,
                    prompt = excluded.prompt,
                    analysis_json = excluded.analysis_json,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (
                    image_id,
                    normalized_description,
                    normalized_prompt,
                    analysis_json,
                    str(model or ""),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT image_id, description, prompt, analysis_json, model, created_at, updated_at
                FROM gallery_ai_metadata
                WHERE image_id = ?
                """,
                (image_id,),
            ).fetchone()
    return _gallery_ai_metadata_from_row(row)


def load_r2_backup_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        return _load_r2_backup_settings_from_conn(conn)


def save_r2_backup_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_r2_backup_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, R2_BACKUP_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def load_nodeimage_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        return _load_nodeimage_settings_from_conn(conn)


def get_nodeimage_settings() -> dict:
    return load_nodeimage_settings()


def save_nodeimage_settings(settings: dict):
    _ensure_database()
    normalized = _normalize_nodeimage_settings(settings)
    with _connect() as conn:
        _set_setting_value(conn, NODEIMAGE_SETTINGS_KEY, json.dumps(normalized))
        conn.commit()
    _secure_data_storage_permissions()


def list_prompt_snippets(query: str = "") -> list[PromptSnippet]:
    _ensure_database()
    normalized_query = str(query or "").strip()
    with _connect() as conn:
        params: list[Any] = []
        where_sql = ""
        if normalized_query:
            where_sql = """
                WHERE title COLLATE NOCASE LIKE ? ESCAPE '\\'
                   OR prompt COLLATE NOCASE LIKE ? ESCAPE '\\'
            """
            like_query = _like_prompt_snippet_query(normalized_query)
            params.extend([like_query, like_query])
        rows = conn.execute(
            f"""
            SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
            FROM prompt_snippets
            {where_sql}
            ORDER BY favorite DESC, updated_at DESC, rowid DESC
            """,
            tuple(params),
        ).fetchall()
    return [_prompt_snippet_from_row(row) for row in rows]


def create_prompt_snippet(
    *,
    title: str,
    prompt: str,
    favorite: bool = False,
) -> PromptSnippet:
    _ensure_database()
    now = utc_now()
    with _connect() as conn:
        with _transaction(conn):
            for _ in range(5):
                snippet_id = _generate_prompt_snippet_id()
                try:
                    conn.execute(
                        """
                        INSERT INTO prompt_snippets (
                            id,
                            title,
                            prompt,
                            favorite,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snippet_id,
                            title,
                            prompt,
                            _normalize_prompt_snippet_favorite(favorite),
                            now,
                            now,
                        ),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise RuntimeError("Failed to generate a unique prompt snippet id")

            row = conn.execute(
                f"""
                SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                FROM prompt_snippets
                WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()

    return _prompt_snippet_from_row(row)


def update_prompt_snippet(
    snippet_id: str,
    updates: dict[str, Any],
) -> PromptSnippet | None:
    _ensure_database()
    allowed_updates = {
        key: _normalize_prompt_snippet_favorite(value) if key == "favorite" else value
        for key, value in updates.items()
        if key in {"title", "prompt", "favorite"} and value is not None
    }

    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                FROM prompt_snippets
                WHERE id = ?
                """,
                (snippet_id,),
            ).fetchone()
            if not row:
                return None

            if allowed_updates:
                now = utc_now()
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"""
                    UPDATE prompt_snippets
                    SET {assignments}, updated_at = ?
                    WHERE id = ?
                    """,
                    (*allowed_updates.values(), now, snippet_id),
                )
                row = conn.execute(
                    f"""
                    SELECT {", ".join(PROMPT_SNIPPET_COLUMNS)}
                    FROM prompt_snippets
                    WHERE id = ?
                    """,
                    (snippet_id,),
                ).fetchone()

    return _prompt_snippet_from_row(row)


def delete_prompt_snippet(snippet_id: str) -> bool:
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            cursor = conn.execute(
                "DELETE FROM prompt_snippets WHERE id = ?",
                (snippet_id,),
            )
            return cursor.rowcount > 0


__all__ = [name for name in globals() if not name.startswith("_")]
