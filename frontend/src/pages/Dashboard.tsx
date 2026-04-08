import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSystemStatus, restartRouting, triggerGeoipUpdate } from '../api'
import { RoutingStatus, SystemStatus } from '../types'
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
  const routing = s.routing

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
        <div className="section-title">Routing Diagram</div>
        {'error' in routing && routing.error ? (
          <div className="error-box">{routing.error}</div>
        ) : (
          <RoutingDiagram
            routing={routing}
            activeNodeName={s.active_node?.name ?? null}
          />
        )}
      </div>
    </>
  )
}

function RoutingDiagram({
  routing,
  activeNodeName,
}: {
  routing: Partial<RoutingStatus>
  activeNodeName: string | null
}) {
  const geoipDestination = routing.geoip_destination
  const otherDestination = routing.other_destination
  const physicalIface = routing.physical_iface ?? 'eth0'

  return (
    <div className="routing-diagram">
      <div className="routing-diagram-header">
        <div>
          <div className="routing-diagram-title">Live traffic map</div>
          <div className="routing-diagram-subtitle">
            {routing.invert_geoip ? 'Inverted mode: GeoIP zone goes to VPN, other traffic goes direct.' : 'Normal mode: GeoIP zone goes direct, other traffic goes to VPN.'}
          </div>
        </div>
        <div className="routing-diagram-mode">
          <span className={`badge ${routing.invert_geoip ? 'badge-warning' : 'badge-online'}`}>
            {routing.invert_geoip ? 'inverted' : 'normal'}
          </span>
        </div>
      </div>

      <div className="routing-diagram-intake">
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
          title="Policy engine"
          meta="ipset + mangle"
        />
      </div>

      <div className="routing-lanes">
        <RoutingLane
          title="GeoIP Local Zone"
          tone="geoip"
          trafficLabel="matched GeoIP CIDRs"
          mark={routing.geoip_mark}
          destination={geoipDestination}
          physicalIface={physicalIface}
          activeNodeName={activeNodeName}
          isMarked={routing.prerouting_geoip}
          hasOutputRule={routing.output_geoip}
          hasNatLocal={routing.nat_eth0}
          hasNatVpn={routing.nat_awg1}
        />
        <RoutingLane
          title="Other Traffic"
          tone="other"
          trafficLabel="everything else"
          mark={routing.other_mark}
          destination={otherDestination}
          physicalIface={physicalIface}
          activeNodeName={activeNodeName}
          isMarked={routing.prerouting_other}
          hasOutputRule={routing.output_other}
          hasNatLocal={routing.nat_eth0}
          hasNatVpn={routing.nat_awg1}
        />
      </div>
    </div>
  )
}

function RoutingLane({
  title,
  tone,
  trafficLabel,
  mark,
  destination,
  physicalIface,
  activeNodeName,
  isMarked,
  hasOutputRule,
  hasNatLocal,
  hasNatVpn,
}: {
  title: string
  tone: 'geoip' | 'other'
  trafficLabel: string
  mark?: string
  destination?: 'local' | 'vpn'
  physicalIface: string
  activeNodeName: string | null
  isMarked?: boolean
  hasOutputRule?: boolean
  hasNatLocal?: boolean
  hasNatVpn?: boolean
}) {
  const isLocal = destination === 'local'
  const routeIface = isLocal ? physicalIface : 'awg1'
  const routeTable = isLocal ? 'table 100' : destination === 'vpn' ? 'table 200' : 'table —'
  const destinationLabel = isLocal ? 'Direct Local' : destination === 'vpn' ? 'Upstream VPN' : 'Unknown'
  const destinationMeta = isLocal ? `via ${physicalIface}` : activeNodeName ? `${activeNodeName} via awg1` : 'via awg1'
  const natReady = isLocal ? hasNatLocal : hasNatVpn

  return (
    <div className={`routing-lane routing-lane-${tone}`}>
      <div className="routing-lane-header">
        <div>
          <div className="routing-lane-title">{title}</div>
          <div className="routing-lane-subtitle">{trafficLabel}</div>
        </div>
        <div className="routing-lane-badges">
          <span className={`badge ${isMarked ? 'badge-online' : 'badge-error'}`}>
            {isMarked ? 'marked' : 'no mark'}
          </span>
          <span className={`badge ${hasOutputRule ? 'badge-online' : 'badge-warning'}`}>
            {hasOutputRule ? 'dns rule' : 'dns missing'}
          </span>
        </div>
      </div>

      <div className="routing-lane-flow">
        <TrafficNode
          icon={<ZoneIcon />}
          title={title}
          meta={trafficLabel}
          tone={tone}
        />
        <TrafficArrow label={mark ? `fwmark ${mark}` : 'fwmark —'} />
        <TrafficNode
          icon={<RouteIcon />}
          title={routeTable}
          meta="policy route"
        />
        <TrafficArrow label={routeIface} />
        <TrafficNode
          icon={isLocal ? <InternetIcon /> : <VpnIcon />}
          title={destinationLabel}
          meta={destinationMeta}
          accent={isLocal}
        />
      </div>

      <div className="routing-lane-footer">
        <span>PREROUTING: {isMarked ? 'active' : 'missing'}</span>
        <span>NAT: {natReady ? 'active' : 'missing'}</span>
      </div>
    </div>
  )
}

function TrafficNode({
  icon,
  title,
  meta,
  accent = false,
  tone,
}: {
  icon: React.ReactNode
  title: string
  meta: string
  accent?: boolean
  tone?: 'geoip' | 'other'
}) {
  return (
    <div className={`traffic-node${accent ? ' traffic-node-accent' : ''}${tone ? ` traffic-node-${tone}` : ''}`}>
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

function ZoneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 12h18" />
      <path d="M12 3a15.3 15.3 0 0 1 0 18" />
      <path d="M12 3a15.3 15.3 0 0 0 0 18" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  )
}

function RouteIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 6h8" />
      <path d="M5 12h14" />
      <path d="M5 18h10" />
      <path d="M13 4l2 2-2 2" />
      <path d="M17 10l2 2-2 2" />
      <path d="M15 16l2 2-2 2" />
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
