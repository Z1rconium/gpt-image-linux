const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);
const FAILURE_JOB_STATUSES = new Set(['partial_failure', 'error', 'cancelled', 'interrupted', 'upstream_error']);

export function isActiveJobStatus(status: string | null | undefined) {
  return Boolean(status && ACTIVE_JOB_STATUSES.has(status));
}

export function isFailureJobStatus(status: string | null | undefined) {
  return Boolean(status && FAILURE_JOB_STATUSES.has(status));
}
