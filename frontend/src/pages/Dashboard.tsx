import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useState } from 'react'
import { getRoutingStatus, getSystemMetrics, getSystemStatus, restartRouting, triggerGeoipUpdate } from '../api'
import { RoutingStatus, SystemMetricsResponse, SystemStatus } from '../types'
import StatusBadge from '../components/StatusBadge'
import { formatDateTimeLocal, formatTimeLocal, parseUtcDate } from '../utils/time'

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
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function fmtPercent(value: number | null | undefined) {
  return `${(value ?? 0).toFixed(1)}%`
}

function fmtMetricTime(ts: string, period: '1h' | '24h') {
  void period
  return formatTimeLocal(ts)
}

function fmtHandshake(ts: string | null) {
  if (!ts) return 'never'
  const date = parseUtcDate(ts)
  if (!date) return 'never'
  const diff = Date.now() - date.getTime()
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
  const [metricsPeriod, setMetricsPeriod] = useState<'1h' | '24h'>('1h')

  const { data, isLoading, isError, error } = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: () => getSystemStatus().then((r) => r.data),
    refetchInterval: 30_000,
  })

  const { data: routingData } = useQuery<RoutingStatus>({
    queryKey: ['routing'],
    queryFn: () => getRoutingStatus().then((r) => r.data),
    refetchInterval: 30_000,
  })

  const { data: metricsData } = useQuery<SystemMetricsResponse>({
    queryKey: ['system-metrics', metricsPeriod],
    queryFn: () => getSystemMetrics(metricsPeriod).then((r) => r.data),
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
  if (isError || !data) {
    const message =
      (error as { response?: { data?: { detail?: string } }; message?: string })?.response?.data?.detail
      ?? (error as Error | undefined)?.message
      ?? 'Failed to load dashboard data'
    return (
      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-title" style={{ marginBottom: 12 }}>Dashboard unavailable</div>
        <div className="error-box" style={{ marginBottom: 12 }}>{message}</div>
        <button className="btn btn-secondary" onClick={() => qc.invalidateQueries({ queryKey: ['system-status'] })}>
          Retry
        </button>
      </div>
    )
  }

  const s = data

  const awg0 = s.interfaces.find((i) => i.name === 'awg0')
  const awg1 = s.interfaces.find((i) => i.name === 'awg1')
  const totalPrefixes = s.geoip.reduce((a, g) => a + (g.prefix_count || 0), 0)
  const routing = routingData ?? s.routing
  const latestMetrics = metricsData?.latest
  const metricPoints = metricsData?.points.map((point) => ({
    ...point,
    label: fmtMetricTime(point.collected_at, metricsPeriod),
  })) ?? []

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
              <div className="stat-label" style={{ marginTop: 8 }}>
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

      <div className="section">
        <div className="section-title">
          <span>System Load</span>
          <div className="segmented-control" role="tablist" aria-label="Metrics period">
            <button
              className={`segmented-btn${metricsPeriod === '1h' ? ' active' : ''}`}
              onClick={() => setMetricsPeriod('1h')}
              type="button"
            >
              1h
            </button>
            <button
              className={`segmented-btn${metricsPeriod === '24h' ? ' active' : ''}`}
              onClick={() => setMetricsPeriod('24h')}
              type="button"
            >
              24h
            </button>
          </div>
        </div>
        <div className="card-grid card-grid-2 system-metrics-grid">
          <div className="card metric-card">
            <div className="metric-card-header">
              <div>
                <div className="card-title" style={{ marginBottom: 10 }}>CPU Load</div>
                <div className="stat-value text-accent">{fmtPercent(latestMetrics?.cpu_usage_percent)}</div>
                <div className="stat-label">sampled every minute, retained for 24 hours</div>
              </div>
              <div className="metric-chip">host CPU</div>
            </div>
            <div className="metric-chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={metricPoints}>
                  <CartesianGrid stroke="rgba(139, 148, 158, 0.12)" vertical={false} />
                  <XAxis
                    dataKey="label"
                    minTickGap={28}
                    stroke="var(--text-3)"
                    tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tickFormatter={(value) => `${value}%`}
                    stroke="var(--text-3)"
                    tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                    width={42}
                  />
                  <Tooltip content={<MetricsTooltip type="cpu" />} />
                  <Line
                    type="monotone"
                    dataKey="cpu_usage_percent"
                    stroke="var(--accent)"
                    strokeWidth={2.5}
                    dot={false}
                    activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--accent)' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="card metric-card">
            <div className="metric-card-header">
              <div>
                <div className="card-title" style={{ marginBottom: 10 }}>Memory</div>
                <div className="stat-value">{fmtBytes(latestMetrics?.memory_used_bytes)}</div>
                <div className="stat-label">
                  free {fmtBytes(latestMetrics?.memory_free_bytes)} of {fmtBytes(latestMetrics?.memory_total_bytes)}
                </div>
              </div>
              <div className="metric-chip">RAM</div>
            </div>
            <div className="metric-chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={metricPoints}>
                  <defs>
                    <linearGradient id="memoryUsedFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="rgba(0, 212, 170, 0.45)" />
                      <stop offset="100%" stopColor="rgba(0, 212, 170, 0.04)" />
                    </linearGradient>
                    <linearGradient id="memoryFreeFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="rgba(56, 189, 248, 0.30)" />
                      <stop offset="100%" stopColor="rgba(56, 189, 248, 0.03)" />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(139, 148, 158, 0.12)" vertical={false} />
                  <XAxis
                    dataKey="label"
                    minTickGap={28}
                    stroke="var(--text-3)"
                    tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                  />
                  <YAxis
                    tickFormatter={(value) => fmtBytes(value)}
                    stroke="var(--text-3)"
                    tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                    width={56}
                  />
                  <Tooltip content={<MetricsTooltip type="memory" />} />
                  <Area
                    type="monotone"
                    dataKey="memory_free_bytes"
                    stackId="memory"
                    stroke="#38bdf8"
                    fill="url(#memoryFreeFill)"
                    strokeWidth={2}
                    dot={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="memory_used_bytes"
                    stackId="memory"
                    stroke="var(--accent)"
                    fill="url(#memoryUsedFill)"
                    strokeWidth={2}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
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
                    {g.last_updated ? formatDateTimeLocal(g.last_updated) : 'never'}
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
        <div className="section-title">Routing Diagram</div>
        {'error' in routing && routing.error ? (
          <div className="error-box">{routing.error}</div>
        ) : (
          <RoutingDiagram
            routing={routing}
            localExternalIp={s.local_external_ip}
            activeNodeName={s.active_node?.name ?? null}
            activeNodeExternalIp={s.active_node?.external_ip ?? null}
          />
        )}
      </div>
    </>
  )
}

function MetricsTooltip({
  active,
  payload,
  label,
  type,
}: {
  active?: boolean
  payload?: Array<{ dataKey?: string; value?: number; payload?: { collected_at: string } }>
  label?: string
  type: 'cpu' | 'memory'
}) {
  if (!active || !payload?.length) return null

  const ts = payload[0]?.payload?.collected_at

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">
        {ts ? formatDateTimeLocal(ts) : label}
      </div>
      {type === 'cpu' ? (
        <div className="chart-tooltip-row">
          <span className="chart-swatch cpu" />
          CPU {fmtPercent(Number(payload[0]?.value ?? 0))}
        </div>
      ) : (
        <>
          {payload
            .slice()
            .reverse()
            .map((entry) => (
              <div key={entry.dataKey} className="chart-tooltip-row">
                <span className={`chart-swatch ${entry.dataKey === 'memory_used_bytes' ? 'used' : 'free'}`} />
                {entry.dataKey === 'memory_used_bytes' ? 'Used' : 'Free'} {fmtBytes(Number(entry.value ?? 0))}
              </div>
            ))}
        </>
      )}
    </div>
  )
}

function RoutingDiagram({
  routing,
  localExternalIp,
  activeNodeName,
  activeNodeExternalIp,
}: {
  routing: Partial<RoutingStatus>
  localExternalIp: string | null
  activeNodeName: string | null
  activeNodeExternalIp: string | null
}) {
  const physicalIface = routing.physical_iface ?? 'eth0'
  const localZoneLabel = routing.geoip_destination === 'local' ? 'GeoIP local zone' : 'Other traffic'
  const vpnZoneLabel = routing.geoip_destination === 'vpn' ? 'GeoIP local zone' : 'Other traffic'
  const localMark = routing.geoip_destination === 'local' ? routing.geoip_mark : routing.other_mark
  const vpnMark = routing.geoip_destination === 'vpn' ? routing.geoip_mark : routing.other_mark
  const localMarked = routing.geoip_destination === 'local' ? routing.prerouting_geoip : routing.prerouting_other
  const vpnMarked = routing.geoip_destination === 'vpn' ? routing.prerouting_geoip : routing.prerouting_other

  return (
    <div className="routing-diagram">
      <div className="routing-diagram-header">
        <div>
          <div className="routing-diagram-title">Live traffic map</div>
          <div className="routing-diagram-subtitle">
            {routing.invert_geoip ? 'Inverted mode: GeoIP zone goes to upstream VPN, other traffic goes directly to the local interface.' : 'Normal mode: GeoIP zone goes directly to the local interface, other traffic goes to upstream VPN.'}
          </div>
        </div>
        <div className="routing-diagram-mode">
          <span className={`badge ${routing.invert_geoip ? 'badge-warning' : 'badge-online'}`}>
            {routing.invert_geoip ? 'inverted' : 'normal'}
          </span>
        </div>
      </div>

      <div className="routing-diagram-main">
        <TrafficNode
          icon={<ClientIcon />}
          title="Clients"
          meta="AWG peers"
        />
        <TrafficArrow />
        <TrafficNode
          icon={<ServerIcon />}
          title="awg0"
          meta="entry interface"
          accent
        />
        <TrafficArrow />
        <TrafficNode
          icon={<PolicyIcon />}
          title="Routing split"
          meta="policy routing"
        />

        <div className="routing-y-diagram">
          <svg className="routing-y-svg" viewBox="0 0 120 180" preserveAspectRatio="none" aria-hidden="true">
            <path d="M16 90 H54" className="routing-y-path" />
            <path d="M54 90 L104 42" className="routing-y-path" />
            <path d="M54 90 L104 138" className="routing-y-path" />
            <path d="M95 33 L104 42 L95 51" className="routing-y-arrow" />
            <path d="M95 129 L104 138 L95 147" className="routing-y-arrow" />
          </svg>

          <div className="routing-branch routing-branch-local">
            <div className="routing-branch-content">
              <div className="routing-branch-label">{localZoneLabel}</div>
              <TrafficNode
                icon={<InternetIcon />}
                title={physicalIface}
                meta={localExternalIp ? `external IP ${localExternalIp}` : 'external IP not configured'}
                accent
              />
              <div className="routing-branch-meta">
                <span>{localMark ? `fwmark ${localMark}` : 'fwmark —'}</span>
                <span>{localMarked ? 'rule active' : 'rule missing'}</span>
              </div>
            </div>
          </div>

          <div className="routing-branch routing-branch-vpn">
            <div className="routing-branch-content">
              <div className="routing-branch-label">{vpnZoneLabel}</div>
              <TrafficNode
                icon={<VpnIcon />}
                title={activeNodeName ?? 'No active node'}
                meta={activeNodeExternalIp ? `external IP ${activeNodeExternalIp}` : 'external IP unavailable'}
              />
              <div className="routing-branch-meta">
                <span>{vpnMark ? `fwmark ${vpnMark}` : 'fwmark —'}</span>
                <span>{vpnMarked ? 'rule active' : 'rule missing'}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function TrafficNode({
  icon,
  title,
  meta,
  accent = false,
}: {
  icon: React.ReactNode
  title: string
  meta: string
  accent?: boolean
}) {
  return (
    <div className={`traffic-node${accent ? ' traffic-node-accent' : ''}`}>
      <div className="traffic-node-icon">{icon}</div>
      <div className="traffic-node-copy">
        <div className="traffic-node-title">{title}</div>
        <div className="traffic-node-meta">{meta}</div>
      </div>
    </div>
  )
}

function TrafficArrow({ label }: { label?: string }) {
  return (
    <div className="traffic-arrow">
      <div className="traffic-arrow-line" />
      {label ? <div className="traffic-arrow-label">{label}</div> : null}
    </div>
  )
}

function ClientIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="5" width="16" height="10" rx="2" />
      <path d="M8 19h8" />
      <path d="M12 15v4" />
    </svg>
  )
}

function ServerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="4" width="16" height="7" rx="2" />
      <rect x="4" y="13" width="16" height="7" rx="2" />
      <path d="M8 8h.01" />
      <path d="M8 17h.01" />
      <path d="M12 8h4" />
      <path d="M12 17h4" />
    </svg>
  )
}

function PolicyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l7 4v5c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V7l7-4z" />
      <path d="M9.5 12l1.8 1.8 3.7-4.1" />
    </svg>
  )
}

function InternetIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c3 3 4.5 6 4.5 9S15 18 12 21c-3-3-4.5-6-4.5-9S9 6 12 3z" />
    </svg>
  )
}

function VpnIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l7 4v5c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V7l7-4z" />
      <path d="M9 12h6" />
      <path d="M12 9v6" />
    </svg>
  )
}
