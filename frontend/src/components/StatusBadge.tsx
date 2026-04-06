type Status = string

const MAP: Record<string, string> = {
  online: 'badge-online',
  up: 'badge-up',
  offline: 'badge-offline',
  down: 'badge-down',
  error: 'badge-error',
  degraded: 'badge-degraded',
  warning: 'badge-warning',
  pending: 'badge-pending',
  deploying: 'badge-deploying',
  running: 'badge-pending',
  success: 'badge-online',
  failed: 'badge-error',
}

export default function StatusBadge({ status }: { status: Status }) {
  const cls = MAP[status?.toLowerCase()] ?? 'badge-unknown'
  return <span className={`badge ${cls}`}>{status}</span>
}
