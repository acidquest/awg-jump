import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSystemStatus, restartRouting, triggerGeoipUpdate } from '../api'
import { SystemStatus } from '../types'
import StatusBadge from '../components/StatusBadge'

function fmtUptime(sec: number) {
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtBytes(bytes: number | null | undefined) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function fmtHandshake(ts: string | null) {
  if (!ts) return 'never'
  const diff = Date.now() - new Date(ts).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  return `${h}h ago`
}

function NodeStatusDot({ status }: { status: string }) {
  const color = status === 'online' ? 'green' : status === 'degraded' ? 'yellow' : 'red'
  return <span className={`pulse-dot ${color}`} />
}

function fmtLatency(latencyMs: number | null | undefined, status?: string | null) {
  if (latencyMs != null) return `${latencyMs.toFixed(0)} ms`
  if (status && ['pending', 'online', 'degraded'].includes(status)) return 'probing...'
  return '—'
}

export default function Dashboard() {
  const qc = useQueryClient()

  const { data, isLoading } = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: () => getSystemStatus().then((r) => r.data),
    refetchInterval: 30_000,
  })

  const restartMut = useMutation({
    mutationFn: restartRouting,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system-status'] }),
  })

  const geoipMut = useMutation({
    mutationFn: triggerGeoipUpdate,
  })

  if (isLoading) return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>

  const s = data!

  const awg0 = s.interfaces.find((i) => i.name === 'awg0')
  const awg1 = s.interfaces.find((i) => i.name === 'awg1')
  const totalPrefixes = s.geoip.reduce((a, g) => a + (g.prefix_count || 0), 0)

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Dashboard</div>
          <div className="page-subtitle">System overview</div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => geoipMut.mutate()}
            disabled={geoipMut.isPending}
          >
            {geoipMut.isPending ? <span className="spinner" /> : null}
            Update GeoIP
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => restartMut.mutate()}
            disabled={restartMut.isPending}
          >
            {restartMut.isPending ? <span className="spinner" /> : null}
            Restart Routing
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="card-grid card-grid-4 mb-4" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>Uptime</div>
          <div className="stat-value text-accent">{fmtUptime(s.uptime_seconds)}</div>
          <div className="stat-label">process uptime</div>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>GeoIP prefixes</div>
          <div className="stat-value">{totalPrefixes.toLocaleString()}</div>
          <div className="stat-label">{s.geoip.map((g) => g.country_code).join(', ')} routes</div>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>Active peers</div>
          <div className="stat-value">{awg0?.peers_count ?? 0}</div>
          <div className="stat-label">on awg0</div>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>Active node</div>
          {s.active_node ? (
            <>
              <div className="stat-value" style={{ fontSize: 18 }}>
                <NodeStatusDot status={s.active_node.status} />
                {' '}{s.active_node.name}
              </div>
              <div className="stat-label">
                {fmtLatency(s.active_node.latency_ms, s.active_node.status)}
              </div>
            </>
          ) : (
            <>
              <div className="stat-value text-muted">—</div>
              <div className="stat-label">no active node</div>
            </>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>
        {/* AWG0 card */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">awg0 — Server</span>
            <StatusBadge status={awg0?.running ? 'up' : 'down'} />
          </div>
          <div className="flex gap-4" style={{ fontSize: 13 }}>
            <div><span className="text-muted">Address:</span>{' '}
              <span className="text-mono">{awg0?.address ?? '—'}</span></div>
            <div><span className="text-muted">Peers:</span>{' '}{awg0?.peers_count ?? 0}</div>
          </div>
        </div>

        {/* AWG1 card */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">awg1 — Upstream VPN</span>
            <StatusBadge status={awg1?.running ? 'up' : 'down'} />
          </div>
          <div className="flex gap-4" style={{ fontSize: 13 }}>
            <div><span className="text-muted">Address:</span>{' '}
              <span className="text-mono">{awg1?.address ?? '—'}</span></div>
            {s.active_node && (
              <div>
                <span className="text-muted">Node:</span>{' '}
                <span style={{ color: 'var(--accent)' }}>{s.active_node.name}</span>
                {' '}
                <StatusBadge status={s.active_node.status} />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* GeoIP sources */}
      <div className="section">
        <div className="section-title">GeoIP Sources</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Country</th>
                <th>IPSet</th>
                <th>Prefixes</th>
                <th>Last updated</th>
                <th>Cache</th>
              </tr>
            </thead>
            <tbody>
              {s.geoip.length === 0 ? (
                <tr><td colSpan={5} className="text-muted" style={{ textAlign: 'center' }}>No sources</td></tr>
              ) : s.geoip.map((g, i) => (
                <tr key={i}>
                  <td><span className="badge badge-unknown">{g.country_code}</span></td>
                  <td className="text-mono">{g.ipset_name}</td>
                  <td>{g.prefix_count?.toLocaleString() ?? 0}</td>
                  <td className="text-muted" style={{ fontSize: 12 }}>
                    {g.last_updated ? new Date(g.last_updated).toLocaleString() : 'never'}
                  </td>
                  <td>
                    <StatusBadge status={g.cache_fresh ? 'online' : 'offline'} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Routing status */}
      <div className="section">
        <div className="section-title">Routing Status</div>
        <div className="card">
          <pre className="mono text-muted" style={{ fontSize: 11, overflowX: 'auto' }}>
            {JSON.stringify(s.routing, null, 2)}
          </pre>
        </div>
      </div>
    </>
  )
}
