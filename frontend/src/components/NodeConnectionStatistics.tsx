import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '../api'
import { formatDateTimeLocal } from '../utils/time'

type Statistics = {
  disconnects: number; downtime_seconds: number; ongoing_since: string | null
  attempts: number; attempt_errors: number; total_pages: number
  per_node: Array<{ node_id: number | null; node_name: string; attempts: number; errors: number }>
  incidents: Array<{ id: number; node_name: string; started_at: string; ended_at: string | null; duration_seconds: number }>
  recent_attempts: Array<{ id: number; node_name: string; created_at: string; applied: boolean; error: string | null }>
}
function duration(seconds: number) {
  const n = Math.floor(seconds)
  return `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m ${n % 60}s`
}
export default function NodeConnectionStatistics() {
  const [days, setDays] = useState(1)
  const [page, setPage] = useState(1)
  const { data, isError, isLoading } = useQuery<Statistics>({
    queryKey: ['node-connections', days, page],
    queryFn: () => api.get('/nodes/connection-statistics', { params: { days, page } }).then(r => r.data),
    refetchInterval: 15_000,
  })
  return <section className="card" style={{ marginBottom: 20 }}>
    <div className="card-header" style={{ flexWrap: 'wrap', gap: 12 }}><div className="card-title">Connection problems</div>
      <select className="form-input" style={{ width: 'auto', maxWidth: '100%' }} aria-label="Statistics period" value={days} onChange={e => { setDays(Number(e.target.value)); setPage(1) }}>
        <option value={1}>Last 24 hours</option><option value={30}>Last 30 days</option>
      </select>
    </div>
    <p className="text-muted text-sm">Observed upstream tunnel outages. Timing follows health checks; history starts after this update. Configuration applied does not mean a handshake has succeeded.</p>
    {isError ? <div className="error-box">Unable to load connection statistics.</div> : isLoading ? <p>Loading…</p> : data && <>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, marginBottom: 16 }}>
        <div>Disconnects: <strong>{data.disconnects}</strong></div>
        <div>Time disconnected: <strong>{duration(data.downtime_seconds)}</strong></div>
        <div>Reconnect attempts: <strong>{data.attempts}</strong></div>
        <div>Configuration errors: <strong>{data.attempt_errors}</strong></div>
      </div>
      {data.ongoing_since && <div className="error-box">Disconnected since {formatDateTimeLocal(data.ongoing_since)}. Automatic retries continue.</div>}
      <div className="table-wrap"><table><thead><tr><th>Node</th><th>Reconnect attempts</th><th>Configuration errors</th></tr></thead>
        <tbody>{data.per_node.map((n, i) => <tr key={`${n.node_id}-${i}`}><td>{n.node_name}</td><td>{n.attempts}</td><td>{n.errors}</td></tr>)}</tbody></table></div>
      <h3>Outage history</h3>
      {!data.incidents.length ? <p className="text-muted">No outages recorded in this period.</p> : <div className="table-wrap"><table>
        <thead><tr><th>Node at disconnect</th><th>Disconnected</th><th>Restored</th><th>Duration</th></tr></thead>
        <tbody>{data.incidents.map(i => <tr key={i.id}><td>{i.node_name}</td><td>{formatDateTimeLocal(i.started_at)}</td><td>{i.ended_at ? formatDateTimeLocal(i.ended_at) : 'Retrying…'}</td><td>{duration(i.duration_seconds)}</td></tr>)}</tbody>
      </table></div>}
      <div className="flex gap-2" style={{ marginTop: 12 }}>
        <button className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>{page} / {data.total_pages}</span>
        <button className="btn btn-secondary btn-sm" disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      <details style={{ marginTop: 16 }}><summary>Latest reconnect attempts (up to 20)</summary>
        <div className="table-wrap"><table><thead><tr><th>Time</th><th>Node</th><th>Result</th></tr></thead>
          <tbody>{data.recent_attempts.map(a => <tr key={a.id}><td>{formatDateTimeLocal(a.created_at)}</td><td>{a.node_name}</td><td>{a.applied ? 'Configuration applied (handshake checked separately)' : a.error || 'Applying configuration…'}</td></tr>)}</tbody>
        </table></div>
      </details>
    </>}
  </section>
}
