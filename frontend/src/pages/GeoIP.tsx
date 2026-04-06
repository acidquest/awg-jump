import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getGeoipStatus, triggerGeoipUpdate } from '../api'
import StatusBadge from '../components/StatusBadge'
import { openSSE } from '../sse'

type ProgressLine = { ts: string; msg: string; done?: boolean }

export default function GeoIP() {
  const [lines, setLines] = useState<ProgressLine[]>([])
  const [updating, setUpdating] = useState(false)
  const cleanupRef = useRef<(() => void) | null>(null)
  const termRef = useRef<HTMLDivElement>(null)

  const { data, refetch } = useQuery({
    queryKey: ['geoip-status'],
    queryFn: () => getGeoipStatus().then((r) => r.data),
    refetchInterval: 10_000,
  })

  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [lines])

  useEffect(() => () => { cleanupRef.current?.() }, [])

  const startUpdate = async () => {
    setUpdating(true)
    setLines([])

    try {
      await triggerGeoipUpdate()
    } catch {
      // 409 = already running
    }

    const cleanup = openSSE(
      '/api/geoip/progress',
      (raw) => {
        const data = raw as { message?: string }
        const msg: string = data.message ?? (typeof raw === 'string' ? raw : '')
        if (!msg) return
        setLines((l) => [...l, { ts: new Date().toLocaleTimeString(), msg }])
        if (msg === '__done__') {
          cleanup()
          setUpdating(false)
          refetch()
        }
      },
      () => { setUpdating(false) }
    )
    cleanupRef.current = cleanup
  }

  const sources = data?.sources ?? []

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">GeoIP</div>
          <div className="page-subtitle">IP prefix sets for routing</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={() => refetch()}>Refresh</button>
          <button
            className="btn btn-primary btn-sm"
            onClick={startUpdate}
            disabled={updating || data?.update_running}
          >
            {(updating || data?.update_running) ? (
              <><span className="spinner" /> Updating…</>
            ) : 'Update now'}
          </button>
        </div>
      </div>

      {/* Sources table */}
      <div className="section">
        <div className="section-title">Sources</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Country</th>
                <th>Name</th>
                <th>IPSet</th>
                <th>Prefixes (DB)</th>
                <th>Prefixes (ipset)</th>
                <th>Last updated</th>
                <th>Cache</th>
              </tr>
            </thead>
            <tbody>
              {sources.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>
                    No sources configured
                  </td>
                </tr>
              ) : sources.map((s: {
                id: number
                country_code: string
                name?: string
                ipset_name: string
                prefix_count: number
                ipset_count: number
                last_updated: string | null
                cache_fresh: boolean
              }) => (
                <tr key={s.id}>
                  <td>
                    <span className="badge badge-unknown">{s.country_code.toUpperCase()}</span>
                  </td>
                  <td>{s.name || s.country_code}</td>
                  <td className="text-mono">{s.ipset_name}</td>
                  <td className="text-mono">{s.prefix_count?.toLocaleString() ?? 0}</td>
                  <td className="text-mono">{s.ipset_count?.toLocaleString() ?? 0}</td>
                  <td className="text-muted" style={{ fontSize: 12 }}>
                    {s.last_updated ? new Date(s.last_updated).toLocaleString() : 'never'}
                  </td>
                  <td>
                    <StatusBadge status={s.cache_fresh ? 'online' : 'offline'} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Progress terminal */}
      {(lines.length > 0 || updating) && (
        <div className="section">
          <div className="section-title">Update progress</div>
          <div className="terminal" ref={termRef}>
            {lines.map((l, i) => (
              <div key={i} className="terminal-line">
                <span className="ts">{l.ts}</span>
                <span className="msg">{l.msg}</span>
              </div>
            ))}
            {updating && (
              <div className="terminal-line">
                <span className="ts" />
                <span className="msg"><span className="spinner" /></span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
