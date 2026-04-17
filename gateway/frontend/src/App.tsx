import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import api from './api'
import { useI18n } from './i18n'

type NodeItem = {
  id: number
  name: string
  raw_conf: string
  endpoint: string
  endpoint_host: string
  endpoint_port: number
  probe_ip: string | null
  public_key: string
  private_key: string
  preshared_key: string | null
  latest_latency_ms: number | null
  latest_latency_at?: string | null
  latest_latency_target?: string | null
  latest_latency_via_interface?: string | null
  latest_latency_method?: string | null
  udp_status: string | null
  udp_detail: string | null
  is_active: boolean
  position: number
  tunnel_address: string
  dns_servers: string[]
  allowed_ips: string[]
  persistent_keepalive: number | null
  obfuscation: Record<string, string | number>
}

type FailoverSettings = {
  enabled: boolean
  last_error: string | null
  last_event_at: string | null
}

type FirstNodeBootstrapLog = {
  id: number
  target_host: string
  ssh_user: string
  ssh_port: number
  remote_dir: string
  docker_namespace: string
  image_tag: string
  status: string
  log_output: string
  finished_at: string | null
  created_at: string
}

type SystemStatus = {
  runtime_available: boolean
  gateway_enabled: boolean
  tunnel_status: string
  tunnel_last_error: string | null
  active_entry_node: {
    id: number
    name: string
    endpoint: string
    latest_latency_ms: number | null
    uptime_seconds: number
    latest_latency_target?: string | null
    latest_latency_via_interface?: string | null
    latest_latency_method?: string | null
  } | null
  entry_node_count: number
  dns_rule_count: number
  allowed_client_cidrs: string[]
  runtime_mode: string
  kernel_available: boolean
  kernel_message: string | null
  ui_language: string
  kill_switch_enabled: boolean
  geoip_countries: string[]
  ipset_name: string
  firewall_backend?: 'iptables' | 'nftables'
  experimental_nftables?: boolean
  external_ip_info: ExternalIpInfo
  active_prefixes_count: number
  active_prefixes_configured_count: number
  traffic_summary: TrafficSummary
}

type TrafficInterfaceCounters = {
  rx_bytes: number
  tx_bytes: number
}

type TrafficCurrentSummary = {
  collected_at: string
  local_interface_name: string | null
  vpn_interface_name: string
  local: TrafficInterfaceCounters
  vpn: TrafficInterfaceCounters
} | null

type TrafficPeriodSummary = {
  local: TrafficInterfaceCounters
  vpn: TrafficInterfaceCounters
} | null

type TrafficSummary = {
  current: TrafficCurrentSummary
  last_hour: TrafficPeriodSummary
  last_day: TrafficPeriodSummary
}

type MetricsPoint = {
  collected_at: string
  cpu_usage_percent: number
  memory_total_bytes: number
  memory_used_bytes: number
  memory_free_bytes: number
}

type SystemMetrics = {
  period: '1h' | '24h'
  retention_hours: number
  sampling_interval_seconds: number
  latest: MetricsPoint | null
  points: MetricsPoint[]
}

type PrefixSourceSummary = {
  key: 'countries' | 'manual' | 'fqdn' | 'system'
  enabled: boolean
  items_count: number
  prefix_count: number | null
  description: string
}

type ExternalIpEndpointInfo = {
  service_url: string
  service_host: string | null
  value: string | null
  error: string | null
  checked_at: string | null
  route_target: 'local' | 'vpn'
}

type ExternalIpInfo = {
  refresh_interval_seconds: number
  forced_domains: string[]
  local: ExternalIpEndpointInfo
  vpn: ExternalIpEndpointInfo
}

type GatewaySettingsData = {
  ui_language: string
  runtime_mode: string
  allowed_client_cidrs: string[]
  gateway_enabled: boolean
  dns_intercept_enabled: boolean
  experimental_nftables: boolean
  device_tracking_enabled: boolean
  device_activity_timeout_seconds: number
  failover_enabled: boolean
  kernel_available: boolean
  kernel_message: string | null
  active_entry_node_id?: number | null
  tunnel_status?: string
  tunnel_last_error?: string | null
  external_ip_info: ExternalIpInfo
  api_settings: ApiSettings
}

type ApiSettings = {
  api_enabled: boolean
  api_access_key: string | null
  api_control_enabled: boolean
  api_allowed_client_cidrs: string[]
  device_api_default_scope: 'all' | 'marked'
}

type DeviceRecord = {
  id: number
  identity_key: string
  identity_source: string
  mac_address: string | null
  current_ip: string | null
  hostname: string | null
  manual_alias: string
  display_name: string
  is_marked: boolean
  is_active: boolean
  is_present: boolean
  presence_state: 'active' | 'present' | 'inactive'
  last_route_target: 'local' | 'vpn' | 'unknown'
  total_bytes: number
  first_seen_at: string | null
  last_seen_at: string | null
  last_traffic_at: string | null
  last_presence_check_at: string | null
  last_present_at: string | null
  last_absent_at: string | null
  ip_history: Array<{
    ip_address: string
    is_current: boolean
    first_seen_at: string | null
    last_seen_at: string | null
  }>
}

type DeviceListResponse = {
  scope: 'all' | 'marked'
  status: 'all' | 'active' | 'present' | 'inactive'
  search: string
  summary: {
    total: number
    all_devices: number
    marked: number
    active: number
    present: number
    inactive: number
  }
  devices: DeviceRecord[]
}

type PrefixSummary = {
  ipset_name: string
  geoip_ipset_name?: string
  manual_ipset_name?: string
  fqdn_ipset_name?: string
  set_name?: string
  geoip_set_name?: string
  manual_set_name?: string
  fqdn_set_name?: string
  firewall_backend?: 'iptables' | 'nftables'
  set_backend_label?: string
  total_prefixes: number
  configured_prefixes?: number
  resolved_prefixes?: number
  fallback_default_route: boolean
  sources: PrefixSourceSummary[]
}

type DeployStep = {
  ts: string
  msg: string
  type: 'info' | 'success' | 'error' | 'default'
}

type RoutingPolicyData = {
  countries_enabled: boolean
  geoip_countries: string[]
  manual_prefixes_enabled: boolean
  manual_prefixes: string[]
  fqdn_prefixes_enabled: boolean
  fqdn_prefixes: string[]
  geoip_ipset_name: string
  prefixes_route_local: boolean
  kill_switch_enabled: boolean
  prefix_summary: PrefixSummary
}

type RoutingPolicyPayload = {
  countries_enabled: boolean
  geoip_countries: string[]
  manual_prefixes_enabled: boolean
  manual_prefixes: string[]
  fqdn_prefixes_enabled: boolean
  fqdn_prefixes: string[]
  prefixes_route_local: boolean
  kill_switch_enabled: boolean
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('gateway-token')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function firewallBackendLabel(
  backend: string | undefined,
  t: (key: 'iptablesBackend' | 'nftablesBackend') => string,
) {
  return backend === 'nftables' ? t('nftablesBackend') : t('iptablesBackend')
}

function tunnelStatusLabel(status: string | undefined, t: (key: any) => string) {
  if (status === 'running') return t('tunnelStatusRunning')
  if (status === 'starting') return t('tunnelStatusStarting')
  if (status === 'stopped') return t('tunnelStatusStopped')
  if (status === 'error') return t('tunnelStatusError')
  if (status === 'unknown') return t('tunnelStatusUnknown')
  return status || '—'
}

const CLIENT_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone
const LOCALHOST_SOURCE = '127.0.0.0/8'

function normalizeUtcTimestamp(value: string) {
  return /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`
}

function parseUtcDate(value: string | null | undefined) {
  if (!value) return null
  const date = new Date(normalizeUtcTimestamp(value))
  return Number.isNaN(date.getTime()) ? null : date
}

function toRoutingPolicyPayload(data: RoutingPolicyData): RoutingPolicyPayload {
  return {
    countries_enabled: data.countries_enabled,
    geoip_countries: data.geoip_countries,
    manual_prefixes_enabled: data.manual_prefixes_enabled,
    manual_prefixes: data.manual_prefixes,
    fqdn_prefixes_enabled: data.fqdn_prefixes_enabled,
    fqdn_prefixes: data.fqdn_prefixes,
    prefixes_route_local: data.prefixes_route_local,
    kill_switch_enabled: data.kill_switch_enabled,
  }
}

function fmtNodeUptime(totalSeconds: number, t: (key: 'daysShort' | 'hoursShort' | 'minutesShort') => string) {
  const safeSeconds = Number.isFinite(totalSeconds) ? Math.max(0, Math.floor(totalSeconds)) : 0
  const totalMinutes = Math.floor(safeSeconds / 60)
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60
  const parts: string[] = []
  if (days > 0) parts.push(`${days}${t('daysShort')}`)
  if (days > 0 || hours > 0) parts.push(`${hours}${t('hoursShort')}`)
  parts.push(`${minutes}${t('minutesShort')}`)
  return parts.join(' ')
}

function useLoader<T>(url: string, fallback: T) {
  const [data, setData] = useState<T>(fallback)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const reload = async (options?: { background?: boolean }) => {
    if (!options?.background) {
      setLoading(true)
    }
    try {
      const response = await api.get(url)
      setData(response.data)
      setError('')
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void reload() }, [url])
  return { data, loading, error, reload, setData }
}

function useBackgroundReload<T>(reload: (options?: { background?: boolean }) => Promise<T>, intervalMs: number) {
  const reloadRef = useRef(reload)

  useEffect(() => {
    reloadRef.current = reload
  }, [reload])

  useEffect(() => {
    const tick = () => { void reloadRef.current({ background: true }) }
    const timer = window.setInterval(tick, intervalMs)
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [intervalMs])
}

function openEventStream(
  path: string,
  onMessage: (payload: any) => void,
  onDone?: () => void,
  onError?: () => void,
) {
  const token = localStorage.getItem('gateway-token')
  const streamUrl = token ? `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}` : path
  const source = new EventSource(streamUrl)
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data)
    onMessage(payload)
    if (payload.finished || payload.status === 'done' || payload.message === '__done__') {
      source.close()
      onDone?.()
    }
  }
  source.onerror = () => {
    source.close()
    onError?.()
  }
  return () => source.close()
}

function LoginPage() {
  const navigate = useNavigate()
  const { t } = useI18n()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const streams = useMemo(
    () => Array.from({ length: 26 }, (_, index) => ({
      id: index,
      text: `gateway route dns latency node policy router awg ${index} `,
      left: `${index * 3.9}%`,
      duration: `${11 + (index % 5) * 2.4}s`,
      delay: `${(index % 7) * -0.9}s`,
      opacity: 0.34 + (index % 4) * 0.08,
      size: 13 + (index % 4) * 2,
    })),
    [],
  )

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      const response = await api.post('/auth/login', { username, password })
      localStorage.setItem('gateway-token', response.data.access_token)
      navigate('/')
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Invalid credentials')
    }
  }

  return (
    <div className="login-shell">
      <div className="login-rain" aria-hidden="true">
        {streams.map((stream) => (
          <div
            key={stream.id}
            className="login-rain-column"
            style={{
              left: stream.left,
              animationDuration: stream.duration,
              animationDelay: stream.delay,
              opacity: stream.opacity,
              fontSize: stream.size,
            }}
          >
            {stream.text}
          </div>
        ))}
      </div>

      <div className="login-panel" style={{ width: 360 }}>
        <div style={{ marginBottom: 28, textAlign: 'center' }}>
          <div className="gateway-brand-wordmark">AWG Gateway</div>
          <div className="page-subtitle">{t('loginTitle')}</div>
        </div>

        <div className="card">
          {error && <div className="error-box">{error}</div>}
          <form onSubmit={submit} autoComplete="off">
            <div className="form-group">
              <label className="form-label">{t('username')}</label>
              <input
                className="form-input"
                name="gateway-operator"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoFocus
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t('password')}</label>
              <input
                className="form-input"
                name="gateway-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="off"
                required
              />
            </div>
            <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} type="submit">
              {t('signIn')}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

function GatewayMark() {
  return (
    <svg className="sidebar-mark" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#0f172a" />
      <path d="M16 33c0-9.4 7.6-17 17-17 4.8 0 9.1 1.9 12.2 5.1L39.6 27A9 9 0 1 0 42 33h8c0 9.4-7.6 17-17 17S16 42.4 16 33Z" fill="#f59e0b" />
      <path d="M45 16h3v8h8v3h-8v8h-3v-8h-8v-3h8z" fill="#38bdf8" />
      <circle cx="25" cy="33" r="3" fill="#fde68a" />
    </svg>
  )
}

function AppLayout() {
  const navigate = useNavigate()
  const { t } = useI18n()
  const navItems: Array<{
    to: string
    label: string
    Icon: ({ size }: { size?: number }) => JSX.Element
  }> = [
    { to: '/', label: t('dashboard'), Icon: GridIcon },
    { to: '/nodes', label: t('nodes'), Icon: ServerIcon },
    { to: '/devices', label: t('devices'), Icon: DeviceIcon },
    { to: '/policy', label: t('policy'), Icon: GlobeIcon },
    { to: '/routing', label: t('routing'), Icon: RouteIcon },
    { to: '/dns', label: t('dns'), Icon: DnsIcon },
    { to: '/backup', label: t('backup'), Icon: ArchiveIcon },
    { to: '/settings', label: t('settings'), Icon: GearIcon },
    { to: '/diagnostics', label: t('diagnostics'), Icon: PulseIcon },
  ]

  async function logout() {
    try { await api.post('/auth/logout') } catch {}
    localStorage.removeItem('gateway-token')
    navigate('/login')
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo" aria-label="AWG Gateway">
          <GatewayMark />
          <div>
            <div className="sidebar-wordmark">AWG Gateway</div>
            <div className="page-subtitle">Policy Router</div>
          </div>
        </div>
        <nav className="sidebar-nav">
          {navItems.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="btn btn-ghost btn-sm" style={{ width: '100%' }} onClick={() => void logout()}>
            {t('logout')}
          </button>
        </div>
      </aside>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/nodes" element={<NodesPage />} />
          <Route path="/devices" element={<DevicesPage />} />
          <Route path="/policy" element={<PolicyPage />} />
          <Route path="/routing" element={<RoutingPage />} />
          <Route path="/dns" element={<DnsPage />} />
          <Route path="/backup" element={<BackupPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/diagnostics" element={<DiagnosticsPage />} />
        </Routes>
      </main>
    </div>
  )
}

function DashboardPage() {
  const { t } = useI18n()
  const [metricsPeriod, setMetricsPeriod] = useState<'1h' | '24h'>('1h')
  const { data, loading, error, reload } = useLoader<SystemStatus>('/system/status', {
    runtime_available: false,
    gateway_enabled: true,
    tunnel_status: 'unknown',
    tunnel_last_error: null,
    active_entry_node: null,
    entry_node_count: 0,
    dns_rule_count: 0,
    allowed_client_cidrs: [LOCALHOST_SOURCE],
    runtime_mode: 'auto',
    kernel_available: false,
    kernel_message: null,
    ui_language: 'en',
    kill_switch_enabled: true,
    geoip_countries: [],
    ipset_name: 'routing_prefixes',
    firewall_backend: 'iptables',
    experimental_nftables: false,
    active_prefixes_count: 0,
    active_prefixes_configured_count: 0,
    external_ip_info: {
      refresh_interval_seconds: 600,
      forced_domains: [],
      local: { service_url: '', service_host: null, value: null, error: null, checked_at: null, route_target: 'local' },
      vpn: { service_url: '', service_host: null, value: null, error: null, checked_at: null, route_target: 'vpn' },
    },
    traffic_summary: {
      current: null,
      last_hour: null,
      last_day: null,
    },
  })
  const { data: metrics, loading: metricsLoading } = useLoader<SystemMetrics>(`/system/metrics?period=${metricsPeriod}`, {
    period: metricsPeriod,
    retention_hours: 24,
    sampling_interval_seconds: 60,
    latest: null,
    points: [],
  })
  const statusTone = data.tunnel_status === 'running' ? 'online' : data.tunnel_status === 'starting' ? 'warning' : 'offline'
  const hideKernelWarning = data.runtime_mode === 'userspace'
  const [togglePending, setTogglePending] = useState(false)
  useBackgroundReload(reload, 5_000)

  async function toggleGatewayEnabled(enabled: boolean) {
    setTogglePending(true)
    try {
      await api.put('/settings/gateway-enabled', { gateway_enabled: enabled })
      await reload()
    } finally {
      setTogglePending(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('dashboard')}</div>
          <div className="page-subtitle">{t('dashboardSubtitle')}</div>
        </div>
      </div>
      {error ? <div className="error-box">{error}</div> : null}
      {!error && !loading && !hideKernelWarning && !data.kernel_available ? <div className="info-box">{t('kernelUnavailable')}{data.kernel_message ? `: ${data.kernel_message}` : ''}</div> : null}
      {!error && !loading && data.tunnel_last_error ? <div className="error-box">{data.tunnel_last_error}</div> : null}
      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
      <div className="card-grid card-grid-4" style={{ marginBottom: 20 }}>
        <div className="card">
          <div className="flex items-center justify-between" style={{ gap: 16, marginBottom: 10 }}>
            <div className="card-title">{t('tunnel')}</div>
            <label className="toggle toggle-lg" title={t('gatewayEnabled')}>
              <input
                type="checkbox"
                checked={Boolean(data.gateway_enabled)}
                disabled={togglePending}
                onChange={(event) => { void toggleGatewayEnabled(event.target.checked) }}
              />
              <span className="toggle-slider" />
            </label>
          </div>
          <div className={`stat-value ${statusTone === 'online' ? 'text-accent' : statusTone === 'offline' ? 'text-danger' : ''}`}>
            {tunnelStatusLabel(data.tunnel_status, t)}
          </div>
          <div className="stat-label">
            {data.gateway_enabled ? t('gatewayEnabled') : t('disabled')}
          </div>
        </div>
        <StatCard title={t('entryNodes')} value={String(data.entry_node_count)} label="" />
        <StatCard
          title={t('activePrefixes')}
          value={String(data.active_prefixes_count)}
          label=""
        />
        <StatCard
          title={t('activeStack')}
          value={firewallBackendLabel(data.firewall_backend, t)}
          label=""
        />
      </div>
      <div className="card-grid card-grid-4" style={{ marginBottom: 20 }}>
        <StatCard
          title={t('localTrafficIn')}
          value={fmtBytes(data.traffic_summary.last_day?.local.rx_bytes)}
          label={`${t('lastHour')}: ${fmtBytes(data.traffic_summary.last_hour?.local.rx_bytes)}`}
        />
        <StatCard
          title={t('localTrafficOut')}
          value={fmtBytes(data.traffic_summary.last_day?.local.tx_bytes)}
          label={`${t('lastHour')}: ${fmtBytes(data.traffic_summary.last_hour?.local.tx_bytes)}`}
        />
        <StatCard
          title={t('vpnTrafficIn')}
          value={fmtBytes(data.traffic_summary.last_day?.vpn.rx_bytes)}
          label={`${t('lastHour')}: ${fmtBytes(data.traffic_summary.last_hour?.vpn.rx_bytes)}`}
        />
        <StatCard
          title={t('vpnTrafficOut')}
          value={fmtBytes(data.traffic_summary.last_day?.vpn.tx_bytes)}
          label={`${t('lastHour')}: ${fmtBytes(data.traffic_summary.last_hour?.vpn.tx_bytes)}`}
        />
      </div>
      <div className="card-grid card-grid-2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>{t('activeNode')}</div>
          {data.active_entry_node ? (
            <>
              <div className="active-node-summary">
                <div>
                  <div className="stat-value" style={{ fontSize: 20 }}>{data.active_entry_node.name}</div>
                  <div className="stat-label">{data.active_entry_node.endpoint}</div>
                </div>
                <div className="active-node-uptime">
                  <div className="active-node-uptime-label">{t('uptime')}</div>
                  <div className="active-node-uptime-value">
                    {fmtNodeUptime(data.active_entry_node?.uptime_seconds ?? 0, t)}
                  </div>
                </div>
              </div>
              <div className="text-muted text-sm" style={{ marginTop: 10 }}>
                {t('latency')}: {fmtLatency(data.active_entry_node.latest_latency_ms)}
                <br />
                {fmtLatencyProbe(data.active_entry_node.latest_latency_target, data.active_entry_node.latest_latency_via_interface, t)}
              </div>
            </>
          ) : (
            <>
              <div className="stat-value text-muted">—</div>
              <div className="stat-label">{t('noActiveNode')}</div>
            </>
          )}
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>{t('routeSafety')}</div>
          <div className="stat-value" style={{ fontSize: 20 }}>{data.kill_switch_enabled ? t('protectedMode') : t('relaxedState')}</div>
          <div className="stat-label">{t('trafficSource')}: {(data.allowed_client_cidrs || []).join(', ') || LOCALHOST_SOURCE}</div>
          <div className="text-muted text-sm" style={{ marginTop: 10 }}>
            {data.runtime_mode === 'userspace'
              ? `${t('runtimeMode')}: ${t('runtimeModeUserspace')}`
              : `${t('kernelModeStatus')}: ${data.kernel_available ? t('available') : t('unavailable')}`}
          </div>
        </div>
      </div>
      <div className="card-grid card-grid-2 section" style={{ marginTop: 20 }}>
        <ExternalIpCard endpoint={data.external_ip_info.local} forcedDomains={data.external_ip_info.forced_domains} />
        <ExternalIpCard endpoint={data.external_ip_info.vpn} forcedDomains={data.external_ip_info.forced_domains} />
      </div>
      <div className="section">
        <div className="section-title">
          <span>{t('systemLoad')}</span>
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
        {metricsLoading ? <div style={{ padding: 24, textAlign: 'center' }}><span className="spinner" /></div> : null}
        {!metricsLoading ? (
          <div className="card-grid card-grid-2 system-metrics-grid">
            <MetricChartCard
              title={t('cpuLoad')}
              value={fmtPercent(metrics.latest?.cpu_usage_percent)}
              subtitle={t('sampledPerMinute')}
              chip={t('hostCpu')}
              period={metricsPeriod}
              points={metrics.points}
              mode="cpu"
            />
            <MetricChartCard
              title={t('memoryUsed')}
              value={fmtBytes(metrics.latest?.memory_used_bytes)}
              subtitle={`${t('memoryFree')}: ${fmtBytes(metrics.latest?.memory_free_bytes)} / ${fmtBytes(metrics.latest?.memory_total_bytes)}`}
              chip={t('ram')}
              period={metricsPeriod}
              points={metrics.points}
              mode="memory"
            />
          </div>
        ) : null}
      </div>
    </>
  )
}

function ExternalIpCard({
  endpoint,
  forcedDomains,
}: {
  endpoint: ExternalIpEndpointInfo
  forcedDomains: string[]
}) {
  const { t } = useI18n()
  const forced = endpoint.service_host ? forcedDomains.includes(endpoint.service_host) : false
  const checkedAt = fmtDateTime(endpoint.checked_at)
  const toneClass = endpoint.value ? 'badge-online' : endpoint.error ? 'badge-error' : 'badge-pending'

  return (
    <div className="card">
      <div className="flex items-center justify-between" style={{ gap: 12, marginBottom: 12 }}>
        <div className="card-title">{endpoint.route_target === 'local' ? t('localExternalIp') : t('vpnExternalIp')}</div>
        <span className={`badge ${toneClass}`}>{endpoint.route_target === 'local' ? t('localInterface') : t('awgInterface')}</span>
      </div>
      <div className="stat-value" style={{ fontSize: 24 }}>{endpoint.value || '—'}</div>
      <div className="stat-label">
        {t('serviceHost')}: {endpoint.service_host || '—'}
      </div>
      <div className="text-muted text-sm" style={{ marginTop: 10 }}>
        {t('serviceUrl')}: <span className="text-mono">{endpoint.service_url || '—'}</span>
      </div>
      <div className="text-muted text-sm" style={{ marginTop: 6 }}>
        {t('lastChecked')}: {checkedAt}
      </div>
      <div className="text-muted text-sm" style={{ marginTop: 6 }}>
        {forced ? t('forcedIntoPrefixes') : t('usesDefaultDirection')}
      </div>
      {endpoint.error ? (
        <div className="text-muted text-sm" style={{ marginTop: 8, color: 'var(--danger)' }}>
          {endpoint.error}
        </div>
      ) : null}
    </div>
  )
}

function PolicyPage() {
  const { t } = useI18n()
  const { data: routing, loading, error, reload, setData } = useLoader<RoutingPolicyData>('/routing', {
    countries_enabled: true,
    geoip_countries: ['ru'],
    manual_prefixes_enabled: false,
    manual_prefixes: [],
    fqdn_prefixes_enabled: false,
    fqdn_prefixes: [],
    geoip_ipset_name: 'routing_prefixes',
    prefixes_route_local: true,
    kill_switch_enabled: true,
    prefix_summary: {
      ipset_name: 'routing_prefixes',
      geoip_ipset_name: 'routing_prefixes_geoip',
      manual_ipset_name: 'routing_prefixes_manual',
      fqdn_ipset_name: 'routing_prefixes_fqdn',
      set_name: 'routing_prefixes',
      geoip_set_name: 'routing_prefixes_geoip',
      manual_set_name: 'routing_prefixes_manual',
      fqdn_set_name: 'routing_prefixes_fqdn',
      firewall_backend: 'iptables',
      set_backend_label: 'ipset',
      total_prefixes: 0,
      configured_prefixes: 0,
      resolved_prefixes: 0,
      fallback_default_route: false,
      sources: [],
    },
  })
  const [message, setMessage] = useState('')
  const [countryModalOpen, setCountryModalOpen] = useState(false)
  const [manualModalOpen, setManualModalOpen] = useState(false)
  const [fqdnModalOpen, setFqdnModalOpen] = useState(false)
  useBackgroundReload(reload, 5_000)

  async function updateGeoip() {
    await api.post('/routing/refresh-geoip')
    setMessage(t('geoipUpdateRequested'))
    await reload()
  }

  async function toggleBlock(key: 'countries_enabled' | 'manual_prefixes_enabled' | 'fqdn_prefixes_enabled', value: boolean) {
    const nextRouting = { ...routing, [key]: value }
    setData(nextRouting)
    await api.put('/routing', toRoutingPolicyPayload(nextRouting))
    await reload()
  }

  async function removeCountry(country: string) {
    await api.delete(`/routing/countries/${country}`)
    await reload()
  }

  async function removeManualPrefix(prefix: string) {
    await api.delete(`/routing/manual-prefixes/${encodeURIComponent(prefix)}`)
    await reload()
  }

  async function removeFqdnPrefix(fqdn: string) {
    await api.delete(`/routing/fqdn-prefixes/${encodeURIComponent(fqdn)}`)
    await reload()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('policyTitle')}</div>
          <div className="page-subtitle">{t('policySubtitle')}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => void reload()}>{t('refresh')}</button>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ gap: 16, flexWrap: 'wrap' }}>
          <div>
            <div className="card-title" style={{ marginBottom: 8 }}>{t('totalPrefixes')}</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)' }}>{routing.prefix_summary.total_prefixes.toLocaleString()}</div>
            <div className="text-muted text-sm">
              {routing.prefix_summary.set_name || routing.geoip_ipset_name}
              {typeof routing.prefix_summary.configured_prefixes === 'number' ? ` • ${t('configured')}: ${routing.prefix_summary.configured_prefixes}` : ''}
            </div>
          </div>
          <div style={{ minWidth: 280 }}>
            <div className="card-title" style={{ marginBottom: 8 }}>{t('assembledFrom')}</div>
            <div className="text-muted text-sm">
              {routing.prefix_summary.sources.map((source) => `${t(source.key)}: ${source.enabled ? source.items_count : 0}`).join(' • ')}
              {routing.prefix_summary.fallback_default_route ? ` • ${t('defaultPrefixApplied')}` : ''}
            </div>
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>
              {firewallBackendLabel(routing.prefix_summary.firewall_backend, t)} • {routing.prefix_summary.geoip_set_name || routing.prefix_summary.geoip_ipset_name} • {routing.prefix_summary.manual_set_name || routing.prefix_summary.manual_ipset_name} • {routing.prefix_summary.fqdn_set_name || routing.prefix_summary.fqdn_ipset_name}
            </div>
          </div>
        </div>
      </div>

      <PolicyBlock
        title={t('countries')}
        description={t('countriesBlockDescription')}
        enabled={routing.countries_enabled}
        onToggle={(value) => void toggleBlock('countries_enabled', value)}
        onAdd={() => setCountryModalOpen(true)}
        addLabel={t('add')}
        actions={<button className="btn btn-secondary btn-sm" onClick={() => void updateGeoip()}>{t('updateGeoip')}</button>}
      >
        <SimpleTable
          headers={[t('countries'), t('status'), t('totalPrefixes'), t('actions')]}
          emptyText={t('noCountriesConfigured')}
          rows={routing.geoip_countries.map((country) => [
            <span className="badge badge-pending" key={`${country}-badge`}>{country.toUpperCase()}</span>,
            <span className={`badge ${routing.countries_enabled ? 'badge-online' : 'badge-offline'}`} key={`${country}-status`}>
              {routing.countries_enabled ? t('enabled') : t('disabled')}
            </span>,
            routing.prefix_summary.sources.find((item) => item.key === 'countries')?.prefix_count?.toLocaleString() ?? '0',
            <button className="btn btn-danger btn-sm" key={`${country}-remove`} onClick={() => void removeCountry(country)}>{t('remove')}</button>,
          ])}
        />
      </PolicyBlock>

      <PolicyBlock
        title={t('manualPrefixes')}
        description={t('manualBlockDescription')}
        enabled={routing.manual_prefixes_enabled}
        onToggle={(value) => void toggleBlock('manual_prefixes_enabled', value)}
        onAdd={() => setManualModalOpen(true)}
        addLabel={t('add')}
      >
        <SimpleTable
          headers={[t('manualPrefixes'), t('actions')]}
          emptyText={t('noManualPrefixesConfigured')}
          rows={routing.manual_prefixes.map((prefix) => [
            <span className="text-mono" key={`${prefix}-value`}>{prefix}</span>,
            <button className="btn btn-danger btn-sm" key={`${prefix}-remove`} onClick={() => void removeManualPrefix(prefix)}>{t('remove')}</button>,
          ])}
        />
      </PolicyBlock>

      <PolicyBlock
        title={t('fqdnPrefixes')}
        description={t('fqdnBlockDescription')}
        enabled={routing.fqdn_prefixes_enabled}
        onToggle={(value) => void toggleBlock('fqdn_prefixes_enabled', value)}
        onAdd={() => setFqdnModalOpen(true)}
        addLabel={t('add')}
      >
        <div className="text-muted text-sm" style={{ marginBottom: 12 }}>
          {t('configured')}: {routing.fqdn_prefixes.length} • {t('resolved')}: {routing.prefix_summary.resolved_prefixes ?? 0}
        </div>
        <SimpleTable
          headers={[t('fqdnPrefixes'), t('actions')]}
          emptyText={t('noFqdnPrefixesConfigured')}
          rows={routing.fqdn_prefixes.map((fqdn) => [
            <span className="text-mono" key={`${fqdn}-value`}>{fqdn}</span>,
            <button className="btn btn-danger btn-sm" key={`${fqdn}-remove`} onClick={() => void removeFqdnPrefix(fqdn)}>{t('remove')}</button>,
          ])}
        />
      </PolicyBlock>

      {countryModalOpen ? (
        <ListModal
          title={t('addCountry')}
          description={t('countryModalDescription')}
          placeholder="ru"
          submitLabel={t('add')}
          onClose={() => setCountryModalOpen(false)}
          onSubmit={async (items) => {
            for (const item of items) {
              await api.post('/routing/countries', { country_code: item })
            }
            setCountryModalOpen(false)
            await reload()
          }}
        />
      ) : null}
      {manualModalOpen ? (
        <ListModal
          title={t('addPrefix')}
          description={t('manualModalDescription')}
          placeholder={'203.0.113.10\n203.0.113.0/24'}
          submitLabel={t('add')}
          onClose={() => setManualModalOpen(false)}
          onSubmit={async (items) => {
            await api.post('/routing/manual-prefixes/bulk', { prefixes: items })
            setManualModalOpen(false)
            await reload()
          }}
        />
      ) : null}
      {fqdnModalOpen ? (
        <ListModal
          title={t('addFqdn')}
          description={t('fqdnModalDescription')}
          placeholder={'example.com\napi.example.com'}
          submitLabel={t('add')}
          onClose={() => setFqdnModalOpen(false)}
          onSubmit={async (items) => {
            await api.post('/routing/fqdn-prefixes/bulk', { fqdn_list: items })
            setFqdnModalOpen(false)
            await reload()
          }}
        />
      ) : null}
    </>
  )
}

function NodesPage() {
  const { t } = useI18n()
  const { data, loading, error, reload } = useLoader<NodeItem[]>('/nodes', [])
  const { data: failover, reload: reloadFailover } = useLoader<FailoverSettings>('/nodes/failover', {
    enabled: false,
    last_error: null,
    last_event_at: null,
  })
  const { data: bootstrapLogs, reload: reloadBootstrapLogs } = useLoader<FirstNodeBootstrapLog[]>('/nodes/bootstrap-first/logs', [])
  const [message, setMessage] = useState('')
  const [editNode, setEditNode] = useState<NodeItem | null>(null)
  const [deleteNode, setDeleteNode] = useState<NodeItem | null>(null)
  const [showAddNode, setShowAddNode] = useState(false)
  const [showBootstrapModal, setShowBootstrapModal] = useState(false)
  const [selectedBootstrapLog, setSelectedBootstrapLog] = useState<FirstNodeBootstrapLog | null>(null)

  async function activate(nodeId: number) {
    await api.post(`/nodes/${nodeId}/activate`)
    setMessage(t('tunnelRebuilt'))
    await reload()
  }

  async function startTunnel() {
    await api.post('/nodes/runtime/start')
    setMessage(t('tunnelStartRequested'))
    await reload()
  }

  async function stopTunnel() {
    await api.post('/nodes/runtime/stop')
    setMessage(t('tunnelStopRequested'))
    await reload()
  }

  async function toggleFailover(enabled: boolean) {
    await api.put('/nodes/failover', { enabled })
    setMessage(t('failoverUpdated'))
    await reloadFailover()
  }

  async function moveNode(nodeId: number, direction: 'up' | 'down') {
    await api.post(`/nodes/${nodeId}/move`, { direction })
    await reload()
  }

  async function removeNode(node: NodeItem) {
    await api.delete(`/nodes/${node.id}`)
    setDeleteNode(null)
    setMessage(`${t('deleted')}: ${node.name}`)
    await reload()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('nodes')}</div>
          <div className="page-subtitle">{t('nodesSubtitle')}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm" onClick={() => setShowBootstrapModal(true)}>{t('deployNode')}</button>
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddNode(true)}>{t('add')}</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void startTunnel()}>{t('startTunnel')}</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void stopTunnel()}>{t('stopTunnel')}</button>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ marginBottom: 14 }}>
          <div>
            <div className="card-title">{t('savedNodes')}</div>
            {failover.last_error ? <div className="text-muted text-sm" style={{ marginTop: 6 }}>{failover.last_error}</div> : null}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm">{t('failover')}</span>
            <label className="toggle" title={t('failover')}>
              <input type="checkbox" checked={failover.enabled} onChange={(event) => { void toggleFailover(event.target.checked) }} />
              <span className="toggle-slider" />
            </label>
          </div>
        </div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('name')}</th>
                <th>{t('endpoint')}</th>
                <th>{t('latency')}</th>
                <th>{t('udpStatus')}</th>
                <th>{t('actions')}</th>
                <th>{t('activeNode')}</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 ? (
                <tr><td colSpan={6} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>{t('noEntryNodes')}</td></tr>
              ) : data.map((node, index) => {
                const hasPinnedActive = data[0]?.is_active
                const canMoveUp = !node.is_active && index > (hasPinnedActive ? 1 : 0)
                const canMoveDown = !node.is_active && index < data.length - 1
                return (
                <tr key={node.id} className={node.is_active ? 'active-node' : ''}>
                  <td>{node.name}</td>
                  <td className="text-mono">{node.endpoint}</td>
                  <td className="text-mono">
                    {fmtLatency(node.latest_latency_ms)}
                    <div className="text-muted" style={{ fontSize: 11, marginTop: 4 }}>
                      {fmtLatencyProbe(node.latest_latency_target, node.latest_latency_via_interface, t)}
                    </div>
                  </td>
                  <td>{node.is_active ? '—' : renderUdpStatus(node.udp_status, t)}</td>
                  <td>
                    <div className="nodes-actions">
                      <button
                        className={`btn btn-sm ${node.is_active ? 'btn-secondary' : 'btn-primary'}`}
                        onClick={() => void activate(node.id)}
                        disabled={node.is_active}
                      >
                        {t('activate')}
                      </button>
                      <button className="btn btn-ghost btn-sm" title={t('moveUp')} onClick={() => void moveNode(node.id, 'up')} disabled={!canMoveUp}>↑</button>
                      <button className="btn btn-ghost btn-sm" title={t('moveDown')} onClick={() => void moveNode(node.id, 'down')} disabled={!canMoveDown}>↓</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditNode(node)}>{t('edit')}</button>
                      <button className="btn btn-danger btn-sm" onClick={() => setDeleteNode(node)}>{t('delete')}</button>
                    </div>
                  </td>
                  <td>{node.is_active ? <span className="badge badge-online">{t('active')}</span> : '—'}</td>
                </tr>
              )})}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-title" style={{ marginBottom: 14 }}>{t('bootstrapHistory')}</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('host')}</th>
                <th>{t('dockerImages')}</th>
                <th>{t('status')}</th>
                <th>{t('createdAt')}</th>
                <th>{t('actions')}</th>
              </tr>
            </thead>
            <tbody>
              {bootstrapLogs.length === 0 ? (
                <tr><td colSpan={5} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>{t('noBootstrapLogs')}</td></tr>
              ) : bootstrapLogs.map((log) => (
                <tr key={log.id}>
                  <td className="text-mono">{log.target_host}:{log.ssh_port}</td>
                  <td className="text-mono">{log.docker_namespace}:{log.image_tag}</td>
                  <td>{renderBootstrapStatus(log.status, t)}</td>
                  <td className="text-mono">{new Date(log.created_at).toLocaleString()}</td>
                  <td>
                    <button className="btn btn-ghost btn-sm" onClick={() => setSelectedBootstrapLog(log)}>{t('viewLog')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {showAddNode ? (
        <NodeImportModal
          onClose={() => setShowAddNode(false)}
          onSaved={async (nodeName) => {
            setShowAddNode(false)
            setMessage(`${t('imported')} ${nodeName}`)
            await reload()
          }}
        />
      ) : null}
      {showBootstrapModal ? (
        <FirstNodeBootstrapModal
          onClose={() => setShowBootstrapModal(false)}
          onDone={async () => {
            setShowBootstrapModal(false)
            setMessage(t('firstNodeBootstrapCompleted'))
            await reloadBootstrapLogs()
          }}
        />
      ) : null}
      {editNode ? (
        <NodeEditorModal
          node={editNode}
          onClose={() => setEditNode(null)}
          onSaved={async () => {
            setEditNode(null)
            setMessage(t('entryNodeUpdated'))
            await reload()
          }}
        />
      ) : null}
      {deleteNode ? (
        <ConfirmDeleteNodeModal
          node={deleteNode}
          onClose={() => setDeleteNode(null)}
          onConfirm={async () => {
            await removeNode(deleteNode)
          }}
        />
      ) : null}
      {selectedBootstrapLog ? (
        <LogViewerModal
          title={t('deployLog')}
          logOutput={selectedBootstrapLog.log_output}
          onClose={() => setSelectedBootstrapLog(null)}
        />
      ) : null}
    </>
  )
}

function renderBootstrapStatus(status: string, t: (key: any) => string) {
  if (status === 'success') return <span className="badge badge-online">{t('completed')}</span>
  if (status === 'failed') return <span className="badge badge-error">{t('failed')}</span>
  return <span className="badge badge-pending">{t('running')}</span>
}

function classifyStep(message: string): DeployStep['type'] {
  const normalized = message.toLowerCase()
  if (normalized.startsWith('error') || normalized.includes('failed')) return 'error'
  if (normalized.includes('complete') || normalized.includes('success')) return 'success'
  if (normalized.startsWith('[')) return 'info'
  return 'default'
}

function LogViewerModal({ title, logOutput, onClose }: { title: string; logOutput: string; onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-xl" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="terminal" style={{ minHeight: 220 }}>
          <pre style={{ margin: 0, color: '#a8b5c2', whiteSpace: 'pre-wrap' }}>{logOutput || '(empty)'}</pre>
        </div>
      </div>
    </div>
  )
}

function ConfirmDeleteNodeModal({
  node,
  onClose,
  onConfirm,
}: {
  node: NodeItem
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const { t } = useI18n()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    setSaving(true)
    setError('')
    try {
      await onConfirm()
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Delete failed')
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('deleteNode')}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="text-muted" style={{ marginBottom: 14 }}>
          {t('deleteNodeConfirmation')}: <span className="text-accent">{node.name}</span>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        <div className="flex gap-2" style={{ justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" type="button" onClick={onClose} disabled={saving}>{t('cancel')}</button>
          <button className="btn btn-danger" type="button" onClick={() => void submit()} disabled={saving}>
            {saving ? t('loading') : t('delete')}
          </button>
        </div>
      </div>
    </div>
  )
}

function FirstNodeBootstrapModal({
  onClose,
  onDone,
}: {
  onClose: () => void
  onDone: () => Promise<void>
}) {
  const { t } = useI18n()
  const [phase, setPhase] = useState<'form' | 'deploying' | 'done'>('form')
  const [form, setForm] = useState(() => {
    const saved = localStorage.getItem('gateway-first-node-bootstrap-form')
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as { docker_namespace?: string; image_tag?: string; remote_dir?: string }
        return {
          host: '',
          ssh_user: 'root',
          ssh_port: '22',
          ssh_password: '',
          remote_dir: parsed.remote_dir || '/opt/awg-jump',
          docker_namespace: parsed.docker_namespace || '',
          image_tag: parsed.image_tag || 'latest',
        }
      } catch {}
    }
    return {
      host: '',
      ssh_user: 'root',
      ssh_port: '22',
      ssh_password: '',
      remote_dir: '/opt/awg-jump',
      docker_namespace: '',
      image_tag: 'latest',
    }
  })
  const [lines, setLines] = useState<DeployStep[]>([])
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)
  const [logOutput, setLogOutput] = useState('')
  const termRef = useRef<HTMLDivElement | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    localStorage.setItem(
      'gateway-first-node-bootstrap-form',
      JSON.stringify({
        remote_dir: form.remote_dir,
        docker_namespace: form.docker_namespace,
        image_tag: form.image_tag,
      }),
    )
  }, [form.remote_dir, form.docker_namespace, form.image_tag])

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight
    }
  }, [lines])

  useEffect(() => () => { cleanupRef.current?.() }, [])

  const setField = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }))

  const addLine = (message: string) => {
    setLines((current) => [...current, { ts: new Date().toLocaleTimeString(), msg: message, type: classifyStep(message) }])
    setLogOutput((current) => `${current}${message}\n`)
  }

  async function startBootstrap() {
    setError('')
    setLines([])
    setLogOutput('')
    setProgress(5)
    setPhase('deploying')

    try {
      const response = await api.post('/nodes/bootstrap-first', {
        host: form.host,
        ssh_user: form.ssh_user,
        ssh_password: form.ssh_password,
        ssh_port: Number(form.ssh_port),
        remote_dir: form.remote_dir,
        docker_namespace: form.docker_namespace,
        image_tag: form.image_tag,
      })
      const logId = response.data.bootstrap_log_id as number
      let stepCount = 0
      cleanupRef.current = openEventStream(
        `/api/nodes/bootstrap-first/${logId}/stream`,
        (payload) => {
          const message = String(payload.message ?? '')
          if (message && message !== '__done__') {
            addLine(message)
          }
          stepCount += message.startsWith('[') ? 1 : 0
          setProgress(Math.min(10 + stepCount * 12, 92))
          if (payload.status === 'error') {
            cleanupRef.current?.()
            setProgress(100)
            setPhase('form')
            setError(message || t('bootstrapFailed'))
          }
          if (payload.finished || payload.status === 'done') {
            setProgress(100)
            setPhase('done')
          }
        },
        () => {
          setProgress(100)
          setPhase((current) => current === 'form' ? current : 'done')
        },
        () => {
          setError(t('streamDisconnected'))
        },
      )
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || t('bootstrapFailed'))
      setProgress(0)
      setPhase('form')
    }
  }

  return (
    <div className="modal-overlay" onClick={() => { cleanupRef.current?.(); onClose() }}>
      <div className="modal modal-xl" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('deployFirstNode')}</div>
          <button className="btn btn-ghost btn-sm" onClick={() => { cleanupRef.current?.(); onClose() }}>Close</button>
        </div>
        {phase === 'form' ? (
          <>
            {error ? <div className="error-box">{error}</div> : null}
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">{t('host')}</label>
                <input className="form-input mono" value={form.host} onChange={setField('host')} placeholder="203.0.113.10" />
              </div>
              <div className="form-group">
                <label className="form-label">{t('remoteDir')}</label>
                <input className="form-input mono" value={form.remote_dir} onChange={setField('remote_dir')} />
              </div>
            </div>
            <div className="form-row form-row-3">
              <div className="form-group">
                <label className="form-label">{t('sshUser')}</label>
                <input className="form-input mono" value={form.ssh_user} onChange={setField('ssh_user')} />
              </div>
              <div className="form-group">
                <label className="form-label">{t('sshPort')}</label>
                <input className="form-input mono" value={form.ssh_port} onChange={setField('ssh_port')} />
              </div>
              <div className="form-group">
                <label className="form-label">{t('imageTag')}</label>
                <input className="form-input mono" value={form.image_tag} onChange={setField('image_tag')} />
              </div>
            </div>
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">{t('sshPassword')}</label>
                <input className="form-input" type="password" value={form.ssh_password} onChange={setField('ssh_password')} autoComplete="new-password" />
              </div>
              <div className="form-group">
                <label className="form-label">{t('dockerNamespace')}</label>
                <input className="form-input mono" value={form.docker_namespace} onChange={setField('docker_namespace')} placeholder="your-dockerhub-namespace" />
              </div>
            </div>
            <div className="info-box">
              {t('bootstrapFormNotice')}
            </div>
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={onClose}>{t('cancel')}</button>
              <button
                className="btn btn-primary"
                onClick={() => void startBootstrap()}
                disabled={!form.host || !form.ssh_user || !form.ssh_password || !form.docker_namespace}
              >
                {t('deploy')}
              </button>
            </div>
          </>
        ) : (
          <>
            <div style={{ marginBottom: 12 }}>
              <div className="progress-bar">
                <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
              </div>
            </div>
            <div className="terminal" ref={termRef}>
              {lines.map((line, index) => (
                <div key={`${line.ts}-${index}`} className={`terminal-line ${line.type}`}>
                  <span className="ts">{line.ts}</span>
                  <span className="msg">{line.msg}</span>
                </div>
              ))}
              {phase === 'deploying' ? (
                <div className="terminal-line">
                  <span className="ts" />
                  <span className="msg"><span className="spinner" /></span>
                </div>
              ) : null}
            </div>
            {phase === 'done' ? (
              <>
                <div className="info-box" style={{ marginTop: 16 }}>
                  <strong>{t('nextSteps')}</strong><br />
                  {t('bootstrapNextStep1a')} <span className="text-mono">{form.remote_dir}/.env</span>. {t('bootstrapNextStep1b')}
                  <br />
                  {t('bootstrapNextStep2a')} <span className="text-mono">{form.remote_dir}</span>: <span className="text-mono">docker compose -f docker-compose.yml pull && docker compose -f docker-compose.yml up -d</span>
                </div>
                <div className="modal-actions">
                  <button className="btn btn-secondary" onClick={() => navigator.clipboard.writeText(logOutput)}>{t('copyLog')}</button>
                  <button className="btn btn-primary" onClick={() => void onDone()}>{t('done')}</button>
                </div>
              </>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}

function NodeImportModal({
  onClose,
  onSaved,
}: {
  onClose: () => void
  onSaved: (nodeName: string) => Promise<void>
}) {
  const { t } = useI18n()
  const [name, setName] = useState('')
  const [confText, setConfText] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function loadConfFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    const text = await file.text()
    setConfText(text)
    if (!name) {
      setName(file.name.replace(/\.conf$/i, ''))
    }
  }

  async function importConf(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const response = await api.post('/nodes/import', { name: name || null, conf_text: confText })
      await onSaved(response.data.name)
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Failed to import entry node')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-xl" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('importConf')}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        <form onSubmit={importConf}>
          <div className="form-group">
            <label className="form-label">Name</label>
            <input className="form-input" value={name} onChange={(event) => setName(event.target.value)} placeholder={t('optionalDisplayName')} />
          </div>
          <div className="form-group">
            <label className="form-label">.conf</label>
            <textarea className="form-input mono" rows={18} value={confText} onChange={(event) => setConfText(event.target.value)} placeholder="[Interface]" />
          </div>
          <div className="modal-actions">
            <label className="btn btn-secondary">
              {t('uploadConfFile')}
              <input type="file" accept=".conf,text/plain" hidden onChange={loadConfFile} />
            </label>
            <button className="btn btn-secondary" type="button" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? <span className="spinner" /> : t('save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function NodeEditorModal({
  node,
  onClose,
  onSaved,
}: {
  node: NodeItem
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { t } = useI18n()
  const [tab, setTab] = useState<'raw' | 'visual'>('visual')
  const [error, setError] = useState('')
  const [rawName, setRawName] = useState(node.name)
  const [rawConf, setRawConf] = useState(node.raw_conf)
  const [visual, setVisual] = useState({
    name: node.name,
    endpoint: node.endpoint,
    probe_ip: node.probe_ip ?? '',
    public_key: node.public_key,
    private_key: node.private_key,
    preshared_key: node.preshared_key ?? '',
    tunnel_address: node.tunnel_address,
    dns_servers: node.dns_servers.join(', '),
    allowed_ips: node.allowed_ips.join(', '),
    persistent_keepalive: node.persistent_keepalive == null ? '' : String(node.persistent_keepalive),
  })
  const [saving, setSaving] = useState(false)

  const setField = (key: keyof typeof visual) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setVisual((current) => ({ ...current, [key]: event.target.value }))

  async function saveRaw() {
    setSaving(true)
    setError('')
    try {
      await api.put(`/nodes/${node.id}/raw-conf`, { name: rawName, conf_text: rawConf })
      await onSaved()
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Failed to update entry node')
    } finally {
      setSaving(false)
    }
  }

  async function saveVisual() {
    setSaving(true)
    setError('')
    try {
      await api.put(`/nodes/${node.id}/visual`, {
        name: visual.name,
        endpoint: visual.endpoint,
        probe_ip: visual.probe_ip || null,
        public_key: visual.public_key,
        private_key: visual.private_key,
        preshared_key: visual.preshared_key || null,
        tunnel_address: visual.tunnel_address,
        dns_servers: visual.dns_servers.split(',').map((item) => item.trim()).filter(Boolean),
        allowed_ips: visual.allowed_ips.split(',').map((item) => item.trim()).filter(Boolean),
        persistent_keepalive: visual.persistent_keepalive ? Number(visual.persistent_keepalive) : null,
      })
      await onSaved()
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Failed to update entry node')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-xl" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('editEntryNode')}: {node.name}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="tabs">
          <button className={`tab-btn ${tab === 'visual' ? 'active' : ''}`} onClick={() => setTab('visual')}>Visual</button>
          <button className={`tab-btn ${tab === 'raw' ? 'active' : ''}`} onClick={() => setTab('raw')}>Raw .conf</button>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        {tab === 'visual' ? (
          <>
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">Name</label>
                <input className="form-input" value={visual.name} onChange={setField('name')} />
              </div>
              <div className="form-group">
                <label className="form-label">Endpoint</label>
                <input className="form-input mono" value={visual.endpoint} onChange={setField('endpoint')} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('probeIp')}</label>
              <input className="form-input mono" value={visual.probe_ip} onChange={setField('probe_ip')} placeholder="10.77.7.1" />
            </div>
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">Tunnel address</label>
                <input className="form-input mono" value={visual.tunnel_address} onChange={setField('tunnel_address')} />
              </div>
              <div className="form-group">
                <label className="form-label">Persistent keepalive</label>
                <input className="form-input mono" value={visual.persistent_keepalive} onChange={setField('persistent_keepalive')} />
              </div>
            </div>
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">DNS servers</label>
                <input className="form-input mono" value={visual.dns_servers} onChange={setField('dns_servers')} />
              </div>
              <div className="form-group">
                <label className="form-label">Allowed IPs</label>
                <input className="form-input mono" value={visual.allowed_ips} onChange={setField('allowed_ips')} />
              </div>
            </div>
            <div className="form-row form-row-2">
              <div className="form-group">
                <label className="form-label">Public key</label>
                <input className="form-input mono" value={visual.public_key} onChange={setField('public_key')} />
              </div>
              <div className="form-group">
                <label className="form-label">Private key</label>
                <input className="form-input mono" value={visual.private_key} onChange={setField('private_key')} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Preshared key</label>
              <input className="form-input mono" value={visual.preshared_key} onChange={setField('preshared_key')} />
            </div>
            <div className="info-box">{t('visualEditorNotice')}</div>
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={() => void saveVisual()} disabled={saving}>
                {saving ? <span className="spinner" /> : 'Save'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="form-group">
              <label className="form-label">Name</label>
              <input className="form-input" value={rawName} onChange={(event) => setRawName(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Raw .conf</label>
              <textarea className="form-input mono" rows={18} value={rawConf} onChange={(event) => setRawConf(event.target.value)} />
            </div>
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={() => void saveRaw()} disabled={saving}>
                {saving ? <span className="spinner" /> : 'Save'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function RoutingPage() {
  const { t } = useI18n()
  const { data, loading, error, reload, setData } = useLoader<RoutingPolicyData>('/routing', {
    countries_enabled: true,
    geoip_countries: ['ru'],
    manual_prefixes_enabled: false,
    manual_prefixes: [],
    fqdn_prefixes_enabled: false,
    fqdn_prefixes: [],
    geoip_ipset_name: 'routing_prefixes',
    prefixes_route_local: true,
    kill_switch_enabled: true,
    prefix_summary: {
      ipset_name: 'routing_prefixes',
      geoip_ipset_name: 'routing_prefixes_geoip',
      manual_ipset_name: 'routing_prefixes_manual',
      fqdn_ipset_name: 'routing_prefixes_fqdn',
      set_name: 'routing_prefixes',
      geoip_set_name: 'routing_prefixes_geoip',
      manual_set_name: 'routing_prefixes_manual',
      fqdn_set_name: 'routing_prefixes_fqdn',
      firewall_backend: 'iptables',
      set_backend_label: 'ipset',
      total_prefixes: 0,
      configured_prefixes: 0,
      resolved_prefixes: 0,
      fallback_default_route: false,
      sources: [],
    },
  })
  const { data: plan, reload: reloadPlan } = useLoader<any>('/routing/plan', { commands: [], warnings: [], safe_to_apply: false })
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(''), 2400)
    return () => window.clearTimeout(timer)
  }, [message])

  async function persistPolicy(nextData: RoutingPolicyData) {
    setData(nextData)
    await api.put('/routing', toRoutingPolicyPayload(nextData))
    const applyResponse = await api.post('/routing/apply')
    setMessage(applyResponse.data.status === 'applied' ? t('routingApplied') : applyResponse.data.error || t('routingBlocked'))
    await reload()
    await reloadPlan()
  }

  async function togglePolicy(key: 'prefixes_route_local' | 'kill_switch_enabled', value: boolean) {
    const nextData = { ...data, [key]: value }
    await persistPolicy(nextData)
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('routing')}</div>
          <div className="page-subtitle">{t('routingSubtitle')}</div>
        </div>
        </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ marginBottom: 10 }}>
          <div>
            <div className="card-title">{t('trafficDirection')}</div>
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>
              {data.prefixes_route_local ? t('prefixesToLocalDescription') : t('prefixesToTunnelDescription')}
            </div>
          </div>
        </div>
        <div className="traffic-direction-card">
          <div className={`traffic-toggle-row ${data.prefixes_route_local ? 'traffic-toggle-row-active' : ''}`}>
            <label className="toggle toggle-lg" title={t('routingPrefixes')}>
              <input type="checkbox" checked={data.prefixes_route_local} onChange={(event) => void togglePolicy('prefixes_route_local', event.target.checked)} />
              <span className="toggle-slider" />
            </label>
            <div className={data.prefixes_route_local ? 'traffic-toggle-copy-active' : ''}>
              <div style={{ fontWeight: 600 }}>{data.prefixes_route_local ? t('sendToLocalInterface') : t('sendToAwgInterface')}</div>
              <div className={`text-sm ${data.prefixes_route_local ? 'traffic-toggle-meta-active' : 'text-muted'}`}>
                {data.prefixes_route_local ? t('stateLocalTranslated') : t('stateTunnelTranslated')}
              </div>
            </div>
          </div>
        </div>
        <div className="flex gap-4" style={{ marginBottom: 4, flexWrap: 'wrap' }}>
          <label className="toggle" title={t('killSwitch')}>
            <input type="checkbox" checked={data.kill_switch_enabled} onChange={(event) => void togglePolicy('kill_switch_enabled', event.target.checked)} />
            <span className="toggle-slider" />
          </label>
          <span className="text-sm">{t('killSwitch')}</span>
        </div>
      </div>
      <div className="section">
        <div className="section-title">{t('policyRoutingDiagram')}</div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="routing-diagram">
          <div className="routing-diagram-header">
            <div>
              <div className="routing-diagram-title">{t('generatedTrafficMap')}</div>
              <div className="routing-diagram-subtitle">
                {data.prefixes_route_local ? t('diagramLocalMode') : t('diagramTunnelMode')}
              </div>
            </div>
            <span className={`badge ${plan.safe_to_apply ? 'badge-online' : 'badge-warning'}`}>
              {plan.safe_to_apply ? t('safeToApply') : t('blocked')}
            </span>
          </div>
          <div className="routing-flow">
            <FlowNode title={t('trafficSource')} value={(plan.selectors || []).join(', ') || LOCALHOST_SOURCE} meta={t('sourceMode')} />
            <FlowArrow />
            <FlowNode
              title={t('prefixSet')}
              value={data.prefix_summary.set_name || data.geoip_ipset_name}
              meta={`${firewallBackendLabel(plan.firewall_backend || data.prefix_summary.firewall_backend, t)} • ${plan.geoip_prefix_count ?? 0} ${t('totalPrefixes').toLowerCase()}`}
              accent
            />
            <FlowArrow />
            <FlowNode
              title={t('trafficDirection')}
              value={data.prefixes_route_local ? t('localInterface') : t('awgInterface')}
              meta={data.prefixes_route_local ? t('stateLocalTranslated') : t('stateTunnelTranslated')}
            />
            <FlowArrow />
            <FlowNode
              title={t('killSwitch')}
              value={data.kill_switch_enabled ? t('enabled') : t('disabled')}
              meta={t('routeSafety')}
            />
          </div>
          {(plan.warnings || []).length > 0 ? (
            <div className="error-box" style={{ marginTop: 16 }}>{plan.warnings.join('\n')}</div>
          ) : (
            <div className="info-box" style={{ marginTop: 16 }}>{t('noWarnings')}</div>
          )}
        </div>
      </div>
      <div className="section">
        <div className="section-title">{t('preview')}</div>
        <div className="terminal" style={{ minHeight: 220 }}>
          {(plan.commands || []).map((command: string, index: number) => (
            <div key={index} className="terminal-line">
              <span className="ts">{String(index + 1).padStart(2, '0')}</span>
              <span className="msg">{command}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

function DnsPage() {
  const { t } = useI18n()
  const { data, loading, error, reload } = useLoader<any>('/dns', { upstreams: [], domains: [], manual_addresses: [], preview: '' })
  const [domainModalOpen, setDomainModalOpen] = useState(false)
  const [zoneModalOpen, setZoneModalOpen] = useState(false)
  const [editingZone, setEditingZone] = useState<any | null>(null)
  const [deletingZone, setDeletingZone] = useState<any | null>(null)
  const [deletingDomain, setDeletingDomain] = useState<any | null>(null)
  const [deletingManualAddress, setDeletingManualAddress] = useState<any | null>(null)
  const [filter, setFilter] = useState('')
  const [manualFilter, setManualFilter] = useState('')
  const [manualAddressModalOpen, setManualAddressModalOpen] = useState(false)

  const localUpstream = data.upstreams.find((item: any) => item.zone === 'local')
  const vpnUpstream = data.upstreams.find((item: any) => item.zone === 'vpn')
  const disabledCount = data.domains.filter((item: any) => !item.enabled).length
  const filteredDomains = useMemo(
    () => data.domains.filter((item: any) => !filter || item.domain.toLowerCase().includes(filter.toLowerCase())),
    [data.domains, filter],
  )
  const filteredManualAddresses = useMemo(
    () => data.manual_addresses.filter((item: any) => !manualFilter || item.domain.toLowerCase().includes(manualFilter.toLowerCase())),
    [data.manual_addresses, manualFilter],
  )
  const selectableZones = data.upstreams.filter((item: any) => item.zone === 'local' || !item.is_builtin)
  const dotZoneExists = data.upstreams.some((item: any) => !item.is_builtin && item.protocol === 'dot')
  const dohZoneExists = data.upstreams.some((item: any) => !item.is_builtin && item.protocol === 'doh')

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('dns')}</div>
          <div className="page-subtitle">{t('dnsSubtitle')}</div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={async () => {
              await api.post('/dns/reload')
              await reload()
            }}
          >
            {t('reloadDns')}
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setZoneModalOpen(true)}>{t('addZone')}</button>
        </div>
      </div>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          <GatewayStatusChip label="dnsmasq" running={Boolean(data.running)} details={data.pid ? `pid ${data.pid}` : '—'} />
          <GatewayStatusChip label="stubby" running={Boolean(data.stubby?.running)} details={data.stubby?.enabled ? data.stubby.listen : t('dnsServiceDisabled')} />
          <GatewayStatusChip label="cloudflared" running={Boolean(data.cloudflared?.running)} details={data.cloudflared?.enabled ? data.cloudflared.listen : t('dnsServiceDisabled')} />
          <InfoChip label={t('localZoneDns')} value={renderGatewayZoneTarget(localUpstream)} accent />
          <InfoChip label={t('upstreamZoneDns')} value={renderGatewayZoneTarget(vpnUpstream)} />
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>{data.domains.length}</div>
            <div className="text-muted text-sm">{t('domains')}</div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-title" style={{ marginBottom: 14 }}>{t('dnsZones')}</div>
        <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{t('dnsProtectedZoneLimitHint')}</div>
        <div className={`card-grid ${gatewayZoneColumnsClass(data.upstreams.length)}`}>
          {data.upstreams.map((zone: any) => (
            <GatewayZoneCard
              key={zone.zone}
              zone={zone}
              onEdit={() => setEditingZone(zone)}
              onDelete={() => setDeletingZone(zone)}
            />
          ))}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ gap: 12, flexWrap: 'wrap' }}>
          <div className="card-title">
            {t('domains')}
            {disabledCount ? <span className="text-muted text-sm" style={{ marginLeft: 8, fontWeight: 400 }}>({disabledCount} disabled)</span> : null}
          </div>
          <div className="flex gap-2" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
            <input
              className="form-input"
              placeholder={t('domainFilterPlaceholder')}
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              style={{ width: 220, fontSize: 13 }}
            />
            <button className="btn btn-primary btn-sm" onClick={() => setDomainModalOpen(true)}>{t('add')}</button>
          </div>
        </div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('domains')}</th>
                <th>{t('zona')}</th>
                <th>{t('status')}</th>
                <th style={{ width: 70 }} />
              </tr>
            </thead>
            <tbody>
              {filteredDomains.length === 0 ? (
                <tr><td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>{t('noDomainsConfigured')}</td></tr>
              ) : filteredDomains.map((item: any) => (
                <tr key={item.id}>
                  <td className="mono" style={{ opacity: item.enabled ? 1 : 0.45 }}>{item.domain}</td>
                  <td><GatewayZoneBadge zone={data.upstreams.find((zone: any) => zone.zone === item.zone)} zoneKey={item.zone} /></td>
                  <td>
                    <label className="toggle" title={t('status')}>
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        onChange={async () => {
                          await api.post(`/dns/domains/${item.id}/toggle`)
                          await reload()
                        }}
                      />
                      <span className="toggle-slider" />
                    </label>
                  </td>
                  <td>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ color: 'var(--danger)', padding: 4 }}
                      onClick={() => setDeletingDomain(item)}
                    >
                      <TrashIconSmall />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ gap: 12, flexWrap: 'wrap' }}>
          <div className="card-title">{t('manualReplaceAddresses')}</div>
          <div className="flex gap-2" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
            <input
              className="form-input"
              placeholder={t('domainFilterPlaceholder')}
              value={manualFilter}
              onChange={(event) => setManualFilter(event.target.value)}
              style={{ width: 220, fontSize: 13 }}
            />
            <button className="btn btn-primary btn-sm" onClick={() => setManualAddressModalOpen(true)}>{t('add')}</button>
          </div>
        </div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('domains')}</th>
                <th>{t('addressLabel')}</th>
                <th>{t('status')}</th>
                <th style={{ width: 70 }} />
              </tr>
            </thead>
            <tbody>
              {filteredManualAddresses.length === 0 ? (
                <tr>
                  <td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>
                    {manualFilter ? t('manualReplaceNoMatch') : t('manualReplaceEmpty')}
                  </td>
                </tr>
              ) : filteredManualAddresses.map((item: any) => (
                <tr key={item.id}>
                  <td className="mono" style={{ opacity: item.enabled ? 1 : 0.45 }}>{item.domain}</td>
                  <td className="mono" style={{ opacity: item.enabled ? 1 : 0.45 }}>{item.address}</td>
                  <td>
                    <label className="toggle" title={t('status')}>
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        onChange={async () => {
                          await api.post(`/dns/manual-addresses/${item.id}/toggle`)
                          await reload()
                        }}
                      />
                      <span className="toggle-slider" />
                    </label>
                  </td>
                  <td>
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ color: 'var(--danger)', padding: 4 }}
                      onClick={() => setDeletingManualAddress(item)}
                    >
                      <TrashIconSmall />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {domainModalOpen ? (
        <GatewayDnsDomainModal
          title={t('addDomain')}
          zones={data.upstreams}
          selectableZones={selectableZones}
          onClose={() => setDomainModalOpen(false)}
          onSubmit={async (items, zone) => {
            await api.post('/dns/domains/bulk', { domains: items, zone, enabled: true })
            setDomainModalOpen(false)
            await reload()
          }}
        />
      ) : null}
      {manualAddressModalOpen ? (
        <GatewayManualAddressModal
          onClose={() => setManualAddressModalOpen(false)}
          onSubmit={async (domain, address) => {
            await api.post('/dns/manual-addresses', { domain, address, enabled: true })
            setManualAddressModalOpen(false)
            await reload()
          }}
        />
      ) : null}
      {zoneModalOpen ? (
        <GatewayDnsZoneModal
          title={t('addZone')}
          description={t('addZoneDescription')}
          dotSlotTaken={dotZoneExists}
          dohSlotTaken={dohZoneExists}
          onClose={() => setZoneModalOpen(false)}
          onSubmit={async (payload) => {
            await api.post('/dns/zones', payload)
            setZoneModalOpen(false)
            await reload()
          }}
        />
      ) : null}
      {editingZone ? (
        <GatewayDnsZoneModal
          title={t('edit')}
          description={t('addZoneDescription')}
          initialZone={editingZone}
          dotSlotTaken={dotZoneExists && editingZone.protocol !== 'dot'}
          dohSlotTaken={dohZoneExists && editingZone.protocol !== 'doh'}
          onClose={() => setEditingZone(null)}
          onSubmit={async (payload) => {
            await api.put(`/dns/zones/${editingZone.zone}`, { ...payload, domains: undefined })
            setEditingZone(null)
            await reload()
          }}
          editing
        />
      ) : null}
      {deletingZone ? (
        <GatewayDeleteZoneModal
          zone={deletingZone}
          onClose={() => setDeletingZone(null)}
          onConfirm={async () => {
            await api.delete(`/dns/zones/${deletingZone.zone}`)
            setDeletingZone(null)
            await reload()
          }}
        />
      ) : null}
      {deletingDomain ? (
        <GatewayDeleteConfirmModal
          title={t('deleteDomainTitle')}
          message={t('deleteDomainConfirm').replace('{domain}', deletingDomain.domain)}
          onClose={() => setDeletingDomain(null)}
          onConfirm={async () => {
            await api.delete(`/dns/domains/${deletingDomain.id}`)
            setDeletingDomain(null)
            await reload()
          }}
        />
      ) : null}
      {deletingManualAddress ? (
        <GatewayDeleteConfirmModal
          title={t('deleteManualAddressTitle')}
          message={t('deleteManualAddressConfirm').replace('{domain}', deletingManualAddress.domain)}
          onClose={() => setDeletingManualAddress(null)}
          onConfirm={async () => {
            await api.delete(`/dns/manual-addresses/${deletingManualAddress.id}`)
            setDeletingManualAddress(null)
            await reload()
          }}
        />
      ) : null}
    </>
  )
}

function GatewayManualAddressModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void
  onSubmit: (domain: string, address: string) => Promise<void>
}) {
  const { t } = useI18n()
  const [domain, setDomain] = useState('')
  const [address, setAddress] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!domain.trim() || !address.trim()) {
      setError('Domain and address are required')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit(domain.trim(), address.trim())
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Request failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('addManualReplaceAddress')}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>{t('close')}</button>
        </div>
        <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{t('manualReplaceDescription')}</div>
        {error ? <div className="error-box">{error}</div> : null}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">{t('domains')}</label>
            <input className="form-input mono" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="example.com" autoFocus />
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>{t('manualReplaceDomainHint')}</div>
          </div>
          <div className="form-group">
            <label className="form-label">{t('addressLabel')}</label>
            <input className="form-input mono" value={address} onChange={(event) => setAddress(event.target.value)} placeholder="192.168.1.100" />
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>{t('manualReplaceAddressHint')}</div>
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" type="button" onClick={onClose}>{t('cancel')}</button>
            <button className="btn btn-primary" type="submit" disabled={saving}>{saving ? <span className="spinner" /> : t('add')}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function GatewayDnsDomainModal({
  title,
  zones,
  selectableZones,
  onClose,
  onSubmit,
}: {
  title: string
  zones: any[]
  selectableZones: any[]
  onClose: () => void
  onSubmit: (items: string[], zone: string) => Promise<void>
}) {
  const { t } = useI18n()
  const [value, setValue] = useState('')
  const [zone, setZone] = useState(selectableZones.length ? selectableZones[0].zone : 'local')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!selectableZones.length) {
      setZone('local')
      return
    }
    if (!selectableZones.some((item) => item.zone === zone)) {
      setZone(selectableZones[0].zone)
    }
  }, [selectableZones, zone])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const items = splitDnsItems(value)
    if (items.length === 0) {
      setError('At least one value is required')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit(items, selectableZones.length ? zone : 'local')
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Request failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>{t('close')}</button>
        </div>
        <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{t('domainModalDescription')}</div>
        {error ? <div className="error-box">{error}</div> : null}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">{t('zone')}</label>
            <select
              className="form-input"
              value={selectableZones.length ? zone : 'local'}
              onChange={(event) => setZone(event.target.value)}
              disabled={!selectableZones.length}
            >
              {selectableZones.length ? selectableZones.map((item) => (
                <option key={item.zone} value={item.zone}>{item.name}</option>
              )) : (
                <option value="local">{zones.find((item) => item.zone === 'local')?.name ?? 'Local'}</option>
              )}
            </select>
            {!selectableZones.length ? <div className="text-muted text-sm" style={{ marginTop: 6 }}>{t('onlyBuiltinZonesHint')}</div> : null}
          </div>
          <div className="form-group">
            <textarea className="form-input mono" rows={8} value={value} onChange={(event) => setValue(event.target.value)} placeholder={'example.com\napi.example.com'} />
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" type="button" onClick={onClose}>{t('cancel')}</button>
            <button className="btn btn-primary" type="submit" disabled={saving}>{saving ? <span className="spinner" /> : t('add')}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function GatewayDnsZoneModal({
  title,
  description,
  dotSlotTaken,
  dohSlotTaken,
  onClose,
  onSubmit,
  initialZone,
  editing = false,
}: {
  title: string
  description: string
  dotSlotTaken: boolean
  dohSlotTaken: boolean
  onClose: () => void
  onSubmit: (payload: {
    name: string
    protocol: 'plain' | 'dot' | 'doh'
    servers: string[]
    endpoint_host: string
    endpoint_port: number | null
    endpoint_url: string
    bootstrap_address: string
    domains?: string[]
  }) => Promise<void>
  initialZone?: any
  editing?: boolean
}) {
  const { t } = useI18n()
  const [name, setName] = useState(initialZone?.name ?? '')
  const [protocol, setProtocol] = useState<'plain' | 'dot' | 'doh'>(initialZone?.protocol ?? 'plain')
  const [server, setServer] = useState(initialZone?.servers?.join('\n') ?? '')
  const [endpointHost, setEndpointHost] = useState(initialZone?.endpoint_host ?? '')
  const [endpointPort, setEndpointPort] = useState(initialZone?.endpoint_port ? String(initialZone.endpoint_port) : '853')
  const [endpointUrl, setEndpointUrl] = useState(initialZone?.endpoint_url ?? '')
  const [bootstrapAddress, setBootstrapAddress] = useState(initialZone?.bootstrap_address ?? '')
  const [domains, setDomains] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) {
      setError(t('zoneNameRequired'))
      return
    }
    const validationError = validateGatewayZonePayload(
      {
        protocol,
        servers: splitDnsItems(server),
        endpoint_host: endpointHost.trim(),
        endpoint_port: Number(endpointPort || '853'),
        endpoint_url: endpointUrl.trim(),
        bootstrap_address: bootstrapAddress.trim(),
      },
      {
        dotSlotTaken,
        dohSlotTaken,
        editingProtocol: initialZone?.protocol,
      },
      t,
    )
    if (validationError) {
      setError(validationError)
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit({
        name: name.trim(),
        protocol,
        servers: protocol === 'plain' ? splitDnsItems(server) : [],
        endpoint_host: protocol === 'dot' ? endpointHost.trim() : '',
        endpoint_port: protocol === 'dot' ? Number(endpointPort || '853') : null,
        endpoint_url: protocol === 'doh' ? endpointUrl.trim() : '',
        bootstrap_address: protocol === 'plain' ? '' : bootstrapAddress.trim(),
        domains: editing ? undefined : splitDnsItems(domains),
      })
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Request failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>{t('close')}</button>
        </div>
        <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{description}</div>
        {error ? <div className="error-box">{error}</div> : null}
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">{t('zoneName')}</label>
            <input className="form-input" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </div>
          <div className="form-group">
            <label className="form-label">{t('zoneProtocol')}</label>
            <select className="form-input" value={protocol} onChange={(event) => setProtocol(event.target.value as 'plain' | 'dot' | 'doh')}>
              <option value="plain">{t('dnsProtocolPlain')}</option>
              <option value="dot" disabled={dotSlotTaken}>{t('dnsProtocolDot')}</option>
              <option value="doh" disabled={dohSlotTaken}>{t('dnsProtocolDoh')}</option>
            </select>
          </div>
          {protocol === 'plain' ? (
            <div className="form-group">
              <label className="form-label">{t('zoneDnsServer')}</label>
              <textarea className="form-input mono" rows={4} value={server} onChange={(event) => setServer(event.target.value)} placeholder={'1.2.3.4\n8.8.8.8'} />
            </div>
          ) : null}
          {protocol === 'dot' ? (
            <>
              <div className="form-group">
                <label className="form-label">{t('dnsDotHost')}</label>
                <input className="form-input mono" value={endpointHost} onChange={(event) => setEndpointHost(event.target.value)} placeholder="dns.example.com or 1.1.1.1" />
              </div>
              <div className="form-group">
                <label className="form-label">{t('dnsDotPort')}</label>
                <input className="form-input mono" value={endpointPort} onChange={(event) => setEndpointPort(event.target.value)} placeholder="853" />
              </div>
            </>
          ) : null}
          {protocol === 'doh' ? (
            <div className="form-group">
              <label className="form-label">{t('dnsDohUrl')}</label>
              <input className="form-input mono" value={endpointUrl} onChange={(event) => setEndpointUrl(event.target.value)} placeholder="https://dns.example.com/dns-query" />
            </div>
          ) : null}
          {protocol !== 'plain' ? (
            <div className="form-group">
              <label className="form-label">{t('dnsBootstrapIp')}</label>
              <input className="form-input mono" value={bootstrapAddress} onChange={(event) => setBootstrapAddress(event.target.value)} placeholder="203.0.113.53" />
              <div className="text-muted text-sm" style={{ marginTop: 6 }}>{t('dnsBootstrapHint')}</div>
            </div>
          ) : null}
          {!editing ? (
            <div className="form-group">
              <label className="form-label">{t('domainNames')}</label>
              <textarea className="form-input mono" rows={8} value={domains} onChange={(event) => setDomains(event.target.value)} placeholder={'gemini.com\napi.gemini.com'} />
            </div>
          ) : null}
          <div className="modal-actions">
            <button className="btn btn-secondary" type="button" onClick={onClose}>{t('cancel')}</button>
            <button className="btn btn-primary" type="submit" disabled={saving}>{saving ? <span className="spinner" /> : (editing ? t('save') : t('addZone'))}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function GatewayZoneCard({
  zone,
  onEdit,
  onDelete,
}: {
  zone: any
  onEdit: () => void
  onDelete: () => void
}) {
  const isLocal = zone.zone === 'local'
  const isUpstream = zone.zone === 'vpn'
  const canDelete = !zone.is_builtin && !isLocal && !isUpstream
  const protocolAccent = zone.protocol === 'dot' ? '#f59e0b' : zone.protocol === 'doh' ? '#34d399' : '#c4b5fd'
  return (
    <div className="card" style={{ background: 'var(--bg-3)', padding: 14, minHeight: 164 }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 10 }}>
        <div className="flex items-center gap-2">
          <div style={{
            width: 32,
            height: 32,
            borderRadius: 10,
            display: 'grid',
            placeItems: 'center',
            background: isLocal ? 'var(--accent-dim)' : isUpstream ? 'rgba(56,189,248,0.14)' : 'rgba(167,139,250,0.14)',
            color: isLocal ? 'var(--accent)' : isUpstream ? 'var(--success)' : '#c4b5fd',
          }}>
            {isLocal ? <LocalZoneIcon /> : <UpstreamZoneIcon />}
          </div>
          <div>
            <div style={{ fontWeight: 600 }}>{zone.name}</div>
            <div className="text-muted text-sm">{zone.zone}</div>
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={onEdit}>Edit</button>
          {canDelete ? (
            <button className="btn btn-danger btn-sm" onClick={onDelete}>Delete</button>
          ) : null}
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <span className="badge" style={{ border: `1px solid ${protocolAccent}`, color: protocolAccent }}>
          {gatewayProtocolLabel(zone.protocol, false)}
        </span>
      </div>
      <div className="mono" style={{ fontSize: 13, wordBreak: 'break-word' }}>{renderGatewayZoneTarget(zone)}</div>
      {zone.bootstrap_address ? <div className="text-muted text-sm" style={{ marginTop: 6 }}>bootstrap {zone.bootstrap_address}</div> : null}
    </div>
  )
}

function GatewayDeleteZoneModal({
  zone,
  onClose,
  onConfirm,
}: {
  zone: any
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const { t } = useI18n()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{t('deleteZoneTitle')}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>{t('close')}</button>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        <div style={{ fontSize: 14 }}>
          {t('deleteZoneConfirm').replace('{name}', zone.name)}
        </div>
        <div className="modal-actions">
          <button className="btn btn-secondary" type="button" onClick={onClose} disabled={saving}>{t('cancel')}</button>
          <button
            className="btn btn-danger"
            type="button"
            disabled={saving}
            onClick={async () => {
              setSaving(true)
              setError('')
              try {
                await onConfirm()
              } catch (err: any) {
                setError(err?.response?.data?.detail || err.message || 'Request failed')
                setSaving(false)
              }
            }}
          >
            {saving ? <span className="spinner" /> : t('delete')}
          </button>
        </div>
      </div>
    </div>
  )
}

function GatewayDeleteConfirmModal({
  title,
  message,
  onClose,
  onConfirm,
}: {
  title: string
  message: string
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const { t } = useI18n()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>{t('close')}</button>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        <div style={{ fontSize: 14 }}>{message}</div>
        <div className="modal-actions">
          <button className="btn btn-secondary" type="button" onClick={onClose} disabled={saving}>{t('cancel')}</button>
          <button
            className="btn btn-danger"
            type="button"
            disabled={saving}
            onClick={async () => {
              setSaving(true)
              setError('')
              try {
                await onConfirm()
              } catch (err: any) {
                setError(err?.response?.data?.detail || err.message || 'Request failed')
                setSaving(false)
              }
            }}
          >
            {saving ? <span className="spinner" /> : t('delete')}
          </button>
        </div>
      </div>
    </div>
  )
}

function GatewayZoneBadge({ zone, zoneKey }: { zone?: any; zoneKey: string }) {
  const isLocal = zoneKey === 'local'
  const isUpstream = zoneKey === 'vpn'
  return (
    <span className={`badge ${isLocal ? 'badge-pending' : isUpstream ? 'badge-online' : 'badge-warning'}`}>
      {(zone?.name ?? zoneKey) + (zone ? ` · ${gatewayProtocolLabel(zone.protocol, true)}` : '')}
    </span>
  )
}

function GatewayStatusChip({ label, running, details }: { label: string; running: boolean; details: string }) {
  return (
    <div style={{ minWidth: 140 }}>
      <div className="text-muted text-sm">{label}</div>
      <div style={{ fontWeight: 600, fontSize: 14, color: running ? undefined : 'var(--danger)' }}>{running ? 'running' : 'stopped'}</div>
      <div className="mono text-sm">{details}</div>
    </div>
  )
}

function gatewayZoneColumnsClass(count: number) {
  if (count >= 3 && count % 3 === 0) return 'card-grid-3'
  if (count >= 2 && count % 2 === 0) return 'card-grid-2'
  return 'card-grid-3'
}

function splitDnsItems(value: string) {
  return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean)
}

function renderGatewayZoneTarget(zone?: any) {
  if (!zone) return '—'
  if (zone.protocol === 'dot') return `${zone.endpoint_host}:${zone.endpoint_port ?? 853}`
  if (zone.protocol === 'doh') return zone.endpoint_url || '—'
  return (zone.servers || []).join(', ') || '—'
}

function gatewayProtocolLabel(protocol: string, short = false) {
  if (protocol === 'dot') return short ? 'DoT' : 'DNS over TLS (DoT)'
  if (protocol === 'doh') return short ? 'DoH' : 'DNS over HTTPS (DoH)'
  return short ? 'DNS' : 'Plain DNS'
}

function validateGatewayZonePayload(
  payload: {
    protocol: 'plain' | 'dot' | 'doh'
    servers: string[]
    endpoint_host: string
    endpoint_port: number
    endpoint_url: string
    bootstrap_address: string
  },
  limits: {
    dotSlotTaken: boolean
    dohSlotTaken: boolean
    editingProtocol?: 'plain' | 'dot' | 'doh'
  },
  t: (key: any) => string,
) {
  if (payload.protocol === 'plain') {
    if (!payload.servers.length) return t('dnsPlainRequired')
    if (!payload.servers.every((item) => isGatewayDnsServer(item))) return t('dnsPlainInvalid')
    return ''
  }
  if (payload.protocol === 'dot') {
    if (limits.dotSlotTaken && limits.editingProtocol !== 'dot') return t('dnsDotSlotTaken')
    if (!payload.endpoint_host.trim()) return t('dnsDotHostRequired')
    if (!isGatewayDnsServer(payload.endpoint_host)) return t('dnsDotHostInvalid')
    if (!Number.isInteger(payload.endpoint_port) || payload.endpoint_port < 1 || payload.endpoint_port > 65535) return t('dnsDotPortInvalid')
    if (!isGatewayIp(payload.endpoint_host) && !isGatewayIp(payload.bootstrap_address)) return t('dnsBootstrapRequired')
    return ''
  }
  if (limits.dohSlotTaken && limits.editingProtocol !== 'doh') return t('dnsDohSlotTaken')
  try {
    const parsed = new URL(payload.endpoint_url)
    if (parsed.protocol !== 'https:') return t('dnsDohUrlInvalid')
    if (!isGatewayIp(parsed.hostname) && !isGatewayIp(payload.bootstrap_address)) return t('dnsBootstrapRequired')
    return ''
  } catch {
    return t('dnsDohUrlInvalid')
  }
}

function isGatewayIp(value: string) {
  if (!value.trim()) return false
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(value)) {
    return value.split('.').every((part) => Number(part) >= 0 && Number(part) <= 255)
  }
  return value.includes(':') && /^[0-9a-fA-F:]+$/.test(value)
}

function isGatewayDnsServer(value: string) {
  const candidate = value.trim().toLowerCase()
  return isGatewayIp(candidate) || /^(?=.{1,253}$)(?!-)(?:[a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}\.?$/i.test(candidate)
}

function LocalZoneIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 11.5L12 4l9 7.5" />
      <path d="M5 10.5V20h14v-9.5" />
      <path d="M9 20v-6h6v6" />
    </svg>
  )
}

function UpstreamZoneIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3z" />
      <path d="M9.5 12.5l1.8 1.8 3.2-4.3" />
    </svg>
  )
}

function TrashIconSmall() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4h6v2" />
    </svg>
  )
}

function BackupPage() {
  const { t } = useI18n()
  const { data, reload } = useLoader<any[]>('/backup/list', [])
  const [message, setMessage] = useState('')

  async function downloadBackup() {
    const response = await api.get('/backup/export', { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = 'awg-gateway-backup.zip'
    link.click()
    URL.revokeObjectURL(url)
    setMessage(t('backupDownloaded'))
    await reload()
  }

  async function restoreBackup(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    await api.post('/backup/restore', form)
    setMessage(t('backupRestored'))
    await reload()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('backup')}</div>
          <div className="page-subtitle">{t('backupSubtitle')}</div>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      <div className="flex gap-2" style={{ marginBottom: 20 }}>
        <button className="btn btn-primary" onClick={() => void downloadBackup()}>{t('exportBackup')}</button>
        <label className="btn btn-secondary">
          {t('restoreBackup')}
          <input type="file" hidden onChange={restoreBackup} />
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t('filename')}</th>
              <th>{t('type')}</th>
              <th>{t('size')}</th>
              <th>{t('createdAt')}</th>
            </tr>
          </thead>
          <tbody>
            {data.length === 0 ? (
              <tr><td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No backup records yet</td></tr>
            ) : data.map((item) => (
              <tr key={item.id}>
                <td>{item.filename}</td>
                <td>{item.kind}</td>
                <td>{item.size_bytes} bytes</td>
                <td>{new Date(item.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function DevicesPage() {
  const { t } = useI18n()
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<'name' | 'status' | 'mac' | 'ip' | 'last_traffic' | 'last_seen'>('last_seen')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [editingDevice, setEditingDevice] = useState<DeviceRecord | null>(null)
  const [editingAlias, setEditingAlias] = useState('')
  const [aliasSaving, setAliasSaving] = useState(false)
  const { data, loading, error, reload } = useLoader<DeviceListResponse>(
    `/devices?scope=all&status=all&search=${encodeURIComponent(search)}`,
    {
      scope: 'all',
      status: 'all',
      search: '',
      summary: { total: 0, all_devices: 0, marked: 0, active: 0, present: 0, inactive: 0 },
      devices: [],
    },
  )
  useBackgroundReload(reload, 10_000)

  function updateSort(nextKey: typeof sortKey) {
    if (nextKey === sortKey) {
      setSortDir((value) => (value === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortKey(nextKey)
    setSortDir(nextKey === 'name' || nextKey === 'status' || nextKey === 'mac' || nextKey === 'ip' ? 'asc' : 'desc')
  }

  async function toggleMarked(device: DeviceRecord) {
    await api.patch(`/devices/${device.id}`, { is_marked: !device.is_marked })
    await reload()
  }

  function openAliasEditor(device: DeviceRecord) {
    setEditingDevice(device)
    setEditingAlias(device.manual_alias)
  }

  async function saveAlias() {
    if (!editingDevice) return
    setAliasSaving(true)
    await api.patch(`/devices/${editingDevice.id}`, { manual_alias: editingAlias })
    setAliasSaving(false)
    setEditingDevice(null)
    await reload()
  }

  const sortedDevices = [...data.devices].sort((left, right) => {
    if (left.is_marked !== right.is_marked) {
      return left.is_marked ? -1 : 1
    }

    const direction = sortDir === 'asc' ? 1 : -1
    const stateRank = (device: DeviceRecord) => {
      if (device.presence_state === 'active') return 0
      if (device.presence_state === 'present') return 1
      return 2
    }
    const timestamp = (value: string | null) => parseUtcDate(value)?.getTime() ?? 0
    const ipToTuple = (value: string | null) => {
      if (!value) return [-1, -1, -1, -1]
      const parts = value.split('.').map((item) => Number(item))
      if (parts.length !== 4 || parts.some((item) => Number.isNaN(item))) return [-1, -1, -1, -1]
      return parts
    }
    const compareIp = (leftIp: string | null, rightIp: string | null) => {
      const leftParts = ipToTuple(leftIp)
      const rightParts = ipToTuple(rightIp)
      for (let index = 0; index < 4; index += 1) {
        if (leftParts[index] !== rightParts[index]) return leftParts[index] - rightParts[index]
      }
      return 0
    }

    let result = 0
    if (sortKey === 'name') result = left.display_name.localeCompare(right.display_name, undefined, { sensitivity: 'base' })
    if (sortKey === 'status') result = stateRank(left) - stateRank(right)
    if (sortKey === 'mac') result = (left.mac_address || '').localeCompare(right.mac_address || '', undefined, { sensitivity: 'base' })
    if (sortKey === 'ip') result = compareIp(left.current_ip, right.current_ip)
    if (sortKey === 'last_traffic') result = timestamp(left.last_traffic_at) - timestamp(right.last_traffic_at)
    if (sortKey === 'last_seen') result = timestamp(left.last_present_at || left.last_seen_at) - timestamp(right.last_present_at || right.last_seen_at)
    if (result !== 0) return result * direction
    return right.id - left.id
  })

  function renderSortableHeader(label: string, key: typeof sortKey) {
    const active = sortKey === key
    return (
      <button
        type="button"
        className={`table-sort-btn${active ? ' active' : ''}`}
        onClick={() => updateSort(key)}
      >
        <span>{label}</span>
        <span className="table-sort-indicator">{active ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}</span>
      </button>
    )
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('devices')}</div>
        </div>
      </div>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card-grid card-grid-4" style={{ marginBottom: 20 }}>
        <StatCard title={t('trackedDevices')} value={String(data.summary.total)} label={t('filtered')} />
        <StatCard title={t('markedDevices')} value={String(data.summary.marked)} label={t('manualSelection')} />
        <StatCard title={t('activeDevices')} value={String(data.summary.active)} label={t('recentTraffic')} tone="online" />
        <StatCard title={t('presentDevices')} value={String(data.summary.present)} label={t('networkPresence')} />
      </div>
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label className="form-label">{t('search')}</label>
          <input
            className="form-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('deviceSearchPlaceholder')}
          />
        </div>
      </div>
      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
      <SimpleTable
        headers={[
          renderSortableHeader(t('name'), 'name'),
          renderSortableHeader(t('status'), 'status'),
          renderSortableHeader('MAC', 'mac'),
          renderSortableHeader('IP', 'ip'),
          renderSortableHeader(t('lastTraffic'), 'last_traffic'),
          renderSortableHeader(t('lastSeen'), 'last_seen'),
          t('actions'),
        ]}
        emptyText={t('noDevicesTracked')}
        rowClassName={(rowIndex) => (sortedDevices[rowIndex]?.is_marked ? 'device-row-marked' : '')}
        rows={sortedDevices.map((device) => [
          <div key={`identity-${device.id}`}>
            <div className="device-identity-row">
              <div style={{ fontWeight: 600 }}>{device.display_name}</div>
              <button
                type="button"
                className={`device-api-toggle${device.is_marked ? ' active' : ''}`}
                onClick={() => { void toggleMarked(device) }}
              >
                {t('apiAccessTitle')}
              </button>
            </div>
            <div className="text-muted text-sm">{device.hostname || device.identity_key}</div>
          </div>,
          <div key={`state-${device.id}`}>
            <span className={`badge ${device.presence_state === 'active' ? 'badge-online' : device.presence_state === 'present' ? 'badge-warning' : 'badge-offline'}`}>
              {device.presence_state === 'active' ? t('deviceStateActive') : device.presence_state === 'present' ? t('deviceStatePresent') : t('deviceStateInactive')}
            </span>
          </div>,
          <span key={`mac-${device.id}`} className="text-mono">{device.mac_address || '—'}</span>,
          <span key={`ip-${device.id}`} className="text-mono">{device.current_ip || '—'}</span>,
          <span key={`traffic-${device.id}`}>{fmtDateTime(device.last_traffic_at)}</span>,
          <span key={`seen-${device.id}`}>{fmtDateTime(device.last_present_at || device.last_seen_at)}</span>,
          <div key={`actions-${device.id}`} className="flex gap-2">
            <button className="btn btn-ghost btn-sm" onClick={() => { openAliasEditor(device) }}>
              {t('edit')}
            </button>
          </div>,
        ])}
      />
      {editingDevice ? (
        <div className="modal-overlay" onClick={() => { if (!aliasSaving) setEditingDevice(null) }}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">{t('edit')} {t('deviceAliasPrompt')}</div>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditingDevice(null)} disabled={aliasSaving}>{t('close')}</button>
            </div>
            <div className="form-group">
              <label className="form-label">{t('deviceAliasPrompt')}</label>
              <input
                className="form-input"
                value={editingAlias}
                onChange={(event) => setEditingAlias(event.target.value)}
                placeholder={editingDevice.display_name}
                autoFocus
              />
            </div>
            <div className="text-muted text-sm">
              {editingDevice.hostname || editingDevice.current_ip || editingDevice.identity_key}
            </div>
            <div className="modal-actions">
              <button className="btn btn-secondary" type="button" onClick={() => setEditingDevice(null)} disabled={aliasSaving}>{t('cancel')}</button>
              <button className="btn btn-primary" type="button" onClick={() => { void saveAlias() }} disabled={aliasSaving}>
                {aliasSaving ? <span className="spinner" /> : t('save')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}

function SettingsPage() {
  const { locale, setLocale, t } = useI18n()
  const { data, reload, setData } = useLoader<GatewaySettingsData>('/settings', {
    ui_language: 'en',
    runtime_mode: 'auto',
    allowed_client_cidrs: [LOCALHOST_SOURCE],
    gateway_enabled: true,
    dns_intercept_enabled: true,
    experimental_nftables: false,
    device_tracking_enabled: true,
    device_activity_timeout_seconds: 300,
    failover_enabled: false,
    kernel_available: false,
    kernel_message: null,
    external_ip_info: {
      refresh_interval_seconds: 600,
      forced_domains: [],
      local: { service_url: '', service_host: null, value: null, error: null, checked_at: null, route_target: 'local' },
      vpn: { service_url: '', service_host: null, value: null, error: null, checked_at: null, route_target: 'vpn' },
    },
    api_settings: {
      api_enabled: false,
      api_access_key: null,
      api_control_enabled: false,
      api_allowed_client_cidrs: [],
      device_api_default_scope: 'all',
    },
  })
  const [sourceInput, setSourceInput] = useState('')
  const [apiAllowedIpInput, setApiAllowedIpInput] = useState('')
  const [localExternalIpServiceUrl, setLocalExternalIpServiceUrl] = useState('')
  const [vpnExternalIpServiceUrl, setVpnExternalIpServiceUrl] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    setLocalExternalIpServiceUrl(data.external_ip_info.local.service_url || '')
    setVpnExternalIpServiceUrl(data.external_ip_info.vpn.service_url || '')
  }, [data.external_ip_info.local.service_url, data.external_ip_info.vpn.service_url])

  function buildSettingsPayload(overrides: Record<string, unknown> = {}) {
    return {
      ui_language: locale,
      runtime_mode: data.runtime_mode,
      allowed_client_cidrs: data.allowed_client_cidrs,
      dns_intercept_enabled: data.dns_intercept_enabled,
      experimental_nftables: data.experimental_nftables,
      device_tracking_enabled: data.device_tracking_enabled,
      device_activity_timeout_seconds: data.device_activity_timeout_seconds,
      external_ip_local_service_url: localExternalIpServiceUrl,
      external_ip_vpn_service_url: vpnExternalIpServiceUrl,
      ...overrides,
    }
  }

  const localhostEnabled = data.allowed_client_cidrs.includes(LOCALHOST_SOURCE)
  const customSourceCidrs = data.allowed_client_cidrs.filter((cidr) => cidr !== LOCALHOST_SOURCE)

  async function saveSettings(event: FormEvent) {
    event.preventDefault()
    await api.put('/settings', buildSettingsPayload())
    setMessage(t('settingsSaved'))
    await reload()
  }

  async function addTrafficSource() {
    const candidate = sourceInput.trim()
    if (!candidate) return

    try {
      await api.put('/settings', buildSettingsPayload({ allowed_client_cidrs: [...data.allowed_client_cidrs, candidate] }))
      setSourceInput('')
      setMessage(t('settingsSaved'))
      await reload()
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function toggleLocalhostSource(enabled: boolean) {
    const nextCidrs = enabled
      ? [LOCALHOST_SOURCE, ...customSourceCidrs]
      : customSourceCidrs
    try {
      await api.put('/settings', buildSettingsPayload({ allowed_client_cidrs: nextCidrs }))
      setMessage(t('settingsSaved'))
      await reload()
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function removeTrafficSource(cidr: string) {
    try {
      await api.put('/settings', buildSettingsPayload({ allowed_client_cidrs: data.allowed_client_cidrs.filter((item) => item !== cidr) }))
      setMessage(t('settingsSaved'))
      await reload()
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function toggleDnsInterception(enabled: boolean) {
    const previous = Boolean(data.dns_intercept_enabled)
    setData({ ...data, dns_intercept_enabled: enabled })
    try {
      await api.put('/settings', buildSettingsPayload({ dns_intercept_enabled: enabled }))
      setMessage(t('dnsInterceptionUpdated'))
      await reload()
    } catch (err: any) {
      setData({ ...data, dns_intercept_enabled: previous })
      setMessage(err?.response?.data?.detail || err.message || t('failedToUpdateDnsInterception'))
    }
  }

  async function toggleExperimentalRouting(enabled: boolean) {
    const previous = Boolean(data.experimental_nftables)
    setData({ ...data, experimental_nftables: enabled })
    try {
      await api.put('/settings', buildSettingsPayload({ experimental_nftables: enabled }))
      setMessage(t('settingsSaved'))
      await reload()
    } catch (err: any) {
      setData({ ...data, experimental_nftables: previous })
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault()
    await api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword })
    setCurrentPassword('')
    setNewPassword('')
    setMessage(t('passwordChanged'))
  }

  async function updateApiAccess(apiEnabled: boolean, apiControlEnabled: boolean) {
    const previous = data.api_settings
    const nextState = {
      api_enabled: apiEnabled,
      api_access_key: previous.api_access_key,
      api_control_enabled: apiEnabled ? apiControlEnabled : false,
      api_allowed_client_cidrs: previous.api_allowed_client_cidrs,
      device_api_default_scope: previous.device_api_default_scope,
    }
    setData({ ...data, api_settings: nextState })
    try {
      const response = await api.put('/settings/api-access', {
        api_enabled: apiEnabled,
        api_control_enabled: apiEnabled ? apiControlEnabled : false,
        api_allowed_client_cidrs: previous.api_allowed_client_cidrs,
        device_api_default_scope: previous.device_api_default_scope,
      })
      setData({ ...data, api_settings: response.data.api_settings })
      setMessage(t('apiAccessUpdated'))
    } catch (err: any) {
      setData({ ...data, api_settings: previous })
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function regenerateApiAccessKey() {
    const response = await api.post('/settings/api-access/regenerate')
    setData({ ...data, api_settings: response.data.api_settings })
    setMessage(t('apiAccessRegenerated'))
  }

  async function addApiAllowedIp() {
    const candidate = apiAllowedIpInput.trim()
    if (!candidate) return
    try {
      const response = await api.put('/settings/api-access', {
        api_enabled: data.api_settings.api_enabled,
        api_control_enabled: data.api_settings.api_control_enabled,
        api_allowed_client_cidrs: [...data.api_settings.api_allowed_client_cidrs, candidate],
        device_api_default_scope: data.api_settings.device_api_default_scope,
      })
      setData({ ...data, api_settings: response.data.api_settings })
      setApiAllowedIpInput('')
      setMessage(t('apiAccessUpdated'))
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function removeApiAllowedIp(cidr: string) {
    try {
      const response = await api.put('/settings/api-access', {
        api_enabled: data.api_settings.api_enabled,
        api_control_enabled: data.api_settings.api_control_enabled,
        api_allowed_client_cidrs: data.api_settings.api_allowed_client_cidrs.filter((item) => item !== cidr),
        device_api_default_scope: data.api_settings.device_api_default_scope,
      })
      setData({ ...data, api_settings: response.data.api_settings })
      setMessage(t('apiAccessUpdated'))
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  async function updateDeviceApiScope(nextScope: 'all' | 'marked') {
    try {
      const response = await api.put('/settings/api-access', {
        api_enabled: data.api_settings.api_enabled,
        api_control_enabled: data.api_settings.api_control_enabled,
        api_allowed_client_cidrs: data.api_settings.api_allowed_client_cidrs,
        device_api_default_scope: nextScope,
      })
      setData({ ...data, api_settings: response.data.api_settings })
      setMessage(t('apiAccessUpdated'))
    } catch (err: any) {
      setMessage(err?.response?.data?.detail || err.message || 'Request failed')
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('settings')}</div>
          <div className="page-subtitle">{t('settingsSubtitle')}</div>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      <div className="card-grid card-grid-2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>{t('language')}</div>
          <form onSubmit={saveSettings}>
            <div className="form-group">
              <label className="form-label">{t('language')}</label>
              <select className="form-input" value={locale} onChange={(event) => setLocale(event.target.value as 'en' | 'ru')}>
                <option value="en">English</option>
                <option value="ru">Русский</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">{t('runtimeMode')}</label>
              <select className="form-input" value={data.runtime_mode} onChange={(event) => setData({ ...data, runtime_mode: event.target.value })}>
                <option value="auto">{t('runtimeModeAuto')}</option>
                <option value="kernel">{t('runtimeModeKernel')}</option>
                <option value="userspace">{t('runtimeModeUserspace')}</option>
              </select>
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>
                {t('kernelModeStatus')}: {data.kernel_available ? t('available') : t('unavailable')}
              </div>
              {!data.kernel_available && data.kernel_message ? (
                <div className="text-muted text-sm" style={{ marginTop: 4 }}>{data.kernel_message}</div>
              ) : null}
            </div>
            <div className="form-group">
              <label className="form-label">{t('sourceMode')}</label>
              <div className="text-muted text-sm" style={{ marginBottom: 8 }}>{t('sourceNetworksDescription')}</div>
              <div className="source-chip-list">
                <div className={`source-chip${localhostEnabled ? '' : ' source-chip-inactive'}`}>
                  <span className="text-mono">{LOCALHOST_SOURCE}</span>
                  <button
                    type="button"
                    className="source-chip-remove"
                    aria-label={`${localhostEnabled ? t('remove') : t('add')} ${LOCALHOST_SOURCE}`}
                    onClick={() => { void toggleLocalhostSource(!localhostEnabled) }}
                  >
                    {localhostEnabled ? '×' : '+'}
                  </button>
                </div>
                {customSourceCidrs.length ? customSourceCidrs.map((cidr) => (
                  <div className="source-chip" key={cidr}>
                    <span className="text-mono">{cidr}</span>
                    <button
                      type="button"
                      className="source-chip-remove"
                      aria-label={`${t('remove')} ${cidr}`}
                      onClick={() => { void removeTrafficSource(cidr) }}
                    >
                      ×
                    </button>
                  </div>
                )) : null}
              </div>
              <div className="form-row form-row-2 source-input-row">
                <input
                  className="form-input mono"
                  value={sourceInput}
                  onChange={(event) => setSourceInput(event.target.value)}
                  placeholder={t('sourceNetworkPlaceholder')}
                />
                <button className="btn btn-secondary" type="button" onClick={() => { void addTrafficSource() }}>{t('add')}</button>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('externalIpServices')}</label>
              <div className="text-muted text-sm" style={{ marginBottom: 8 }}>{t('externalIpServicesDescription')}</div>
              <div className="form-row form-row-2">
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label className="form-label">{t('localExternalIpService')}</label>
                  <input
                    className="form-input mono"
                    value={localExternalIpServiceUrl}
                    onChange={(event) => setLocalExternalIpServiceUrl(event.target.value)}
                    placeholder="https://ipinfo.io/ip"
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label className="form-label">{t('vpnExternalIpService')}</label>
                  <input
                    className="form-input mono"
                    value={vpnExternalIpServiceUrl}
                    onChange={(event) => setVpnExternalIpServiceUrl(event.target.value)}
                    placeholder="https://ifconfig.me/ip"
                  />
                </div>
              </div>
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>
                {t('forcedDomains')}: {data.external_ip_info.forced_domains.length ? data.external_ip_info.forced_domains.join(', ') : '—'}
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('dnsInterception')}</label>
              <label className="toggle" title={t('dnsInterception')}>
                <input
                  type="checkbox"
                  checked={Boolean(data.dns_intercept_enabled)}
                  onChange={(event) => { void toggleDnsInterception(event.target.checked) }}
                />
                <span className="toggle-slider" />
              </label>
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>{t('dnsInterceptionDescription')}</div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('experimentalRouting')}</label>
              <label className="toggle" title={t('experimentalRouting')}>
                <input
                  type="checkbox"
                  checked={Boolean(data.experimental_nftables)}
                  onChange={(event) => { void toggleExperimentalRouting(event.target.checked) }}
                />
                <span className="toggle-slider" />
              </label>
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>{t('experimentalRoutingDescription')}</div>
              <div className="text-muted text-sm" style={{ marginTop: 4 }}>
                {t('firewallBackend')}: {firewallBackendLabel(data.experimental_nftables ? 'nftables' : 'iptables', t)}
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('deviceTracking')}</label>
              <label className="toggle" title={t('deviceTracking')}>
                <input
                  type="checkbox"
                  checked={Boolean(data.device_tracking_enabled)}
                  onChange={(event) => setData({ ...data, device_tracking_enabled: event.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>{t('deviceTrackingDescription')}</div>
            </div>
            <div className="form-group">
              <label className="form-label">{t('deviceActivityTimeout')}</label>
              <input
                className="form-input mono"
                type="number"
                min={30}
                step={30}
                value={data.device_activity_timeout_seconds}
                onChange={(event) => setData({ ...data, device_activity_timeout_seconds: Number(event.target.value || 300) })}
              />
              <div className="text-muted text-sm" style={{ marginTop: 8 }}>{t('deviceActivityTimeoutDescription')}</div>
            </div>
            <button className="btn btn-primary" type="submit">{t('save')}</button>
          </form>
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>{t('changePassword')}</div>
          <form onSubmit={changePassword}>
            <div className="form-group">
              <label className="form-label">{t('currentPassword')}</label>
              <input className="form-input" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">{t('newPassword')}</label>
              <input className="form-input" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
            </div>
            <button className="btn btn-primary" type="submit">{t('save')}</button>
          </form>

          <div style={{ height: 1, background: 'var(--border)', margin: '22px 0' }} />

          <div className="card-title" style={{ marginBottom: 14 }}>{t('apiAccessTitle')}</div>
          <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{t('apiAccessDescription')}</div>
          <div className="form-group">
            <label className="form-label">{t('apiAccessEnabled')}</label>
            <label className="toggle" title={t('apiAccessEnabled')}>
              <input
                type="checkbox"
                checked={Boolean(data.api_settings.api_enabled)}
                onChange={(event) => { void updateApiAccess(event.target.checked, data.api_settings.api_control_enabled) }}
              />
              <span className="toggle-slider" />
            </label>
          </div>
          <div className="form-group">
            <label className="form-label">{t('apiAccessKey')}</label>
            <div className="form-row form-row-2 source-input-row">
              <input
                className="form-input mono"
                value={data.api_settings.api_access_key || ''}
                readOnly
                placeholder={t('apiAccessKeyPlaceholder')}
              />
              <button
                className="btn btn-secondary"
                type="button"
                disabled={!data.api_settings.api_enabled}
                onClick={() => { void regenerateApiAccessKey() }}
              >
                {t('regenerate')}
              </button>
            </div>
            <div className="text-muted text-sm" style={{ marginTop: 8 }}>
              {data.api_settings.api_enabled ? t('apiAccessKeyGenerated') : ''}
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">{t('apiControlMode')}</label>
            <label className="toggle" title={t('apiControlMode')}>
              <input
                type="checkbox"
                checked={Boolean(data.api_settings.api_enabled && data.api_settings.api_control_enabled)}
                disabled={!data.api_settings.api_enabled}
                onChange={(event) => { void updateApiAccess(data.api_settings.api_enabled, event.target.checked) }}
              />
              <span className="toggle-slider" />
            </label>
            <div className="text-muted text-sm" style={{ marginTop: 8 }}>
              {data.api_settings.api_control_enabled ? t('apiControlEnabledDescription') : t('apiReadOnlyDescription')}
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">{t('apiAllowedIps')}</label>
            <div className="text-muted text-sm" style={{ marginBottom: 8 }}>{t('apiAllowedIpsDescription')}</div>
            <div className="source-chip-list">
              {data.api_settings.api_allowed_client_cidrs.length ? data.api_settings.api_allowed_client_cidrs.map((cidr) => (
                <div className="source-chip" key={cidr}>
                  <span className="text-mono">{cidr}</span>
                  <button
                    type="button"
                    className="source-chip-remove"
                    aria-label={`${t('remove')} ${cidr}`}
                    onClick={() => { void removeApiAllowedIp(cidr) }}
                  >
                    ×
                  </button>
                </div>
              )) : <div className="text-muted text-sm">{t('apiAllowedIpsEmpty')}</div>}
            </div>
            <div className="form-row form-row-2 source-input-row">
              <input
                className="form-input mono"
                value={apiAllowedIpInput}
                onChange={(event) => setApiAllowedIpInput(event.target.value)}
                placeholder={t('apiAllowedIpPlaceholder')}
              />
              <button className="btn btn-secondary" type="button" onClick={() => { void addApiAllowedIp() }}>{t('add')}</button>
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">{t('deviceApiDefaultScope')}</label>
            <select
              className="form-input"
              value={data.api_settings.device_api_default_scope}
              onChange={(event) => { void updateDeviceApiScope(event.target.value as 'all' | 'marked') }}
            >
              <option value="all">{t('allDevices')}</option>
              <option value="marked">{t('markedDevicesOnly')}</option>
            </select>
            <div className="text-muted text-sm" style={{ marginTop: 8 }}>{t('deviceApiDefaultScopeDescription')}</div>
          </div>
        </div>
      </div>
    </>
  )
}

function DiagnosticsPage() {
  const { t } = useI18n()
  const { data, reload } = useLoader<any>('/backup/diagnostics', { manifest: {}, routing_plan: {}, dns_preview: '' })
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('diagnostics')}</div>
          <div className="page-subtitle">{t('diagnosticsSubtitle')}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => void reload()}>{t('diagnosticsBundle')}</button>
      </div>
      <div className="terminal">
        <div className="terminal-line">
          <span className="msg" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(data, null, 2)}</span>
        </div>
      </div>
    </>
  )
}

function PolicyBlock({
  title,
  description,
  enabled,
  onToggle,
  onAdd,
  addLabel,
  actions,
  children,
}: {
  title: string
  description: string
  enabled: boolean
  onToggle: (value: boolean) => void
  onAdd: () => void
  addLabel: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="card policy-block">
      <div className="card-header" style={{ marginBottom: 14 }}>
        <div>
          <div className="routing-diagram-title">{title}</div>
          <div className="routing-diagram-subtitle">{description}</div>
        </div>
        <div className="flex items-center gap-2">
          {actions}
          <label className="toggle" title={title}>
            <input type="checkbox" checked={enabled} onChange={(event) => onToggle(event.target.checked)} />
            <span className="toggle-slider" />
          </label>
          <button className="btn btn-primary btn-sm" onClick={onAdd}>{addLabel}</button>
        </div>
      </div>
      {children}
    </div>
  )
}

function SimpleTable({
  headers,
  rows,
  emptyText,
  rowClassName,
}: {
  headers: React.ReactNode[]
  rows: React.ReactNode[][]
  emptyText: string
  rowClassName?: (rowIndex: number) => string
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {headers.map((header, index) => <th key={index}>{header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={headers.length} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>{emptyText}</td></tr>
          ) : rows.map((row, rowIndex) => (
            <tr key={rowIndex} className={rowClassName?.(rowIndex) || ''}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ListModal({
  title,
  description,
  placeholder,
  submitLabel,
  onClose,
  onSubmit,
}: {
  title: string
  description: string
  placeholder: string
  submitLabel: string
  onClose: () => void
  onSubmit: (items: string[]) => Promise<void>
}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const items = value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean)
    if (items.length === 0) {
      setError('At least one value is required')
      return
    }
    setSaving(true)
    setError('')
    try {
      await onSubmit(items)
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Request failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">{title}</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
        <div className="text-muted text-sm" style={{ marginBottom: 14 }}>{description}</div>
        {error ? <div className="error-box">{error}</div> : null}
        <form onSubmit={submit}>
          <div className="form-group">
            <textarea className="form-input mono" rows={8} value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} />
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" type="button" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" type="submit" disabled={saving}>{saving ? <span className="spinner" /> : submitLabel}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function MetricChartCard({
  title,
  value,
  subtitle,
  chip,
  points,
  period,
  mode,
}: {
  title: string
  value: string
  subtitle: string
  chip: string
  points: MetricsPoint[]
  period: '1h' | '24h'
  mode: 'cpu' | 'memory'
}) {
  return (
    <div className="card metric-card">
      <div className="metric-card-header">
        <div>
          <div className="card-title" style={{ marginBottom: 8 }}>{title}</div>
          <div className="stat-value" style={{ fontSize: 22 }}>{value}</div>
          <div className="stat-label">{subtitle}</div>
        </div>
        <div className="metric-chip">{chip}</div>
      </div>
      <div className="metric-chart-wrap">
        <ResponsiveContainer width="100%" height="100%">
          {mode === 'cpu' ? (
            <LineChart data={points}>
              <CartesianGrid stroke="rgba(139, 148, 158, 0.12)" vertical={false} />
              <XAxis
                dataKey="collected_at"
                minTickGap={28}
                stroke="var(--text-3)"
                tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                tickFormatter={(value) => fmtMetricTime(String(value), period)}
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(value) => `${value}%`}
                stroke="var(--text-3)"
                tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                width={42}
              />
              <Tooltip content={<MetricsTooltip type="cpu" period={period} />} />
              <Line
                type="monotone"
                dataKey="cpu_usage_percent"
                stroke="var(--accent)"
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--accent)' }}
              />
            </LineChart>
          ) : (
            <AreaChart data={points}>
              <defs>
                <linearGradient id="gatewayMemoryUsedFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="rgba(245, 158, 11, 0.38)" />
                  <stop offset="100%" stopColor="rgba(245, 158, 11, 0.05)" />
                </linearGradient>
                <linearGradient id="gatewayMemoryFreeFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="rgba(56, 189, 248, 0.30)" />
                  <stop offset="100%" stopColor="rgba(56, 189, 248, 0.04)" />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(139, 148, 158, 0.12)" vertical={false} />
              <XAxis
                dataKey="collected_at"
                minTickGap={28}
                stroke="var(--text-3)"
                tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                tickFormatter={(value) => fmtMetricTime(String(value), period)}
              />
              <YAxis
                tickFormatter={(value) => fmtBytes(Number(value))}
                stroke="var(--text-3)"
                tick={{ fontSize: 11, fill: 'var(--text-3)' }}
                width={56}
              />
              <Tooltip content={<MetricsTooltip type="memory" period={period} />} />
              <Area
                type="monotone"
                dataKey="memory_free_bytes"
                stroke="var(--success)"
                fill="url(#gatewayMemoryFreeFill)"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--success)' }}
              />
              <Area
                type="monotone"
                dataKey="memory_used_bytes"
                stroke="var(--accent)"
                fill="url(#gatewayMemoryUsedFill)"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--accent)' }}
              />
            </AreaChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function fmtMetricTime(ts: string, period: '1h' | '24h') {
  const date = parseUtcDate(ts)
  if (!date) return ''
  void period
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: CLIENT_TIME_ZONE,
  }).format(date)
}

function fmtDateTime(ts: string | null | undefined) {
  const date = parseUtcDate(ts)
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: CLIENT_TIME_ZONE,
  }).format(date)
}

function MetricsTooltip({
  active,
  payload,
  label,
  type,
  period,
}: {
  active?: boolean
  payload?: Array<{ dataKey?: string; value?: number | string; payload?: { collected_at: string } }>
  label?: string
  type: 'cpu' | 'memory'
  period: '1h' | '24h'
}) {
  if (!active || !payload?.length) return null

  const ts = payload[0]?.payload?.collected_at || (typeof label === 'string' ? label : '')

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">
        {ts ? new Intl.DateTimeFormat(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short',
          timeZone: CLIENT_TIME_ZONE,
        }).format(parseUtcDate(ts) ?? new Date(ts)) : ''}
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

function FlowNode({ title, value, meta, accent = false }: { title: string; value: string; meta: string; accent?: boolean }) {
  return (
    <div className={`routing-node${accent ? ' routing-node-accent' : ''}`}>
      <div className="routing-node-label">{title}</div>
      <div className="routing-node-value">{value}</div>
      <div className="routing-node-meta">{meta}</div>
    </div>
  )
}

function FlowArrow() {
  return <div className="routing-arrow">→</div>
}

function StatCard({
  title,
  value,
  label,
  tone,
}: {
  title: string
  value: string
  label: string
  tone?: 'online' | 'offline' | 'warning'
}) {
  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 10 }}>{title}</div>
      <div className={`stat-value ${tone === 'online' ? 'text-accent' : tone === 'offline' ? 'text-danger' : ''}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

function InfoChip({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ minWidth: 180 }}>
      <div className="text-muted text-sm">{label}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color: accent ? 'var(--accent)' : 'var(--text)' }}>{value}</div>
    </div>
  )
}

function ZoneCard({ title, description, value }: { title: string; description: string; value: string }) {
  return (
    <div className="card" style={{ background: 'var(--bg-3)', padding: 14 }}>
      <div className="card-title" style={{ marginBottom: 8 }}>{title}</div>
      <div className="stat-value" style={{ fontSize: 18 }}>{value}</div>
      <div className="stat-label">{description}</div>
    </div>
  )
}

function fmtBytes(bytes: number | null | undefined) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(1)} ${units[index]}`
}

function fmtPercent(value: number | null | undefined) {
  return `${(value ?? 0).toFixed(1)}%`
}

function fmtLatency(latencyMs: number | null | undefined) {
  if (latencyMs == null) return '—'
  return `${latencyMs.toFixed(0)} ms`
}

function fmtLatencyProbe(
  target: string | null | undefined,
  viaInterface: string | null | undefined,
  t: (key: any) => string,
) {
  if (!target) return t('latencyTargetUnknown')
  if (viaInterface) return `${t('latencyProbeLabel')} ${target} ${t('latencyProbeVia')} ${viaInterface}`
  return `${t('latencyProbeLabel')} ${target} ${t('latencyProbeVia')} ${t('defaultRoute')}`
}

function renderUdpStatus(status: string | null | undefined, t: (key: any) => string) {
  if (!status) return '—'
  if (status === 'available') return <span className="badge badge-online">{t('available')}</span>
  if (status === 'unavailable') return <span className="badge badge-offline">{t('unavailable')}</span>
  return <span className="badge badge-unknown">{status}</span>
}

function GridIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  )
}

function ServerIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="8" rx="2" />
      <rect x="2" y="14" width="20" height="8" rx="2" />
      <line x1="6" y1="6" x2="6.01" y2="6" />
      <line x1="6" y1="18" x2="6.01" y2="18" />
    </svg>
  )
}

function DeviceIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="2" width="14" height="20" rx="3" />
      <line x1="9" y1="6" x2="15" y2="6" />
      <line x1="12" y1="18" x2="12.01" y2="18" />
    </svg>
  )
}

function GlobeIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}

function RouteIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="19" r="3" />
      <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" />
      <circle cx="18" cy="5" r="3" />
    </svg>
  )
}

function DnsIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a10 10 0 1 0 10 10" />
      <path d="M12 6v6l4 2" />
      <path d="M18 14h4M20 12v4" />
    </svg>
  )
}

function ArchiveIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="21 8 21 21 3 21 3 8" />
      <rect x="1" y="3" width="22" height="5" />
      <line x1="10" y1="12" x2="14" y2="12" />
    </svg>
  )
}

function GearIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  )
}

function PulseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 7-4-14-3 7H2" />
    </svg>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/*" element={<RequireAuth><AppLayout /></RequireAuth>} />
    </Routes>
  )
}
