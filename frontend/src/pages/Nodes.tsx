import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getNodes, createNode, deployNode, redeployNode,
  activateNode, checkNode, deleteNode, getNodeStats
} from '../api'
import { Node, NodeStats, DeployLog } from '../types'
import StatusBadge from '../components/StatusBadge'
import Modal from '../components/Modal'
import { openSSE } from '../sse'

function fmtBytes(n: number | null) {
  if (!n) return '0'
  const u = ['B', 'KB', 'MB', 'GB']
  let v = n; let i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${u[i]}`
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString()
}

type DeployStep = {
  ts: string
  msg: string
  type: 'info' | 'success' | 'error' | 'default'
}

export default function Nodes() {
  const qc = useQueryClient()

  const [showDeploy, setShowDeploy] = useState(false)
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [redeployNode_, setRedeployNode] = useState<Node | null>(null)
  const [logModal, setLogModal] = useState<DeployLog | null>(null)

  const { data: nodes = [], isLoading } = useQuery<Node[]>({
    queryKey: ['nodes'],
    queryFn: () => getNodes().then((r) => r.data),
    refetchInterval: 30_000,
  })

  const { data: stats } = useQuery<NodeStats>({
    queryKey: ['node-stats', selectedNode?.id],
    queryFn: () => getNodeStats(selectedNode!.id).then((r) => r.data),
    enabled: !!selectedNode,
    refetchInterval: 15_000,
  })

  const activateMut = useMutation({
    mutationFn: (id: number) => activateNode(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })

  const checkMut = useMutation({
    mutationFn: (id: number) => checkNode(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteNode(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ['nodes'] })
      if (selectedNode?.id === id) setSelectedNode(null)
    },
  })

  if (isLoading) return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Upstream Nodes</div>
          <div className="page-subtitle">Remote AWG exit nodes</div>
        </div>
        <button className="btn btn-primary" onClick={() => setShowDeploy(true)}>
          + Deploy node
        </button>
      </div>

      {/* Nodes table */}
      <div className="section">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Active</th>
                <th>Name</th>
                <th>Host</th>
                <th>Status</th>
                <th>Latency</th>
                <th>RX / TX</th>
                <th>Last seen</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {nodes.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-muted" style={{ textAlign: 'center', padding: 32 }}>
                    No nodes deployed yet
                  </td>
                </tr>
              ) : nodes.map((n) => (
                <tr
                  key={n.id}
                  className={n.is_active ? 'active-node' : ''}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedNode(selectedNode?.id === n.id ? null : n)}
                >
                  <td onClick={(e) => e.stopPropagation()}>
                    <input
                      type="radio"
                      name="active-node"
                      checked={n.is_active}
                      onChange={() => {
                        if (!n.is_active && (n.status === 'online' || n.status === 'degraded')) {
                          activateMut.mutate(n.id)
                        }
                      }}
                      style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
                    />
                  </td>
                  <td>
                    <span style={{ fontWeight: 500 }}>{n.name}</span>
                    {n.is_active && (
                      <span className="badge badge-online" style={{ marginLeft: 8 }}>active</span>
                    )}
                  </td>
                  <td className="text-mono">{n.host}</td>
                  <td><StatusBadge status={n.status} /></td>
                  <td className="text-mono">
                    {n.latency_ms != null ? `${n.latency_ms.toFixed(0)} ms` : '—'}
                  </td>
                  <td className="text-mono" style={{ fontSize: 12 }}>
                    {fmtBytes(n.rx_bytes)} / {fmtBytes(n.tx_bytes)}
                  </td>
                  <td className="text-muted" style={{ fontSize: 12 }}>{fmtDate(n.last_seen)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-2">
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => activateMut.mutate(n.id)}
                        disabled={n.is_active || !['online', 'degraded'].includes(n.status)}
                        title="Set as active"
                      >Activate</button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => checkMut.mutate(n.id)}
                        title="Health check"
                      >Check</button>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setRedeployNode(n)}
                        title="Redeploy"
                      >Redeploy</button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => { if (confirm(`Delete node ${n.name}?`)) deleteMut.mutate(n.id) }}
                      >Del</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Node stats panel */}
      {selectedNode && stats && (
        <div className="section">
          <div className="section-title">
            Node stats — {selectedNode.name}
            <button className="btn btn-ghost btn-sm" onClick={() => setSelectedNode(null)}>✕</button>
          </div>

          <div className="card-grid card-grid-3" style={{ marginBottom: 16 }}>
            <div className="card">
              <div className="stat-value text-mono" style={{ fontSize: 18 }}>
                {stats.latency_ms != null ? `${stats.latency_ms.toFixed(0)} ms` : '—'}
              </div>
              <div className="stat-label">latency</div>
            </div>
            <div className="card">
              <div className="stat-value text-mono" style={{ fontSize: 18 }}>{fmtBytes(stats.rx_bytes)}</div>
              <div className="stat-label">received</div>
            </div>
            <div className="card">
              <div className="stat-value text-mono" style={{ fontSize: 18 }}>{fmtBytes(stats.tx_bytes)}</div>
              <div className="stat-label">sent</div>
            </div>
          </div>

          {/* Deploy logs */}
          <div className="card-title" style={{ marginBottom: 10 }}>Deploy history</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Finished</th>
                  <th>Status</th>
                  <th>Log</th>
                </tr>
              </thead>
              <tbody>
                {stats.deploy_logs.length === 0 ? (
                  <tr><td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 16 }}>No deploys</td></tr>
                ) : stats.deploy_logs.map((log) => (
                  <tr key={log.id}>
                    <td className="text-muted" style={{ fontSize: 12 }}>{fmtDate(log.started_at)}</td>
                    <td className="text-muted" style={{ fontSize: 12 }}>{fmtDate(log.finished_at)}</td>
                    <td><StatusBadge status={log.status} /></td>
                    <td>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setLogModal(log)}
                      >View log</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Modals */}
      {showDeploy && (
        <DeployModal
          onClose={() => setShowDeploy(false)}
          onDone={() => {
            setShowDeploy(false)
            qc.invalidateQueries({ queryKey: ['nodes'] })
          }}
        />
      )}

      {redeployNode_ && (
        <RedeployModal
          node={redeployNode_}
          onClose={() => setRedeployNode(null)}
          onDone={() => {
            setRedeployNode(null)
            qc.invalidateQueries({ queryKey: ['nodes'] })
          }}
        />
      )}

      {logModal && (
        <Modal open title="Deploy log" onClose={() => setLogModal(null)} size="xl">
          <div className="terminal" style={{ maxHeight: 600 }}>
            <pre style={{ color: '#a8b5c2', fontSize: 11, whiteSpace: 'pre-wrap' }}>
              {logModal.log_output || '(empty)'}
            </pre>
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setLogModal(null)}>Close</button>
          </div>
        </Modal>
      )}
    </>
  )
}

// ── Deploy modal ──────────────────────────────────────────────────────────

function DeployModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [phase, setPhase] = useState<'form' | 'deploying' | 'done'>('form')
  const [form, setForm] = useState({
    name: '',
    host: '',
    ssh_port: '22',
    ssh_user: 'root',
    ssh_password: '',
    awg_port: '51821',
    priority: '100',
  })
  const [lines, setLines] = useState<DeployStep[]>([])
  const [error, setError] = useState('')
  const [progress, setProgress] = useState(0)
  const termRef = useRef<HTMLDivElement>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  const f = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }))

  const addLine = (msg: string, type: DeployStep['type'] = 'default') =>
    setLines((l) => [...l, { ts: new Date().toLocaleTimeString(), msg, type }])

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight
    }
  }, [lines])

  useEffect(() => () => { cleanupRef.current?.() }, [])

  const start = async () => {
    setError('')
    setPhase('deploying')
    setLines([])
    setProgress(5)

    try {
      // 1. Create node record
      const nodeRes = await createNode({
        name: form.name,
        host: form.host,
        ssh_port: Number(form.ssh_port),
        awg_port: Number(form.awg_port),
        priority: Number(form.priority),
      })
      const nodeId: number = nodeRes.data.id

      addLine(`Node created: ${form.name} (id=${nodeId})`, 'info')
      setProgress(10)

      // 2. Deploy
      const deployRes = await deployNode({
        node_id: nodeId,
        ssh_user: form.ssh_user,
        ssh_password: form.ssh_password,
        ssh_port: Number(form.ssh_port),
      })
      const logId: number = deployRes.data.deploy_log_id
      addLine(`Deploy started, log #${logId}`, 'info')

      // 3. SSE stream
      let stepCount = 0
      const cleanup = openSSE(
        `/api/nodes/deploy/${logId}/stream`,
        (raw) => {
          const data = raw as { message?: string; status?: string; finished?: boolean }
          const msg: string = data.message ?? ''
          const type = msg.startsWith('✅') || msg.startsWith('OK') ? 'success'
            : msg.startsWith('❌') || msg.toLowerCase().includes('error') ? 'error'
            : msg.startsWith('[') || msg.startsWith('+') ? 'info'
            : 'default'
          if (msg && msg !== '__done__') addLine(msg, type)

          stepCount++
          setProgress(Math.min(10 + stepCount * 10, 90))

          if (data.status === 'done' || data.finished || msg === '__done__') {
            cleanup()
            setProgress(100)
            setPhase('done')
            addLine('Deployment complete', 'success')
          }
        },
        () => { setProgress(100); setPhase('done') }
      )
      cleanupRef.current = cleanup
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Deploy failed'
      setError(msg)
      setPhase('form')
      setProgress(0)
    }
  }

  return (
    <Modal
      open
      title="Deploy new node"
      onClose={() => { cleanupRef.current?.(); onClose() }}
      size="lg"
    >
      {phase === 'form' && (
        <>
          {error && <div className="error-box">{error}</div>}
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">Node name</label>
              <input className="form-input" value={form.name} onChange={f('name')} placeholder="vps-us-01" required />
            </div>
            <div className="form-group">
              <label className="form-label">Host (IP / hostname)</label>
              <input className="form-input mono" value={form.host} onChange={f('host')} placeholder="1.2.3.4" required />
            </div>
          </div>
          <div className="form-row form-row-3">
            <div className="form-group">
              <label className="form-label">SSH port</label>
              <input className="form-input mono" value={form.ssh_port} onChange={f('ssh_port')} />
            </div>
            <div className="form-group">
              <label className="form-label">SSH user</label>
              <input className="form-input mono" value={form.ssh_user} onChange={f('ssh_user')} />
            </div>
            <div className="form-group">
              <label className="form-label">AWG port</label>
              <input className="form-input mono" value={form.awg_port} onChange={f('awg_port')} />
            </div>
          </div>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">SSH password</label>
              <input className="form-input" type="password" value={form.ssh_password} onChange={f('ssh_password')} required />
            </div>
            <div className="form-group">
              <label className="form-label">Priority</label>
              <input className="form-input mono" value={form.priority} onChange={f('priority')} />
            </div>
          </div>
          <div className="info-box" style={{ fontSize: 12 }}>
            SSH credentials are not stored. AWG keys are generated automatically.
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button
              className="btn btn-primary"
              onClick={start}
              disabled={!form.name || !form.host || !form.ssh_password}
            >
              Deploy
            </button>
          </div>
        </>
      )}

      {(phase === 'deploying' || phase === 'done') && (
        <>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
          </div>
          <div className="terminal" ref={termRef}>
            {lines.map((l, i) => (
              <div key={i} className={`terminal-line ${l.type}`}>
                <span className="ts">{l.ts}</span>
                <span className="msg">{l.msg}</span>
              </div>
            ))}
            {phase === 'deploying' && (
              <div className="terminal-line">
                <span className="ts" />
                <span className="msg"><span className="spinner" /></span>
              </div>
            )}
          </div>
          {phase === 'done' && (
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={onDone}>Done</button>
            </div>
          )}
        </>
      )}
    </Modal>
  )
}

// ── Redeploy modal ────────────────────────────────────────────────────────

function RedeployModal({ node, onClose, onDone }: { node: Node; onClose: () => void; onDone: () => void }) {
  const [phase, setPhase] = useState<'form' | 'deploying' | 'done'>('form')
  const [ssh_user, setSshUser] = useState('root')
  const [ssh_password, setSshPassword] = useState('')
  const [lines, setLines] = useState<DeployStep[]>([])
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const termRef = useRef<HTMLDivElement>(null)
  const cleanupRef2 = useRef<(() => void) | null>(null)

  const addLine = (msg: string, type: DeployStep['type'] = 'default') =>
    setLines((l) => [...l, { ts: new Date().toLocaleTimeString(), msg, type }])

  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [lines])

  useEffect(() => () => { cleanupRef2.current?.() }, [])

  const start = async () => {
    setError('')
    setPhase('deploying')
    setLines([])
    setProgress(5)

    try {
      const res = await redeployNode(node.id, {
        ssh_user,
        ssh_password,
        ssh_port: node.ssh_port,
      })
      const logId: number = res.data.deploy_log_id
      addLine(`Redeploy started, log #${logId}`, 'info')
      setProgress(15)

      let stepCount = 0
      const cleanup = openSSE(
        `/api/nodes/deploy/${logId}/stream`,
        (raw) => {
          const data = raw as { message?: string; status?: string; finished?: boolean }
          const msg: string = data.message ?? ''
          const type = msg.startsWith('✅') ? 'success' : msg.startsWith('❌') ? 'error' : 'default'
          if (msg && msg !== '__done__') addLine(msg, type)
          stepCount++
          setProgress(Math.min(15 + stepCount * 12, 90))
          if (data.status === 'done' || data.finished || msg === '__done__') {
            cleanup(); setProgress(100); setPhase('done')
            addLine('Redeployment complete', 'success')
          }
        },
        () => { setProgress(100); setPhase('done') }
      )
      cleanupRef2.current = cleanup
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg); setPhase('form'); setProgress(0)
    }
  }

  return (
    <Modal open title={`Redeploy — ${node.name}`} onClose={() => { cleanupRef2.current?.(); onClose() }} size="lg">
      {phase === 'form' && (
        <>
          {error && <div className="error-box">{error}</div>}
          <div className="info-box" style={{ fontSize: 12 }}>
            Existing AWG keys will be preserved. Sources will be updated.
          </div>
          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label">SSH user</label>
              <input className="form-input mono" value={ssh_user} onChange={(e) => setSshUser(e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">SSH password</label>
              <input className="form-input" type="password" value={ssh_password} onChange={(e) => setSshPassword(e.target.value)} required />
            </div>
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={start} disabled={!ssh_password}>Redeploy</button>
          </div>
        </>
      )}
      {(phase === 'deploying' || phase === 'done') && (
        <>
          <div className="progress-bar"><div className="progress-bar-fill" style={{ width: `${progress}%` }} /></div>
          <div className="terminal" ref={termRef}>
            {lines.map((l, i) => (
              <div key={i} className={`terminal-line ${l.type}`}>
                <span className="ts">{l.ts}</span>
                <span className="msg">{l.msg}</span>
              </div>
            ))}
            {phase === 'deploying' && <div className="terminal-line"><span /><span className="msg"><span className="spinner" /></span></div>}
          </div>
          {phase === 'done' && (
            <div className="modal-actions"><button className="btn btn-primary" onClick={onDone}>Done</button></div>
          )}
        </>
      )}
    </Modal>
  )
}
