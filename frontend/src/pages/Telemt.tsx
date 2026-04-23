import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createTelemtUser,
  deleteTelemtUser,
  getTelemt,
  telemtServiceAction,
  updateTelemtSettings,
  updateTelemtUser,
} from '../api'
import { TelemtPageData, TelemtServiceStatus, TelemtUser } from '../types'
import Modal from '../components/Modal'

function getApiError(e: unknown, fallback = 'Request failed') {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback
}

function versionBadge(data: TelemtPageData['version']) {
  if (data.version_status === 'latest') return { text: 'latest', bg: 'rgba(16, 185, 129, 0.14)', color: '#10b981' }
  if (data.version_status === 'outdated') return { text: 'update available', bg: 'rgba(245, 158, 11, 0.14)', color: '#f59e0b' }
  if (data.version_status === 'ahead') return { text: 'ahead of release', bg: 'rgba(56, 189, 248, 0.14)', color: '#38bdf8' }
  return { text: 'version check unavailable', bg: 'rgba(148, 163, 184, 0.14)', color: '#94a3b8' }
}

export default function TelemtPage() {
  const qc = useQueryClient()
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [editingUser, setEditingUser] = useState<TelemtUser | null>(null)
  const [configText, setConfigText] = useState('')

  const { data, isLoading } = useQuery<TelemtPageData>({
    queryKey: ['telemt'],
    queryFn: () => getTelemt().then((r) => r.data),
  })

  useEffect(() => {
    if (data) {
      setConfigText(data.settings.config_text)
    }
  }, [data])

  const refresh = () => qc.invalidateQueries({ queryKey: ['telemt'] })

  const settingsMut = useMutation({
    mutationFn: () => updateTelemtSettings({ config_text: configText }),
    onSuccess: (resp) => {
      setError('')
      setMessage(resp.data.restart_required
        ? 'Config saved. Container restart is required because server.port changed and Docker publish must be updated.'
        : 'Config saved. Use the service controls to apply the new config.')
      refresh()
      qc.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (e: unknown) => setError(getApiError(e, 'Failed to save TeleMT config')),
  })

  const serviceMut = useMutation({
    mutationFn: (action: 'start' | 'stop' | 'restart') => telemtServiceAction(action),
    onSuccess: (resp) => {
      const service = resp.data as TelemtServiceStatus
      setError('')
      setMessage(service.command_output || `TeleMT ${service.action || 'service'} executed.`)
      refresh()
      qc.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (e: unknown) => setError(getApiError(e, 'TeleMT service command failed')),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteTelemtUser(id),
    onSuccess: () => {
      setError('')
      setMessage('User deleted. Restart TeleMT if you need immediate runtime refresh.')
      refresh()
    },
    onError: (e: unknown) => setError(getApiError(e, 'Failed to delete user')),
  })

  if (isLoading) {
    return <div className="card"><span className="spinner" /></div>
  }

  if (!data?.feature_enabled) {
    return (
      <div className="card">
        <div className="card-title" style={{ marginBottom: 12 }}>TeleMT is disabled</div>
        <div className="text-muted">Set <span className="text-mono">TELEMT_ENABLED=on</span> in `.env`, then restart the container.</div>
      </div>
    )
  }

  const badge = versionBadge(data.version)

  return (
    <>
      <div className="page-header" style={{ alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <div className="page-title">TeleMT</div>
            <div className="text-mono" style={{ fontSize: 13, color: 'var(--muted)' }}>
              version {data.version.installed}
            </div>
            <a
              href={data.version.version_status === 'outdated' ? data.version.latest_release_url : data.version.repo_url}
              target="_blank"
              rel="noreferrer"
              className="text-mono"
              style={{
                fontSize: 12,
                padding: '6px 10px',
                borderRadius: 999,
                background: badge.bg,
                color: badge.color,
                textDecoration: 'none',
              }}
            >
              {data.version.version_status === 'outdated' && data.version.latest_version
                ? `update available: ${data.version.latest_version}`
                : badge.text}
            </a>
          </div>
          <div className="page-subtitle">
            MTProxy on Rust + Tokio · <a href={data.version.repo_url} target="_blank" rel="noreferrer">https://github.com/telemt/telemt</a>
          </div>
        </div>
      </div>

      {message && <div className="info-box" style={{ marginBottom: 16 }}>{message}</div>}
      {error && <div className="error-box" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-title" style={{ marginBottom: 12 }}>Service</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div className="form-group" style={{ marginBottom: 0, minWidth: 180 }}>
            <label className="form-label">Status</label>
            <div className="form-input mono">{data.service.status}</div>
          </div>
          <div className="form-group" style={{ marginBottom: 0, minWidth: 140 }}>
            <label className="form-label">Port</label>
            <div className="form-input mono">{data.settings.port}</div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 20 }}>
            <button className="btn btn-primary btn-sm" onClick={() => serviceMut.mutate('start')} disabled={serviceMut.isPending}>Start</button>
            <button className="btn btn-secondary btn-sm" onClick={() => serviceMut.mutate('restart')} disabled={serviceMut.isPending}>Restart</button>
            <button className="btn btn-danger btn-sm" onClick={() => serviceMut.mutate('stop')} disabled={serviceMut.isPending}>Stop</button>
          </div>
        </div>
        <div className="info-box" style={{ marginTop: 12, fontSize: 12 }}>
          {data.service.message || 'Use the service controls to start, restart, or stop TeleMT.'}
        </div>
        <div className="info-box" style={{ marginTop: 12, fontSize: 12 }}>
          Auto-start after container restart: <span className="text-mono">{data.settings.service_autostart ? 'enabled' : 'disabled'}</span>
        </div>
        {data.settings.restart_required && (
          <div className="error-box" style={{ marginTop: 12 }}>
            Config changed `server.port`. Restart the container to update Docker port publish.
          </div>
        )}
      </div>

      <div className="section">
        <div className="page-header" style={{ marginBottom: 12 }}>
          <div>
            <div className="section-title">Users</div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Add user</button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Secret</th>
                <th>Status</th>
                <th>Address</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.users.length === 0 ? (
                <tr><td colSpan={5} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No users</td></tr>
              ) : data.users.map((user) => (
                <tr key={user.id}>
                  <td className="text-mono">{user.username}</td>
                  <td className="text-mono">{user.secret_hex}</td>
                  <td>{user.enabled ? 'enabled' : 'disabled'}</td>
                  <td className="text-mono" style={{ maxWidth: 420, wordBreak: 'break-all' }}>{user.address ?? 'available after service start'}</td>
                  <td>
                    <div className="flex gap-2">
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingUser(user)}>Edit</button>
                      <button className="btn btn-danger btn-sm" onClick={() => { if (confirm('Delete TeleMT user?')) deleteMut.mutate(user.id) }}>Del</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section">
        <div className="section-title">Settings</div>
        <div className="card">
          <div className="form-group">
            <textarea
              className="form-input mono"
              rows={20}
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              style={{ minHeight: 420, resize: 'vertical' }}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" onClick={() => settingsMut.mutate()} disabled={settingsMut.isPending}>
              {settingsMut.isPending ? <span className="spinner" /> : 'Сохранить'}
            </button>
            <a href={data.settings.docs_url} target="_blank" rel="noreferrer" className="text-mono" style={{ fontSize: 12 }}>
              https://github.com/telemt/telemt/tree/main/docs/Config_params
            </a>
          </div>
        </div>
      </div>

      {showCreate && (
        <TelemtUserModal
          title="Add TeleMT user"
          onClose={() => setShowCreate(false)}
          onSubmit={(payload) => createTelemtUser(payload)}
          onSaved={() => {
            setShowCreate(false)
            setMessage('User added. Restart TeleMT if you need immediate runtime refresh.')
            refresh()
          }}
        />
      )}

      {editingUser && (
        <TelemtUserModal
          title="Edit TeleMT user"
          initialUser={editingUser}
          onClose={() => setEditingUser(null)}
          onSubmit={(payload) => updateTelemtUser(editingUser.id, payload)}
          onSaved={() => {
            setEditingUser(null)
            setMessage('User updated. Restart TeleMT if you need immediate runtime refresh.')
            refresh()
          }}
        />
      )}
    </>
  )
}

function TelemtUserModal({
  title,
  initialUser,
  onClose,
  onSubmit,
  onSaved,
}: {
  title: string
  initialUser?: TelemtUser
  onClose: () => void
  onSubmit: (payload: Record<string, unknown>) => Promise<unknown>
  onSaved: () => void
}) {
  const [form, setForm] = useState({
    username: initialUser?.username ?? '',
    secret_hex: initialUser?.secret_hex ?? '',
    enabled: initialUser?.enabled ?? true,
  })
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => onSubmit({
      username: form.username,
      secret_hex: form.secret_hex || undefined,
      enabled: form.enabled,
    }),
    onSuccess: () => {
      setError('')
      onSaved()
    },
    onError: (e: unknown) => setError(getApiError(e, 'Failed to save user')),
  })

  return (
    <Modal open title={title} onClose={onClose}>
      {error && <div className="error-box">{error}</div>}
      <div className="form-group">
        <label className="form-label">Username</label>
        <input className="form-input mono" value={form.username} onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))} />
      </div>
      <div className="form-group">
        <label className="form-label">Secret (32 hex chars, optional for new user)</label>
        <input className="form-input mono" value={form.secret_hex} onChange={(e) => setForm((prev) => ({ ...prev, secret_hex: e.target.value }))} placeholder="auto-generate if empty" />
      </div>
      <label className="toggle" style={{ gap: 10 }}>
        <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((prev) => ({ ...prev, enabled: e.target.checked }))} />
        <span className="toggle-slider" />
        <span>Enabled</span>
      </label>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => mut.mutate()} disabled={mut.isPending}>
          {mut.isPending ? <span className="spinner" /> : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
