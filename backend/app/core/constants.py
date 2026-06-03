ACTIVE_GENERATE_JOB_STATUSES: frozenset[str] = frozenset({"queued", "running"})
ERROR_GENERATE_JOB_STATUSES: frozenset[str] = frozenset({"error", "upstream_error"})
