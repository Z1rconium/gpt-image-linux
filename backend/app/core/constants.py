ACTIVE_GENERATE_JOB_STATUSES: frozenset[str] = frozenset({"queued", "running"})
ERROR_GENERATE_JOB_STATUSES: frozenset[str] = frozenset(
    {"partial_failure", "error", "upstream_error"}
)
