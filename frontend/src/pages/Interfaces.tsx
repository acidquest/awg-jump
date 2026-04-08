import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getInterfaces, updateInterface, applyInterface, stopInterface, regenObfuscation } from '../api'
import { Interface } from '../types'
import StatusBadge from '../components/StatusBadge'
import Modal from '../components/Modal'

export default function Interfaces() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Interface | null>(null)
  const [busy, setBusy] = useState<Record<number, string>>({})
  const [regenTarget, setRegenTarget] = useState<Interface | null>(null)

  const { data: ifaces = [], isLoading } = useQuery<Interface[]>({
    queryKey: ['interfaces'],
    queryFn: () => getInterfaces().then((r) => r.data),
  })

  const withBusy = async (id: number, label: string, fn: () => Promise<unknown>) => {
    setBusy((b) => ({ ...b, [id]: label }))
    try { await fn() } finally {
      setBusy((b) => { const n = { ...b }; delete n[id]; return n })
      qc.invalidateQueries({ queryKey: ['interfaces'] })
    }
  }

  if (isLoading) return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Interfaces</div>
          <div className="page-subtitle">AWG tunnel interfaces</div>
        </div>
      </div>

      {ifaces.map((iface) => (
        <div key={iface.id} className="card" style={{ marginBottom: 16 }}>
          <div className="card-header">
            <div className="flex items-center gap-3">
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600 }}>
                {iface.name}
              </span>
              <StatusBadge status={iface.running ? 'up' : 'down'} />
              <span className="badge badge-unknown">{iface.mode}</span>
            </div>
            <div className="flex gap-2">
              {busy[iface.id] ? (
                <span className="text-muted text-sm">{busy[iface.id]}… <span className="spinner" /></span>
              ) : (
                <>
                  <button className="btn btn-secondary btn-sm" onClick={() => setEditing(iface)}>Edit</button>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => withBusy(iface.id, 'Applying', () => applyInterface(iface.id))}
                  >Apply</button>
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => withBusy(iface.id, 'Stopping', () => stopInterface(iface.id))}
                    disabled={!iface.running}
                  >Stop</button>
                </>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, fontSize: 13 }}>
            <div>
              <div className="text-muted text-sm">Address</div>
              <div className="text-mono">{iface.address}</div>
            </div>
            {iface.listen_port && (
              <div>
                <div className="text-muted text-sm">Listen port</div>
                <div className="text-mono">{iface.listen_port}/udp</div>
              </div>
            )}
            {iface.endpoint && (
              <div>
                <div className="text-muted text-sm">Endpoint</div>
                <div className="text-mono">{iface.endpoint}</div>
              </div>
            )}
            {iface.dns && (
              <div>
                <div className="text-muted text-sm">DNS</div>
                <div className="text-mono">{iface.dns}</div>
              </div>
            )}
            <div>
              <div className="text-muted text-sm">Public key</div>
              <div className="text-mono truncate" style={{ maxWidth: 280 }}>{iface.public_key || '—'}</div>
            </div>
          </div>

          {/* Obfuscation params */}
          {iface.obf_h1 != null && (
            <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 10 }}>
                <span className="card-title">Obfuscation Parameters</span>
                <div className="flex gap-2">
                  {iface.obf_generated_at && (
                    <span className="text-muted text-sm">
                      Generated {new Date(iface.obf_generated_at).toLocaleDateString()}
                    </span>
                  )}
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setRegenTarget(iface)}
                  >Regenerate</button>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, fontSize: 12 }}>
                {iface.obf_jc != null && (
                  <ObfParam label="Jc" value={iface.obf_jc} />
                )}
                {iface.obf_jmin != null && (
                  <ObfParam label="Jmin" value={iface.obf_jmin} />
                )}
                {iface.obf_jmax != null && (
                  <ObfParam label="Jmax" value={iface.obf_jmax} />
                )}
                <ObfParam label="S1" value={iface.obf_s1} />
                <ObfParam label="S2" value={iface.obf_s2} />
                <ObfParam label="S3" value={iface.obf_s3} />
                <ObfParam label="S4" value={iface.obf_s4} />
                <ObfParam label="H1" value={iface.obf_h1} />
                <ObfParam label="H2" value={iface.obf_h2} />
                <ObfParam label="H3" value={iface.obf_h3} />
                <ObfParam label="H4" value={iface.obf_h4} />
              </div>
            </div>
          )}
        </div>
      ))}

      {editing && (
        <EditModal
          iface={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            qc.invalidateQueries({ queryKey: ['interfaces'] })
          }}
        />
      )}

      {regenTarget && (
        <RegenConfirmModal
          iface={regenTarget}
          onClose={() => setRegenTarget(null)}
          onConfirm={() => {
            const target = regenTarget
            setRegenTarget(null)
            withBusy(target.id, 'Regenerating', () => regenObfuscation(target.id))
          }}
        />
      )}
    </>
  )
}

function RegenConfirmModal({ iface, onClose, onConfirm }: { iface: Interface; onClose: () => void; onConfirm: () => void }) {
  return (
    <Modal open title="Regenerate Obfuscation Parameters" onClose={onClose}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{
          display: 'flex', gap: 12, alignItems: 'flex-start',
          background: 'var(--bg-3)', borderRadius: 8, padding: '12px 14px',
        }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 1 }}>
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              After generating new parameters, all clients will lose connectivity.
            </div>
            <div className="text-muted">
              The new obfuscation parameters must match on both ends of the tunnel.
              You will need to redistribute configs to all clients of interface <span className="text-mono">{iface.name}</span> and ask them to reconnect.
            </div>
          </div>
        </div>
        <div className="text-muted" style={{ fontSize: 13 }}>
          Continue generating new parameters for <span className="text-mono">{iface.name}</span>?
        </div>
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" onClick={onConfirm}>Regenerate</button>
      </div>
    </Modal>
  )
}

function ObfParam({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div style={{ background: 'var(--bg-3)', borderRadius: 4, padding: '4px 8px' }}>
      <span className="text-muted">{label}:</span>{' '}
      <span className="text-mono">{value ?? '—'}</span>
    </div>
  )
}

function EditModal({ iface, onClose, onSaved }: { iface: Interface; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    listen_port: iface.listen_port ?? '',
    address: iface.address,
    dns: iface.dns ?? '',
    endpoint: iface.endpoint ?? '',
    allowed_ips: iface.allowed_ips ?? '',
    persistent_keepalive: iface.persistent_keepalive ?? '',
    enabled: iface.enabled,
  })
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => updateInterface(iface.id, {
      listen_port: form.listen_port ? Number(form.listen_port) : undefined,
      address: form.address || undefined,
      dns: form.dns || undefined,
      endpoint: form.endpoint || undefined,
      allowed_ips: form.allowed_ips || undefined,
      persistent_keepalive: form.persistent_keepalive ? Number(form.persistent_keepalive) : undefined,
      enabled: form.enabled,
    }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  const f = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }))

  return (
    <Modal open title={`Edit ${iface.name}`} onClose={onClose} size="lg">
      {error && <div className="error-box">{error}</div>}
      <div className="form-row form-row-2">
        {iface.mode === 'server' ? (
          <div className="form-group">
            <label className="form-label">Listen port</label>
            <input className="form-input mono" value={form.listen_port} onChange={f('listen_port')} />
          </div>
        ) : (
          <div className="form-group">
            <label className="form-label">Endpoint</label>
            <input className="form-input mono" value={form.endpoint} onChange={f('endpoint')} placeholder="host:port" />
          </div>
        )}
        <div className="form-group">
          <label className="form-label">Address</label>
          <input className="form-input mono" value={form.address} onChange={f('address')} />
        </div>
      </div>
      <div className="form-row form-row-2">
        <div className="form-group">
          <label className="form-label">DNS</label>
          <input className="form-input mono" value={form.dns} onChange={f('dns')} placeholder="1.1.1.1" />
        </div>
        <div className="form-group">
          <label className="form-label">Persistent keepalive</label>
          <input className="form-input mono" value={form.persistent_keepalive} onChange={f('persistent_keepalive')} placeholder="25" />
        </div>
      </div>
      {iface.mode === 'client' && (
        <div className="form-group">
          <label className="form-label">Allowed IPs</label>
          <input className="form-input mono" value={form.allowed_ips} onChange={f('allowed_ips')} placeholder="0.0.0.0/0" />
        </div>
      )}
      <div className="form-group">
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <label className="toggle">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))} />
            <span className="toggle-slider" />
          </label>
          <span className="form-label" style={{ marginBottom: 0 }}>Enabled</span>
        </label>
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => mut.mutate()} disabled={mut.isPending}>
          {mut.isPending ? <span className="spinner" /> : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
