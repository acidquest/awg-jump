import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getRoutingStatus, applyRouting, resetRouting } from '../api'

export default function Routing() {
  const qc = useQueryClient()

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['routing'],
    queryFn: () => getRoutingStatus().then((r) => r.data),
  })

  const applyMut = useMutation({
    mutationFn: applyRouting,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['routing'] }),
  })

  const resetMut = useMutation({
    mutationFn: resetRouting,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['routing'] }),
  })

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Routing</div>
          <div className="page-subtitle">Policy routing and iptables</div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >Refresh</button>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => applyMut.mutate()}
            disabled={applyMut.isPending}
          >
            {applyMut.isPending ? <span className="spinner" /> : 'Apply'}
          </button>
          <button
            className="btn btn-danger btn-sm"
            onClick={() => { if (confirm('Reset all routing rules?')) resetMut.mutate() }}
            disabled={resetMut.isPending}
          >
            {resetMut.isPending ? <span className="spinner" /> : 'Reset'}
          </button>
        </div>
      </div>

      {/* Routing policy diagram */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-title" style={{ marginBottom: 14 }}>Policy routing diagram</div>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
          color: 'var(--text-2)',
          background: 'var(--bg-3)',
          borderRadius: 6,
          padding: '16px 20px',
          lineHeight: 2.2,
        }}>
          <div>
            Client (AWG) ──► <span style={{ color: 'var(--accent)' }}>awg0</span> ──► [ipset/iptables mangle]
          </div>
          <div style={{ paddingLeft: 40 }}>
            ├── <span style={{ color: '#ffa502' }}>RU CIDR</span> (ipset: geoip_ru) ──► fwmark 0x1 ──► table 100 ──► <span style={{ color: 'var(--accent)' }}>eth0</span> (direct)
          </div>
          <div style={{ paddingLeft: 40 }}>
            └── <span style={{ color: 'var(--text)' }}>other</span> ──► fwmark 0x2 ──► table 200 ──► <span style={{ color: 'var(--accent)' }}>awg1</span> ──► upstream node
          </div>
        </div>
      </div>

      {isLoading && <div style={{ textAlign: 'center', padding: 24 }}><span className="spinner" /></div>}

      {data && (
        <>
          {/* IP rules */}
          {data.ip_rules && (
            <div className="section">
              <div className="section-title">IP Rules</div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Priority</th>
                      <th>Rule</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.ip_rules as string[]).map((rule: string, i: number) => (
                      <tr key={i}>
                        <td className="text-mono text-muted">{i}</td>
                        <td className="text-mono" style={{ fontSize: 12 }}>{rule}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* IP routes */}
          {data.ip_routes && Object.entries(data.ip_routes as Record<string, string[]>).map(([table, routes]) => (
            <div className="section" key={table}>
              <div className="section-title">IP Routes — table {table}</div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Route</th></tr>
                  </thead>
                  <tbody>
                    {(routes as string[]).map((r: string, i: number) => (
                      <tr key={i}>
                        <td className="text-mono" style={{ fontSize: 12 }}>{r}</td>
                      </tr>
                    ))}
                    {routes.length === 0 && (
                      <tr><td className="text-muted" style={{ textAlign: 'center' }}>Empty</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {/* Raw JSON fallback */}
          {!data.ip_rules && !data.ip_routes && (
            <div className="card">
              <div className="card-title" style={{ marginBottom: 10 }}>Raw status</div>
              <pre className="mono" style={{ fontSize: 11, color: 'var(--text-2)', overflowX: 'auto' }}>
                {JSON.stringify(data, null, 2)}
              </pre>
            </div>
          )}
        </>
      )}
    </>
  )
}
