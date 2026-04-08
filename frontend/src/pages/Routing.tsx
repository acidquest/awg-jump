import { useState } from 'react'
import type { AxiosError } from 'axios'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  applyRouting,
  getRoutingStatus,
  resetRouting,
  updateRoutingSettings,
} from '../api'
import type { RoutingStatus } from '../types'

export default function Routing() {
  const qc = useQueryClient()
  const [error, setError] = useState('')

  const { data, isLoading, refetch } = useQuery<RoutingStatus>({
    queryKey: ['routing'],
    queryFn: () => getRoutingStatus().then((r) => r.data),
  })

  const applyMut = useMutation({
    mutationFn: applyRouting,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing'] })
      qc.invalidateQueries({ queryKey: ['system-status'] })
    },
  })

  const resetMut = useMutation({
    mutationFn: resetRouting,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing'] })
      qc.invalidateQueries({ queryKey: ['system-status'] })
    },
  })

  const settingsMut = useMutation({
    mutationFn: (invert_geoip: boolean) => updateRoutingSettings({ invert_geoip }),
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['routing'] })
      qc.invalidateQueries({ queryKey: ['system-status'] })
    },
    onError: (err: unknown) => setError(getErrorMessage(err, 'Failed to update routing mode')),
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

      {error ? <div className="error-box" style={{ marginBottom: 16 }}>{error}</div> : null}

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header" style={{ marginBottom: 10 }}>
          <div>
            <div className="card-title">Traffic Direction</div>
            <div className="text-muted text-sm" style={{ marginTop: 6 }}>
              Normal mode routes GeoIP local zone through the physical interface. Inverted mode swaps the directions.
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div className="text-mono text-sm" style={{ color: 'var(--text)' }}>
              {data?.invert_geoip ? 'Inverted' : 'Normal'}
            </div>
            <label className="toggle" title="Invert GeoIP routing">
              <input
                type="checkbox"
                checked={Boolean(data?.invert_geoip)}
                onChange={(e) => settingsMut.mutate(e.target.checked)}
                disabled={settingsMut.isPending || isLoading}
              />
              <span className="toggle-slider" />
            </label>
          </div>
        </div>
        <div className="card-grid card-grid-2">
          <DirectionCard
            title="GeoIP Local Zone"
            destination={data?.geoip_destination}
            mark={data?.geoip_mark}
            physicalIface={data?.physical_iface}
          />
          <DirectionCard
            title="Other Traffic"
            destination={data?.other_destination}
            mark={data?.other_mark}
            physicalIface={data?.physical_iface}
          />
        </div>
      </div>

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
            ├── <span style={{ color: '#ffa502' }}>LOCAL CIDR</span> ──► fwmark {data?.geoip_mark ?? '—'} ──► {routeTableForDestination(data?.geoip_destination)} ──► <span style={{ color: 'var(--accent)' }}>{routeIfaceForDestination(data?.geoip_destination, data?.physical_iface)}</span> ({routeLabelForDestination(data?.geoip_destination)})
          </div>
          <div style={{ paddingLeft: 40 }}>
            └── <span style={{ color: 'var(--text)' }}>other</span> ──► fwmark {data?.other_mark ?? '—'} ──► {routeTableForDestination(data?.other_destination)} ──► <span style={{ color: 'var(--accent)' }}>{routeIfaceForDestination(data?.other_destination, data?.physical_iface)}</span> ({routeLabelForDestination(data?.other_destination)})
          </div>
        </div>
      </div>

      {isLoading && <div style={{ textAlign: 'center', padding: 24 }}><span className="spinner" /></div>}

      {data && (
        <>
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
                    {data.ip_rules.map((rule, i) => (
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

          {data.ip_routes && Object.entries(data.ip_routes).map(([table, routes]) => (
            <div className="section" key={table}>
              <div className="section-title">IP Routes — table {table}</div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Route</th></tr>
                  </thead>
                  <tbody>
                    {routes.map((r, i) => (
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
        </>
      )}
    </>
  )
}

function DirectionCard({
  title,
  destination,
  mark,
  physicalIface,
}: {
  title: string
  destination?: 'local' | 'vpn'
  mark?: string
  physicalIface?: string
}) {
  return (
    <div className="card" style={{ background: 'var(--bg-3)', padding: 14 }}>
      <div className="card-title" style={{ marginBottom: 8 }}>{title}</div>
      <div className="stat-value" style={{ fontSize: 22 }}>
        {routeIfaceForDestination(destination, physicalIface)}
      </div>
      <div className="stat-label">
        mark {mark ?? '—'} → {routeLabelForDestination(destination)}
      </div>
    </div>
  )
}

function routeIfaceForDestination(destination?: 'local' | 'vpn', physicalIface?: string) {
  if (destination === 'local') return physicalIface ?? 'eth0'
  if (destination === 'vpn') return 'awg1'
  return '—'
}

function routeTableForDestination(destination?: 'local' | 'vpn') {
  if (destination === 'local') return 'table 100'
  if (destination === 'vpn') return 'table 200'
  return 'table —'
}

function routeLabelForDestination(destination?: 'local' | 'vpn') {
  if (destination === 'local') return 'direct local route'
  if (destination === 'vpn') return 'upstream VPN'
  return 'unknown'
}

function getErrorMessage(error: unknown, fallback: string) {
  const axiosError = error as AxiosError<{ detail?: string }>
  return axiosError.response?.data?.detail || axiosError.message || fallback
}
