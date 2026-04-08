import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getInterfaces, getPeers, createPeer, updatePeer,
  deletePeer, togglePeer, getPeerConfig, getPeerQr
} from '../api'
import { Interface, Peer } from '../types'
import StatusBadge from '../components/StatusBadge'
import Modal from '../components/Modal'
import { parseUtcDate } from '../utils/time'

function fmtBytes(n: number | null) {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB']
  let v = n; let i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${u[i]}`
}

function fmtHandshake(ts: string | null) {
  if (!ts) return 'never'
  const date = parseUtcDate(ts)
  if (!date) return 'never'
  const diff = Date.now() - date.getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  return `${Math.floor(m / 60)}h ago`
}

export default function Peers() {
  const qc = useQueryClient()
  const [ifaceId, setIfaceId] = useState<number | undefined>()
  const [showCreate, setShowCreate] = useState(false)
  const [qrPeer, setQrPeer] = useState<Peer | null>(null)
  const [editPeer, setEditPeer] = useState<Peer | null>(null)

  const { data: ifaces = [] } = useQuery<Interface[]>({
    queryKey: ['interfaces'],
    queryFn: () => getInterfaces().then((r) => r.data),
  })

  const { data: peers = [], isLoading } = useQuery<Peer[]>({
    queryKey: ['peers', ifaceId],
    queryFn: () => getPeers(ifaceId).then((r) => r.data),
    refetchInterval: 30_000,
  })

  const toggleMut = useMutation({
    mutationFn: (id: number) => togglePeer(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['peers'] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deletePeer(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['peers'] }),
  })

  const downloadConfig = async (peer: Peer) => {
    const res = await getPeerConfig(peer.id)
    const blob = new Blob([res.data as string], { type: 'text/plain' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${peer.name || `peer-${peer.id}`}.conf`
    a.click()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Peers</div>
          <div className="page-subtitle">awg0 client configurations</div>
        </div>
        <div className="flex gap-2">
          <select
            className="form-input"
            style={{ width: 160 }}
            value={ifaceId ?? ''}
            onChange={(e) => setIfaceId(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">All interfaces</option>
            {ifaces.map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </select>
          <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Add peer</button>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Tunnel IP</th>
              <th>Status</th>
              <th>Last handshake</th>
              <th>RX / TX</th>
              <th>Interface</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={7} style={{ textAlign: 'center', padding: 24 }}><span className="spinner" /></td></tr>
            ) : peers.length === 0 ? (
              <tr><td colSpan={7} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>No peers</td></tr>
            ) : peers.map((p) => {
              const iface = ifaces.find((i) => i.id === p.interface_id)
              return (
                <tr key={p.id}>
                  <td>
                    <span style={{ fontWeight: 500 }}>{p.name || `peer-${p.id}`}</span>
                  </td>
                  <td className="text-mono">{p.tunnel_address ?? '—'}</td>
                  <td>
                    <label className="toggle" title={p.enabled ? 'Click to disable' : 'Click to enable'}>
                      <input
                        type="checkbox"
                        checked={p.enabled}
                        onChange={() => toggleMut.mutate(p.id)}
                      />
                      <span className="toggle-slider" />
                    </label>
                  </td>
                  <td className="text-muted" style={{ fontSize: 12 }}>{fmtHandshake(p.last_handshake)}</td>
                  <td className="text-mono" style={{ fontSize: 12 }}>
                    {fmtBytes(p.rx_bytes)} / {fmtBytes(p.tx_bytes)}
                  </td>
                  <td className="text-mono text-muted">{iface?.name ?? `#${p.interface_id}`}</td>
                  <td>
                    <div className="flex gap-2">
                      <button className="btn btn-ghost btn-sm" onClick={() => downloadConfig(p)} title="Download config">
                        DL
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setQrPeer(p)} title="QR code">
                        QR
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditPeer(p)}>Edit</button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => { if (confirm('Delete peer?')) deleteMut.mutate(p.id) }}
                      >Del</button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <CreatePeerModal
          ifaces={ifaces}
          onClose={() => setShowCreate(false)}
          onSaved={() => {
            setShowCreate(false)
            qc.invalidateQueries({ queryKey: ['peers'] })
          }}
        />
      )}

      {editPeer && (
        <EditPeerModal
          peer={editPeer}
          onClose={() => setEditPeer(null)}
          onSaved={() => {
            setEditPeer(null)
            qc.invalidateQueries({ queryKey: ['peers'] })
          }}
        />
      )}

      {qrPeer && (
        <QrModal
          peer={qrPeer}
          onClose={() => setQrPeer(null)}
        />
      )}
    </>
  )
}

function CreatePeerModal({ ifaces, onClose, onSaved }: {
  ifaces: Interface[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState({
    name: '',
    interface_id: ifaces[0]?.id ?? '',
    tunnel_address: '',
    allowed_ips: '0.0.0.0/0',
    persistent_keepalive: '',
  })
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => createPeer({
      name: form.name,
      interface_id: Number(form.interface_id),
      tunnel_address: form.tunnel_address || undefined,
      allowed_ips: form.allowed_ips,
      persistent_keepalive: form.persistent_keepalive ? Number(form.persistent_keepalive) : undefined,
    }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  const f = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }))

  return (
    <Modal open title="Add peer" onClose={onClose}>
      {error && <div className="error-box">{error}</div>}
      <div className="form-group">
        <label className="form-label">Name</label>
        <input className="form-input" value={form.name} onChange={f('name')} placeholder="device-name" />
      </div>
      <div className="form-group">
        <label className="form-label">Interface</label>
        <select className="form-input" value={form.interface_id} onChange={f('interface_id')}>
          {ifaces.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </div>
      <div className="form-row form-row-2">
        <div className="form-group">
          <label className="form-label">Tunnel address (optional)</label>
          <input className="form-input mono" value={form.tunnel_address} onChange={f('tunnel_address')} placeholder="10.10.0.2/32" />
        </div>
        <div className="form-group">
          <label className="form-label">Allowed IPs</label>
          <input className="form-input mono" value={form.allowed_ips} onChange={f('allowed_ips')} />
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Persistent keepalive</label>
        <input className="form-input mono" value={form.persistent_keepalive} onChange={f('persistent_keepalive')} placeholder="25" />
      </div>
      <div className="info-box" style={{ fontSize: 12 }}>
        Keys will be auto-generated if not specified
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => mut.mutate()} disabled={mut.isPending}>
          {mut.isPending ? <span className="spinner" /> : 'Create'}
        </button>
      </div>
    </Modal>
  )
}

function EditPeerModal({ peer, onClose, onSaved }: { peer: Peer; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: peer.name,
    allowed_ips: peer.allowed_ips,
    tunnel_address: peer.tunnel_address ?? '',
    persistent_keepalive: peer.persistent_keepalive ?? '',
  })
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => updatePeer(peer.id, {
      name: form.name || undefined,
      allowed_ips: form.allowed_ips || undefined,
      tunnel_address: form.tunnel_address || undefined,
      persistent_keepalive: form.persistent_keepalive ? Number(form.persistent_keepalive) : undefined,
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
    <Modal open title={`Edit peer: ${peer.name}`} onClose={onClose}>
      {error && <div className="error-box">{error}</div>}
      <div className="form-group">
        <label className="form-label">Name</label>
        <input className="form-input" value={form.name} onChange={f('name')} />
      </div>
      <div className="form-group">
        <label className="form-label">Tunnel address</label>
        <input className="form-input mono" value={form.tunnel_address} onChange={f('tunnel_address')} />
      </div>
      <div className="form-group">
        <label className="form-label">Allowed IPs</label>
        <input className="form-input mono" value={form.allowed_ips} onChange={f('allowed_ips')} />
      </div>
      <div className="form-group">
        <label className="form-label">Persistent keepalive</label>
        <input className="form-input mono" value={String(form.persistent_keepalive)} onChange={f('persistent_keepalive')} />
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

function QrModal({ peer, onClose }: { peer: Peer; onClose: () => void }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let objectUrl: string | null = null
    setLoading(true)
    setError('')
    getPeerQr(peer.id)
      .then((res) => {
        objectUrl = URL.createObjectURL(res.data as Blob)
        setBlobUrl(objectUrl)
      })
      .catch(() => setError('Failed to load QR code'))
      .finally(() => setLoading(false))
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [peer.id])

  return (
    <Modal open title={`QR — ${peer.name}`} onClose={onClose}>
      <div style={{ textAlign: 'center', minHeight: 120 }}>
        {loading && <span className="spinner" />}
        {error && <div className="error-box">{error}</div>}
        {!loading && !error && blobUrl && (
          <img
            src={blobUrl}
            alt="QR code"
            style={{ maxWidth: 280, imageRendering: 'pixelated', border: '1px solid var(--border)', borderRadius: 8 }}
          />
        )}
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
        {blobUrl && (
          <a
            href={blobUrl}
            download={`${peer.name}.png`}
            className="btn btn-primary"
          >
            Download PNG
          </a>
        )}
      </div>
    </Modal>
  )
}
