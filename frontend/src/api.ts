import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach token from localStorage
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Redirect to login on 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api

// ── Auth ─────────────────────────────────────────────────────────────────
export const login = (username: string, password: string) =>
  api.post<{ access_token: string }>('/auth/login', { username, password })

export const logout = () => api.post('/auth/logout')

// ── System ────────────────────────────────────────────────────────────────
export const getSystemStatus = () => api.get('/system/status')
export const getSystemMetrics = (period: '1h' | '24h' = '1h') =>
  api.get('/system/metrics', { params: { period } })
export const restartRouting = () => api.post('/system/restart-routing')
export const getLogs = (service = 'uvicorn', lines = 200) =>
  api.get('/system/logs', { params: { service, lines } })

// ── Interfaces ────────────────────────────────────────────────────────────
export const getInterfaces = () => api.get('/interfaces')
export const updateInterface = (id: number, data: Record<string, unknown>) =>
  api.put(`/interfaces/${id}`, data)
export const applyInterface = (id: number) => api.post(`/interfaces/${id}/apply`)
export const stopInterface = (id: number) => api.post(`/interfaces/${id}/stop`)
export const regenObfuscation = (id: number) =>
  api.post(`/interfaces/${id}/regenerate-obfuscation`)

// ── Peers ─────────────────────────────────────────────────────────────────
export const getPeers = (interfaceId?: number) =>
  api.get('/peers', { params: interfaceId ? { interface_id: interfaceId } : {} })
export const createPeer = (data: Record<string, unknown>) => api.post('/peers', data)
export const updatePeer = (id: number, data: Record<string, unknown>) =>
  api.put(`/peers/${id}`, data)
export const deletePeer = (id: number) => api.delete(`/peers/${id}`)
export const togglePeer = (id: number) => api.post(`/peers/${id}/toggle`)
export const getPeerConfig = (id: number, endpoint?: string) =>
  api.get(`/peers/${id}/config`, {
    params: endpoint ? { server_endpoint: endpoint } : {},
    responseType: 'text',
  })
export const getPeerQr = (id: number, endpoint?: string) =>
  api.get(`/peers/${id}/qr`, {
    params: endpoint ? { server_endpoint: endpoint } : {},
    responseType: 'blob',
  })

// ── Nodes ─────────────────────────────────────────────────────────────────
export const getNodes = () => api.get('/nodes')
export const createNode = (data: Record<string, unknown>) => api.post('/nodes', data)
export const updateNode = (id: number, data: Record<string, unknown>) =>
  api.put(`/nodes/${id}`, data)
export const deleteNode = (id: number, creds?: Record<string, unknown>) =>
  api.delete(`/nodes/${id}`, { data: creds })
export const deployNode = (data: Record<string, unknown>) => api.post('/nodes/deploy', data)
export const redeployNode = (id: number, data: Record<string, unknown>) =>
  api.post(`/nodes/${id}/redeploy`, data)
export const activateNode = (id: number) => api.post(`/nodes/${id}/activate`)
export const resetNode = (id: number) => api.post(`/nodes/${id}/reset`)
export const checkNode = (id: number) => api.post(`/nodes/${id}/check`)
export const getNodeStats = (id: number) => api.get(`/nodes/${id}/stats`)
export const getNodePeers = (id: number) => api.get(`/nodes/${id}/peers`)
export const createNodePeer = (id: number, data: Record<string, unknown>) => api.post(`/nodes/${id}/peers`, data)
export const updateNodePeer = (id: number, peerId: number, data: Record<string, unknown>) =>
  api.put(`/nodes/${id}/peers/${peerId}`, data)
export const deleteNodePeer = (id: number, peerId: number) => api.delete(`/nodes/${id}/peers/${peerId}`)
export const getNodePeerConfig = (id: number, peerId: number) =>
  api.get(`/nodes/${id}/peers/${peerId}/config`, { responseType: 'text' })

// ── GeoIP ─────────────────────────────────────────────────────────────────
export const getGeoipStatus = () => api.get('/geoip/status')
export const getGeoipSources = () => api.get('/geoip/sources')
export const createGeoipSource = (data: Record<string, unknown>) => api.post('/geoip/sources', data)
export const updateGeoipSource = (id: number, data: Record<string, unknown>) =>
  api.put(`/geoip/sources/${id}`, data)
export const deleteGeoipSource = (id: number) => api.delete(`/geoip/sources/${id}`)
export const triggerGeoipUpdate = () => api.post('/geoip/update')

// ── Routing ───────────────────────────────────────────────────────────────
export const getRoutingStatus = () => api.get('/routing/status')
export const applyRouting = () => api.post('/routing/apply')
export const resetRouting = () => api.post('/routing/reset')
export const updateRoutingSettings = (data: { invert_geoip: boolean }) =>
  api.put('/routing/settings', data)

// ── DNS ───────────────────────────────────────────────────────────────────
export const getDnsStatus = () => api.get('/dns/status')
export const getDnsDomains = () => api.get('/dns/domains')
export const getDnsManualAddresses = () => api.get('/dns/manual-addresses')
export const getDnsZones = () => api.get('/dns/zones')
export const createDnsZone = (data: Record<string, unknown>) => api.post('/dns/zones', data)
export const deleteDnsZone = (zone: string) => api.delete(`/dns/zones/${zone}`)
export const createDnsDomain = (data: Record<string, unknown>) => api.post('/dns/domains', data)
export const createDnsManualAddress = (data: Record<string, unknown>) => api.post('/dns/manual-addresses', data)
export const createDnsDomainsBulk = (data: Record<string, unknown>) => api.post('/dns/domains/bulk', data)
export const updateDnsDomain = (id: number, data: Record<string, unknown>) =>
  api.put(`/dns/domains/${id}`, data)
export const updateDnsManualAddress = (id: number, data: Record<string, unknown>) =>
  api.put(`/dns/manual-addresses/${id}`, data)
export const updateDnsZone = (zone: string, data: Record<string, unknown>) =>
  api.put(`/dns/zones/${zone}`, data)
export const deleteDnsDomain = (id: number) => api.delete(`/dns/domains/${id}`)
export const deleteDnsManualAddress = (id: number) => api.delete(`/dns/manual-addresses/${id}`)
export const toggleDnsDomain = (id: number) => api.post(`/dns/domains/${id}/toggle`)
export const toggleDnsManualAddress = (id: number) => api.post(`/dns/manual-addresses/${id}/toggle`)
export const reloadDns = () => api.post('/dns/reload')

// ── Backup ────────────────────────────────────────────────────────────────
export const downloadBackup = () =>
  api.get('/backup/export', { responseType: 'blob' })
export const uploadBackup = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/backup/import', form)
}

// ── Settings ─────────────────────────────────────────────────────────────
export const getSettings = () => api.get('/settings')
export const updateSettings = (data: Record<string, unknown>) => api.put('/settings', data)
export const updateAdminPassword = (data: { current_password: string; new_password: string }) =>
  api.post('/settings/password', data)
export const uploadTlsMaterial = (certFile: File, keyFile: File) => {
  const form = new FormData()
  form.append('cert_file', certFile)
  form.append('key_file', keyFile)
  return api.post('/settings/tls', form)
}
