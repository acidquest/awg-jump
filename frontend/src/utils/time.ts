const CLIENT_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone

function normalizeUtcTimestamp(value: string) {
  return /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`
}

export function parseUtcDate(value: string | null | undefined) {
  if (!value) return null
  const date = new Date(normalizeUtcTimestamp(value))
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTimeLocal(value: string | null | undefined) {
  const date = parseUtcDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: CLIENT_TIME_ZONE,
  }).format(date)
}

export function formatTimeLocal(value: string | null | undefined, withSeconds = false) {
  const date = parseUtcDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: withSeconds ? '2-digit' : undefined,
    timeZone: CLIENT_TIME_ZONE,
  }).format(date)
}

export function formatDateLocal(value: string | null | undefined) {
  const date = parseUtcDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeZone: CLIENT_TIME_ZONE,
  }).format(date)
}
