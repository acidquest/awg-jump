import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getSettings, updateAdminPassword, updateSettings, uploadTlsMaterial } from '../api'
import { SystemSettings } from '../types'

export default function SettingsPage() {
  const qc = useQueryClient()
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '' })
  const [webForm, setWebForm] = useState({ web_mode: 'https', web_port: '8080' })
  const [certFile, setCertFile] = useState<File | null>(null)
  const [keyFile, setKeyFile] = useState<File | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data } = useQuery<SystemSettings>({
    queryKey: ['settings'],
    queryFn: () => getSettings().then((r) => r.data),
  })

  useEffect(() => {
    if (data) {
      setWebForm({ web_mode: data.web_mode, web_port: String(data.web_port) })
    }
  }, [data])

  const saveWebMut = useMutation({
    mutationFn: () => updateSettings({ web_mode: webForm.web_mode, web_port: Number(webForm.web_port) }),
    onSuccess: (resp) => {
      setMessage(resp.data.restart_required ? 'Saved. Container restart is required.' : 'Saved.')
      setError('')
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: unknown) => setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to save settings'),
  })

  const passwordMut = useMutation({
    mutationFn: () => updateAdminPassword(passwordForm),
    onSuccess: () => {
      setMessage('Admin password updated.')
      setError('')
      setPasswordForm({ current_password: '', new_password: '' })
    },
    onError: (e: unknown) => setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Failed to update password'),
  })

  const tlsMut = useMutation({
    mutationFn: () => {
      if (!certFile || !keyFile) throw new Error('Select certificate and private key files')
      return uploadTlsMaterial(certFile, keyFile)
    },
    onSuccess: () => {
      setMessage('TLS material uploaded. Container restart is required.')
      setError('')
      setCertFile(null)
      setKeyFile(null)
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: unknown) => setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? (e as Error).message ?? 'Failed to upload TLS files'),
  })

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Settings</div>
          <div className="page-subtitle">Web access, TLS, and admin credentials</div>
        </div>
      </div>

      {message && <div className="info-box" style={{ marginBottom: 16 }}>{message}</div>}
      {error && <div className="error-box" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="card-grid card-grid-2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>Web transport</div>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">Mode</label>
              <select
                className="form-input"
                value={webForm.web_mode}
                onChange={(e) => setWebForm((prev) => ({ ...prev, web_mode: e.target.value }))}
              >
                <option value="https">HTTPS</option>
                <option value="http">HTTP</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">WEB_PORT</label>
              <input
                className="form-input mono"
                value={webForm.web_port}
                onChange={(e) => setWebForm((prev) => ({ ...prev, web_port: e.target.value }))}
              />
            </div>
          </div>
          <div className="info-box" style={{ fontSize: 12, marginBottom: 12 }}>
            Changing web mode or port updates `/app/.env`. Restart the container to apply it.
          </div>
          <button className="btn btn-primary" onClick={() => saveWebMut.mutate()} disabled={saveWebMut.isPending}>
            {saveWebMut.isPending ? <span className="spinner" /> : 'Save web settings'}
          </button>
        </div>

        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>Admin password</div>
          <div className="form-group">
            <label className="form-label">Current password</label>
            <input
              className="form-input"
              type="password"
              value={passwordForm.current_password}
              onChange={(e) => setPasswordForm((prev) => ({ ...prev, current_password: e.target.value }))}
            />
          </div>
          <div className="form-group">
            <label className="form-label">New password</label>
            <input
              className="form-input"
              type="password"
              value={passwordForm.new_password}
              onChange={(e) => setPasswordForm((prev) => ({ ...prev, new_password: e.target.value }))}
            />
          </div>
          <button className="btn btn-primary" onClick={() => passwordMut.mutate()} disabled={passwordMut.isPending}>
            {passwordMut.isPending ? <span className="spinner" /> : 'Change password'}
          </button>
        </div>
      </div>

      <div className="section">
        <div className="section-title">TLS certificate</div>
        <div className="card">
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">Certificate (.crt/.pem)</label>
              <input className="form-input" type="file" accept=".crt,.pem" onChange={(e) => setCertFile(e.target.files?.[0] ?? null)} />
            </div>
            <div className="form-group">
              <label className="form-label">Private key (.key/.pem)</label>
              <input className="form-input" type="file" accept=".key,.pem" onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)} />
            </div>
          </div>
          {data?.cert && (
            <div className="info-box" style={{ fontSize: 12, marginBottom: 12 }}>
              Current cert: `{data.cert.path}` SHA-256 `{data.cert.sha256.slice(0, 16)}...`
            </div>
          )}
          <button className="btn btn-primary" onClick={() => tlsMut.mutate()} disabled={tlsMut.isPending || !certFile || !keyFile}>
            {tlsMut.isPending ? <span className="spinner" /> : 'Upload TLS files'}
          </button>
        </div>
      </div>
    </>
  )
}
