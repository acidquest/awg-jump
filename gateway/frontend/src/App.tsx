import { FormEvent, useEffect, useMemo, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
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
  udp_status: string | null
  udp_detail: string | null
  is_active: boolean
  tunnel_address: string
  dns_servers: string[]
  allowed_ips: string[]
  persistent_keepalive: number | null
  obfuscation: Record<string, string | number>
}

type SystemStatus = {
  runtime_available: boolean
  tunnel_status: string
  active_entry_node: { id: number; name: string; endpoint: string; latest_latency_ms: number | null } | null
  entry_node_count: number
  dns_rule_count: number
  traffic_source_mode: string
  runtime_mode: string
  kernel_available: boolean
  kernel_message: string | null
  ui_language: string
  kill_switch_enabled: boolean
  geoip_countries: string[]
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('gateway-token')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function useLoader<T>(url: string, fallback: T) {
  const [data, setData] = useState<T>(fallback)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const reload = async () => {
    setLoading(true)
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

function LoginPage() {
  const navigate = useNavigate()
  const { t } = useI18n()
  const [username, setUsername] = useState('admin')
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
          <form onSubmit={submit}>
            <div className="form-group">
              <label className="form-label">{t('username')}</label>
              <input
                className="form-input"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoFocus
                autoComplete="username"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">{t('password')}</label>
              <input
                className="form-input"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
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
    { to: '/geoip', label: t('geoip'), Icon: GlobeIcon },
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
          <Route path="/geoip" element={<GeoipPage />} />
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
  const { data, loading, error, reload } = useLoader<SystemStatus>('/system/status', {
    runtime_available: false,
    tunnel_status: 'unknown',
    active_entry_node: null,
    entry_node_count: 0,
    dns_rule_count: 0,
    traffic_source_mode: 'localhost',
    runtime_mode: 'auto',
    kernel_available: false,
    kernel_message: null,
    ui_language: 'en',
    kill_switch_enabled: true,
    geoip_countries: [],
  })

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('dashboard')}</div>
          <div className="page-subtitle">{t('dashboardSubtitle')}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => void reload()}>{t('refresh')}</button>
      </div>
      {error ? <div className="error-box">{error}</div> : null}
      {!error && !loading && !data.kernel_available ? <div className="info-box">{t('kernelUnavailable')}{data.kernel_message ? `: ${data.kernel_message}` : ''}</div> : null}
      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
      <div className="card-grid card-grid-4" style={{ marginBottom: 20 }}>
        <StatCard title={t('tunnel')} value={data.tunnel_status} label={data.runtime_available ? 'runtime ready' : 'runtime missing'} accent />
        <StatCard title={t('entryNodes')} value={String(data.entry_node_count)} label={t('activeNode')} />
        <StatCard title={t('dnsRules')} value={String(data.dns_rule_count)} label={t('domains')} />
        <StatCard title={t('geoipCountries')} value={data.geoip_countries.join(', ') || '—'} label={data.runtime_mode ?? t('geoipSummary')} />
      </div>
      <div className="card-grid card-grid-2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 10 }}>{t('activeNode')}</div>
          {data.active_entry_node ? (
            <>
              <div className="stat-value" style={{ fontSize: 20 }}>{data.active_entry_node.name}</div>
              <div className="stat-label">{data.active_entry_node.endpoint}</div>
              <div className="text-muted text-sm" style={{ marginTop: 10 }}>{t('latency')}: {fmtLatency(data.active_entry_node.latest_latency_ms)}</div>
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
          <div className="stat-value" style={{ fontSize: 20 }}>{data.kill_switch_enabled ? 'protected' : 'relaxed'}</div>
          <div className="stat-label">{t('trafficSource')}: {data.traffic_source_mode}</div>
          <div className="text-muted text-sm" style={{ marginTop: 10 }}>
            {t('kernelModeStatus')}: {data.kernel_available ? t('available') : t('unavailable')}
          </div>
        </div>
      </div>
    </>
  )
}

function GeoipPage() {
  const { t } = useI18n()
  const { data: routing, loading, error, reload } = useLoader<any>('/routing', {
    geoip_enabled: true,
    geoip_countries: ['ru'],
    manual_prefixes: [],
    geoip_ipset_name: 'gateway_geoip_local',
  })
  const { data: diagnostics, reload: reloadDiagnostics } = useLoader<any>('/backup/diagnostics', { routing_plan: {} })
  const [message, setMessage] = useState('')
  const [countryCode, setCountryCode] = useState('')
  const [manualPrefix, setManualPrefix] = useState('')

  async function updateGeoip() {
    await api.post('/routing/refresh-geoip')
    setMessage('GeoIP update requested')
    await reload()
    await reloadDiagnostics()
  }

  async function addCountry(event: FormEvent) {
    event.preventDefault()
    await api.post('/routing/countries', { country_code: countryCode })
    setCountryCode('')
    await reload()
  }

  async function removeCountry(country: string) {
    await api.delete(`/routing/countries/${country}`)
    await reload()
  }

  async function addManualPrefix(event: FormEvent) {
    event.preventDefault()
    await api.post('/routing/manual-prefixes', { prefix: manualPrefix })
    setManualPrefix('')
    await reload()
    await reloadDiagnostics()
  }

  async function removeManualPrefix(prefix: string) {
    await api.delete(`/routing/manual-prefixes/${encodeURIComponent(prefix)}`)
    await reload()
    await reloadDiagnostics()
  }

  const prefixCount = diagnostics?.routing_plan?.geoip_prefix_count ?? 0

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('localZones')}</div>
          <div className="page-subtitle">{t('localZonesSubtitle')}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={() => void reload()}>{t('refresh')}</button>
          <button className="btn btn-primary btn-sm" onClick={() => void updateGeoip()}>{t('updateGeoip')}</button>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ gap: 16, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 28, fontWeight: 700 }}>{routing.geoip_countries?.length ?? 0}</div>
            <div className="text-muted text-sm">{t('countries')}</div>
          </div>
          <div>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)' }}>{prefixCount.toLocaleString()}</div>
            <div className="text-muted text-sm">{t('totalPrefixes')}</div>
          </div>
          <div>
            <div className="text-muted text-sm">{t('status')}</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{routing.geoip_enabled ? 'enabled' : 'disabled'}</div>
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">{t('countries')}</div>
        <form onSubmit={addCountry} style={{ marginBottom: 14 }}>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">{t('addCountry')}</label>
              <input className="form-input" value={countryCode} onChange={(event) => setCountryCode(event.target.value)} placeholder="ru" />
            </div>
            <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button className="btn btn-primary" type="submit">{t('save')}</button>
            </div>
          </div>
        </form>
        <div className="table-wrap nodes-table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('countries')}</th>
                <th>IPSet</th>
                <th>{t('totalPrefixes')}</th>
                <th>{t('status')}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(routing.geoip_countries || []).length === 0 ? (
                <tr><td colSpan={5} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No GeoIP countries configured</td></tr>
              ) : (
                (routing.geoip_countries || []).map((country: string) => (
                  <tr key={country}>
                    <td><span className="badge badge-pending">{country.toUpperCase()}</span></td>
                    <td className="text-mono">{routing.geoip_ipset_name}</td>
                    <td>{prefixCount.toLocaleString()}</td>
                    <td><span className={`badge ${routing.geoip_enabled ? 'badge-online' : 'badge-offline'}`}>{routing.geoip_enabled ? 'enabled' : 'disabled'}</span></td>
                    <td><button className="btn btn-danger btn-sm" onClick={() => void removeCountry(country)}>{t('remove')}</button></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section">
        <div className="section-title">{t('manualPrefixes')}</div>
        <form onSubmit={addManualPrefix} style={{ marginBottom: 14 }}>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">{t('addPrefix')}</label>
              <input className="form-input mono" value={manualPrefix} onChange={(event) => setManualPrefix(event.target.value)} placeholder="203.0.113.10 or 203.0.113.0/24" />
            </div>
            <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button className="btn btn-primary" type="submit">{t('save')}</button>
            </div>
          </div>
        </form>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('manualPrefixes')}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(routing.manual_prefixes || []).length === 0 ? (
                <tr><td colSpan={2} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No manual prefixes configured</td></tr>
              ) : (
                (routing.manual_prefixes || []).map((prefix: string) => (
                  <tr key={prefix}>
                    <td className="text-mono">{prefix}</td>
                    <td><button className="btn btn-danger btn-sm" onClick={() => void removeManualPrefix(prefix)}>{t('remove')}</button></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

function NodesPage() {
  const { t } = useI18n()
  const { data, loading, error, reload } = useLoader<NodeItem[]>('/nodes', [])
  const [message, setMessage] = useState('')
  const [editNode, setEditNode] = useState<NodeItem | null>(null)
  const [showAddNode, setShowAddNode] = useState(false)

  async function activate(nodeId: number) {
    await api.post(`/nodes/${nodeId}/activate`)
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

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('nodes')}</div>
          <div className="page-subtitle">{t('nodesSubtitle')}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddNode(true)}>{t('add')}</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void startTunnel()}>{t('startTunnel')}</button>
          <button className="btn btn-secondary btn-sm" onClick={() => void stopTunnel()}>{t('stopTunnel')}</button>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-title" style={{ marginBottom: 14 }}>{t('savedNodes')}</div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('activeNode')}</th>
                <th>Name</th>
                <th>{t('endpoint')}</th>
                <th>{t('latency')}</th>
                <th>{t('udpStatus')}</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.length === 0 ? (
                <tr><td colSpan={6} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No entry nodes imported yet</td></tr>
              ) : data.map((node) => (
                <tr key={node.id} className={node.is_active ? 'active-node' : ''}>
                  <td>{node.is_active ? <span className="badge badge-online">{t('active')}</span> : '—'}</td>
                  <td>{node.name}</td>
                  <td className="text-mono">{node.endpoint}</td>
                  <td className="text-mono">{node.is_active ? fmtLatency(node.latest_latency_ms) : t('unavailable')}</td>
                  <td>{node.is_active ? '—' : renderUdpStatus(node.udp_status, t)}</td>
                    <td>
                      <div className="nodes-actions">
                        <button className="btn btn-primary btn-sm" onClick={() => void activate(node.id)}>{t('activate')}</button>
                        <button className="btn btn-ghost btn-sm" onClick={() => setEditNode(node)}>Edit</button>
                      </div>
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
      {editNode ? (
        <NodeEditorModal
          node={editNode}
          onClose={() => setEditNode(null)}
          onSaved={async () => {
            setEditNode(null)
            setMessage('Entry node updated')
            await reload()
          }}
        />
      ) : null}
    </>
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
          <div className="modal-title">Edit entry node: {node.name}</div>
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
            <div className="info-box">Visual editor preserves the saved obfuscation parameters. Edit raw .conf if you need to change them.</div>
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
  const { data, loading, error, reload, setData } = useLoader<any>('/routing', {
    geoip_enabled: true,
    geoip_countries: ['ru'],
    invert_geoip: false,
    default_policy: 'vpn',
    kill_switch_enabled: true,
    strict_mode: true,
  })
  const { data: plan, reload: reloadPlan } = useLoader<any>('/routing/plan', { commands: [], warnings: [], safe_to_apply: false })
  const [countries, setCountries] = useState('ru')
  const [message, setMessage] = useState('')

  useEffect(() => setCountries((data.geoip_countries || ['ru']).join(',')), [data.geoip_countries])

  async function persistPolicy(nextData: any) {
    setData(nextData)
    await api.put('/routing', {
      ...nextData,
      geoip_countries: countries.split(',').map((item) => item.trim()).filter(Boolean),
    })
    const applyResponse = await api.post('/routing/apply')
    setMessage(applyResponse.data.status === 'applied' ? 'Routing applied' : applyResponse.data.error || 'Routing blocked')
    await reload()
    await reloadPlan()
  }

  async function togglePolicy(key: 'geoip_enabled' | 'kill_switch_enabled' | 'strict_mode', value: boolean) {
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
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={() => void api.post('/routing/refresh-geoip')}>{t('refreshGeoip')}</button>
          <button className="btn btn-primary btn-sm" onClick={() => void reloadPlan()}>{t('buildPlan')}</button>
        </div>
      </div>
      {message ? <div className="info-box">{message}</div> : null}
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ marginBottom: 10 }}>
          <div>
            <div className="card-title">Traffic Direction</div>
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>
              Normal mode keeps configured GeoIP countries on the host interface. Inverted mode swaps the directions.
            </div>
          </div>
        </div>
        <div className="form-row form-row-2">
          <div className="form-group">
            <label className="form-label">{t('geoipCountries')}</label>
            <input className="form-input" value={countries} onChange={(event) => setCountries(event.target.value)} readOnly />
          </div>
          <div className="form-group">
            <label className="form-label">Default policy</label>
            <input className="form-input" value={data.default_policy} readOnly />
          </div>
        </div>
        <div className="flex gap-4" style={{ marginBottom: 14 }}>
          <label className="toggle" title={t('geoipEnabled')}>
            <input type="checkbox" checked={data.geoip_enabled} onChange={(event) => void togglePolicy('geoip_enabled', event.target.checked)} />
            <span className="toggle-slider" />
          </label>
          <span className="text-sm">{t('geoipEnabled')}</span>
          <label className="toggle" title={t('killSwitch')}>
            <input type="checkbox" checked={data.kill_switch_enabled} onChange={(event) => void togglePolicy('kill_switch_enabled', event.target.checked)} />
            <span className="toggle-slider" />
          </label>
          <span className="text-sm">{t('killSwitch')}</span>
          <label className="toggle" title={t('strictMode')}>
            <input type="checkbox" checked={data.strict_mode} onChange={(event) => void togglePolicy('strict_mode', event.target.checked)} />
            <span className="toggle-slider" />
          </label>
          <span className="text-sm">{t('strictMode')}</span>
        </div>
      </div>
      <div className="section">
        <div className="section-title">Policy routing diagram</div>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="routing-diagram">
          <div className="routing-diagram-header">
            <div>
              <div className="routing-diagram-title">Generated traffic map</div>
              <div className="routing-diagram-subtitle">
                {plan.safe_to_apply ? 'The current plan is safe to apply.' : 'The current plan blocks unsafe fallback and keeps traffic from leaking outside the tunnel.'}
              </div>
            </div>
            <div className="routing-diagram-mode">
              <span className={`badge ${plan.safe_to_apply ? 'badge-online' : 'badge-warning'}`}>
                {plan.safe_to_apply ? 'safe' : 'blocked'}
              </span>
            </div>
          </div>
          <div className="routing-diagram-grid">
            <div className="routing-node">
              <div className="routing-node-label">Source mode</div>
              <div className="routing-node-value">{plan.source_mode || data.traffic_source_mode || 'localhost'}</div>
              <div className="routing-node-meta">{(plan.selectors || []).join(', ') || 'OUTPUT only'}</div>
            </div>
            <div className="routing-arrow">→</div>
            <div className="routing-node">
              <div className="routing-node-label">GeoIP / ipset</div>
              <div className="routing-node-value">{data.geoip_ipset_name || 'gateway_geoip_local'}</div>
              <div className="routing-node-meta">{plan.geoip_prefix_count ?? 0} prefixes</div>
            </div>
            <div className="routing-arrow">→</div>
            <div className="routing-node">
              <div className="routing-node-label">Tunnel policy</div>
              <div className="routing-node-value">{data.kill_switch_enabled ? 'kill switch on' : 'kill switch off'}</div>
              <div className="routing-node-meta">{data.strict_mode ? 'strict' : 'relaxed'}</div>
            </div>
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
  const { data, loading, error, reload } = useLoader<any>('/dns', { upstreams: [], domains: [], preview: '' })
  const [domain, setDomain] = useState('')

  async function addDomain(event: FormEvent) {
    event.preventDefault()
    await api.post('/dns/domains', { domain, zone: 'local', enabled: true })
    setDomain('')
    await reload()
  }

  const localUpstream = data.upstreams.find((item: any) => item.zone === 'local')
  const vpnUpstream = data.upstreams.find((item: any) => item.zone === 'vpn')

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{t('dns')}</div>
          <div className="page-subtitle">{t('dnsSubtitle')}</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={() => void reload()}>{t('refresh')}</button>
          <button className="btn btn-primary btn-sm" onClick={() => void api.post('/dns/domains', { domain: 'example.com', zone: 'local', enabled: true })}>{t('addDomain')}</button>
        </div>
      </div>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{data.preview ? t('dnsRunning') : t('dnsStopped')}</div>
          </div>
          <InfoChip label={t('localZoneDns')} value={localUpstream?.servers?.join(', ') ?? '—'} accent />
          <InfoChip label={t('upstreamZoneDns')} value={vpnUpstream?.servers?.join(', ') ?? '—'} />
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>{data.domains.length}</div>
            <div className="text-muted text-sm">{t('localZoneDomains')}</div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-title" style={{ marginBottom: 14 }}>{t('dnsZones')}</div>
        <div className="card-grid card-grid-2">
          <ZoneCard title={t('localZoneDns')} description={t('localDnsDescription')} value={localUpstream?.servers?.join(', ') ?? '—'} />
          <ZoneCard title={t('upstreamZoneDns')} description={t('vpnDnsDescription')} value={vpnUpstream?.servers?.join(', ') ?? '—'} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <div className="card-title">{t('domains')}</div>
        </div>
        <form onSubmit={addDomain} style={{ marginBottom: 14 }}>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">Domain</label>
              <input className="form-input" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="example.com" />
            </div>
            <div className="form-group" style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button className="btn btn-primary" type="submit">{t('save')}</button>
            </div>
          </div>
        </form>
        {loading ? <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t('domains')}</th>
                <th>Zone</th>
                <th>{t('status')}</th>
              </tr>
            </thead>
            <tbody>
              {data.domains.length === 0 ? (
                <tr><td colSpan={3} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No domains configured</td></tr>
              ) : data.domains.map((item: any) => (
                <tr key={item.id}>
                  <td>{item.domain}</td>
                  <td><span className={`badge ${item.zone === 'local' ? 'badge-online' : 'badge-pending'}`}>{item.zone}</span></td>
                  <td><span className={`badge ${item.enabled ? 'badge-online' : 'badge-offline'}`}>{item.enabled ? 'enabled' : 'disabled'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section">
        <div className="section-title">{t('preview')}</div>
        <div className="terminal" style={{ minHeight: 220 }}>
          <div className="terminal-line">
            <span className="msg" style={{ whiteSpace: 'pre-wrap' }}>{data.preview}</span>
          </div>
        </div>
      </div>
    </>
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

function SettingsPage() {
  const { locale, setLocale, t } = useI18n()
  const { data, reload } = useLoader<any>('/settings', {
    ui_language: 'en',
    runtime_mode: 'auto',
    traffic_source_mode: 'localhost',
    allowed_client_cidrs: [],
    allowed_client_hosts: [],
    kernel_available: false,
    kernel_message: null,
  })
  const [cidrs, setCidrs] = useState('')
  const [hosts, setHosts] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    setCidrs((data.allowed_client_cidrs || []).join(', '))
    setHosts((data.allowed_client_hosts || []).join(', '))
  }, [data.allowed_client_cidrs, data.allowed_client_hosts])

  async function saveSettings(event: FormEvent) {
    event.preventDefault()
    await api.put('/settings', {
      ui_language: locale,
      runtime_mode: data.runtime_mode,
      traffic_source_mode: data.traffic_source_mode,
      allowed_client_cidrs: cidrs.split(',').map((item: string) => item.trim()).filter(Boolean),
      allowed_client_hosts: hosts.split(',').map((item: string) => item.trim()).filter(Boolean),
    })
    setMessage('Settings saved')
    await reload()
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault()
    await api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword })
    setCurrentPassword('')
    setNewPassword('')
    setMessage('Password changed')
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
              <select className="form-input" value={data.runtime_mode} onChange={(event) => { data.runtime_mode = event.target.value }}>
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
              <select className="form-input" value={data.traffic_source_mode} onChange={(event) => { data.traffic_source_mode = event.target.value }}>
                <option value="localhost">{t('localhost')}</option>
                <option value="selected_cidr">{t('selectedCidr')}</option>
                <option value="selected_hosts">{t('selectedHosts')}</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">{t('cidrList')}</label>
              <input className="form-input" value={cidrs} onChange={(event) => setCidrs(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">{t('hostList')}</label>
              <input className="form-input" value={hosts} onChange={(event) => setHosts(event.target.value)} />
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

function StatCard({ title, value, label, accent = false }: { title: string; value: string; label: string; accent?: boolean }) {
  return (
    <div className="card">
      <div className="card-title" style={{ marginBottom: 10 }}>{title}</div>
      <div className={`stat-value ${accent ? 'text-accent' : ''}`}>{value}</div>
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

function fmtLatency(latencyMs: number | null | undefined) {
  if (latencyMs == null) return '—'
  return `${latencyMs.toFixed(0)} ms`
}

function renderUdpStatus(status: string | null | undefined, t: (key: any) => string) {
  if (!status) return t('unavailable')
  if (status === 'open') return t('udpOpen')
  if (status === 'open_or_filtered') return t('udpOpenOrFiltered')
  if (status === 'unreachable') return t('udpUnreachable')
  return status
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
