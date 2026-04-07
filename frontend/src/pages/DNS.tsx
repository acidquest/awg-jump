import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'

import {
  createDnsDomain,
  deleteDnsDomain,
  getDnsDomains,
  getDnsStatus,
  getDnsZones,
  reloadDns,
  toggleDnsDomain,
  updateDnsZone,
} from '../api'
import Modal from '../components/Modal'
import { DnsDomain, DnsStatus, DnsZone } from '../types'

type ZoneKey = 'local' | 'vpn'
type Notice = { type: 'success' | 'error'; message: string } | null
type ZoneUpdatePayload = { dns_servers: string[]; description?: string }

const IPV4_REGEX = /^(\d{1,3}\.){3}\d{1,3}$/
const IPV6_REGEX = /^[0-9a-fA-F:]+$/

const ZONE_META: Record<ZoneKey, {
  title: string
  description: string
  icon: 'local' | 'vpn'
  accent: string
}> = {
  local: {
    title: 'Local Zone DNS',
    description: 'Used for domains routed through physical interface',
    icon: 'local',
    accent: 'var(--accent)',
  },
  vpn: {
    title: 'VPN Zone DNS',
    description: 'Used for all other traffic through VPN',
    icon: 'vpn',
    accent: '#60a5fa',
  },
}

export default function DNS() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DnsDomain | null>(null)
  const [filter, setFilter] = useState('')
  const [notice, setNotice] = useState<Notice>(null)

  const { data: status } = useQuery<DnsStatus>({
    queryKey: ['dns-status'],
    queryFn: () => getDnsStatus().then((r) => r.data),
    refetchInterval: 15_000,
  })

  const {
    data: zones = [],
    isLoading: zonesLoading,
    isError: zonesError,
    error: zonesQueryError,
  } = useQuery<DnsZone[]>({
    queryKey: ['dns-zones'],
    queryFn: () => getDnsZones().then((r) => r.data),
  })

  const { data: domains = [], isLoading } = useQuery<DnsDomain[]>({
    queryKey: ['dns-domains'],
    queryFn: () => getDnsDomains().then((r) => r.data),
  })

  const refreshDnsData = () => {
    qc.invalidateQueries({ queryKey: ['dns-status'] })
    qc.invalidateQueries({ queryKey: ['dns-domains'] })
    qc.invalidateQueries({ queryKey: ['dns-zones'] })
  }

  const reloadMut = useMutation({
    mutationFn: reloadDns,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns-status'] }),
  })

  const updateZoneMut = useMutation({
    mutationFn: ({ zone, payload }: { zone: ZoneKey; payload: ZoneUpdatePayload }) =>
      updateDnsZone(zone, payload),
    onSuccess: () => {
      setNotice({ type: 'success', message: 'DNS settings updated, dnsmasq reloading...' })
      refreshDnsData()
    },
    onError: () => {
      setNotice({ type: 'error', message: 'Failed to update DNS settings.' })
    },
  })

  const toggleMut = useMutation({
    mutationFn: (id: number) => toggleDnsDomain(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns-domains'] }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteDnsDomain(id),
    onSuccess: () => {
      setDeleteTarget(null)
      qc.invalidateQueries({ queryKey: ['dns-domains'] })
    },
  })

  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(null), 3500)
    return () => window.clearTimeout(timer)
  }, [notice])

  const filtered = domains.filter((d) =>
    !filter || d.domain.toLowerCase().includes(filter.toLowerCase())
  )

  const localCount = domains.filter((d) => d.enabled && d.upstream === 'yandex').length
  const disabledCount = domains.filter((d) => !d.enabled).length
  const localZone = zones.find((zone) => zone.zone === 'local')
  const vpnZone = zones.find((zone) => zone.zone === 'vpn')

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Split DNS</div>
          <div className="page-subtitle">dnsmasq — политика разрешения доменных имён</div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => reloadMut.mutate()}
            disabled={reloadMut.isPending}
          >
            {reloadMut.isPending ? <span className="spinner" /> : 'Reload dnsmasq'}
          </button>
          <button className="btn btn-primary" onClick={() => setAddOpen(true)}>
            + Add domain
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div className="flex items-center gap-3">
            <div style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: status?.running ? 'var(--green)' : 'var(--red)',
              flexShrink: 0,
            }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>
                dnsmasq {status?.running ? 'running' : 'stopped'}
              </div>
              {status?.pid && (
                <div className="text-muted text-sm">pid {status.pid}</div>
              )}
            </div>
          </div>

          <InfoChip label="Listen" value={status ? `${status.listen_ip}:53, 127.0.0.1:53` : '—'} />
          <InfoChip label="Local Zone" value={status?.local_zone_dns?.join(', ') ?? '—'} accent />
          <InfoChip label="VPN Zone" value={status?.vpn_zone_dns?.join(', ') ?? '—'} />

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>{localCount}</div>
              <div className="text-muted text-sm">local zone domains</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{domains.length}</div>
              <div className="text-muted text-sm">total</div>
            </div>
          </div>
        </div>

        <div style={{
          marginTop: 14,
          paddingTop: 14,
          borderTop: '1px solid var(--border)',
          fontSize: 12,
          color: 'var(--text-2)',
          lineHeight: 1.7,
        }}>
          Клиенты AWG используют <span className="text-mono">{status?.listen_ip ?? '...'}</span> как DNS.
          Домены из local zone резолвятся через <span className="text-mono">{status?.local_zone_dns?.join(', ') ?? '77.88.8.8'}</span>,
          остальные — через <span className="text-mono">{status?.vpn_zone_dns?.join(', ') ?? '1.1.1.1, 8.8.8.8'}</span>.
          Трафик DNS-сервера маршрутизируется по GeoIP так же, как клиентский:
          RU IP → eth0, остальное → awg1.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <div className="card-title">DNS Zone Settings</div>
        </div>

        {notice && (
          <div
            style={{
              marginBottom: 14,
              padding: '10px 12px',
              borderRadius: 6,
              border: `1px solid ${notice.type === 'success' ? 'var(--accent)' : 'var(--danger)'}`,
              background: notice.type === 'success' ? 'var(--accent-dim)' : 'var(--danger-dim)',
              color: notice.type === 'success' ? 'var(--accent)' : 'var(--danger)',
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            {notice.message}
          </div>
        )}

        <div className="card-grid card-grid-2">
          <ZoneCard
            zone="local"
            data={localZone}
            isLoading={zonesLoading}
            errorMessage={zonesError ? getErrorMessage(zonesQueryError, 'DNS zones API is unavailable') : ''}
            isSaving={updateZoneMut.isPending && updateZoneMut.variables?.zone === 'local'}
            onSave={(payload) => updateZoneMut.mutate({ zone: 'local', payload })}
          />
          <ZoneCard
            zone="vpn"
            data={vpnZone}
            isLoading={zonesLoading}
            errorMessage={zonesError ? getErrorMessage(zonesQueryError, 'DNS zones API is unavailable') : ''}
            isSaving={updateZoneMut.isPending && updateZoneMut.variables?.zone === 'vpn'}
            onSave={(payload) => updateZoneMut.mutate({ zone: 'vpn', payload })}
          />
        </div>
      </div>

      <div className="card">
        <div className="flex items-center justify-between" style={{ marginBottom: 14 }}>
          <div className="card-title">
            Домены
            {disabledCount > 0 && (
              <span className="text-muted text-sm" style={{ marginLeft: 8, fontWeight: 400 }}>
                ({disabledCount} disabled)
              </span>
            )}
          </div>
          <input
            className="form-input"
            placeholder="Filter domains…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ width: 200, fontSize: 13 }}
          />
        </div>

        {isLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner" /></div>
        ) : filtered.length === 0 ? (
          <div className="text-muted" style={{ textAlign: 'center', padding: '32px 0', fontSize: 14 }}>
            {filter ? 'No domains match the filter.' : 'No domains configured.'}
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: '8px 12px', alignItems: 'center' }}>
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Domain</div>
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Upstream</div>
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Status</div>
            <div style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }} />

            {filtered.map((d) => (
              <DomainRow
                key={d.id}
                domain={d}
                onToggle={() => toggleMut.mutate(d.id)}
                onDelete={() => setDeleteTarget(d)}
                togglePending={toggleMut.isPending}
              />
            ))}
          </div>
        )}
      </div>

      {addOpen && (
        <AddDomainModal
          onClose={() => setAddOpen(false)}
          onSaved={() => {
            setAddOpen(false)
            qc.invalidateQueries({ queryKey: ['dns-domains'] })
          }}
        />
      )}

      {deleteTarget && (
        <Modal open title="Удалить домен" onClose={() => setDeleteTarget(null)}>
          <div style={{ marginBottom: 16, fontSize: 14 }}>
            Удалить <span className="text-mono">{deleteTarget.domain}</span> из таблицы split DNS?
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>Отмена</button>
            <button
              className="btn btn-danger"
              onClick={() => deleteMut.mutate(deleteTarget.id)}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? <span className="spinner" /> : 'Удалить'}
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}

function ZoneCard({
  zone,
  data,
  isLoading,
  errorMessage,
  isSaving,
  onSave,
}: {
  zone: ZoneKey
  data?: DnsZone
  isLoading: boolean
  errorMessage: string
  isSaving: boolean
  onSave: (payload: ZoneUpdatePayload) => void
}) {
  const [isEditing, setIsEditing] = useState(false)
  const [draftServers, setDraftServers] = useState<string[]>([])
  const [newServer, setNewServer] = useState('')
  const [inputError, setInputError] = useState('')

  useEffect(() => {
    if (!data || isEditing) return
    setDraftServers(data.dns_servers)
  }, [data, isEditing])

  const startEdit = () => {
    setDraftServers(data?.dns_servers ?? [])
    setNewServer('')
    setInputError('')
    setIsEditing(true)
  }

  const cancelEdit = () => {
    setDraftServers(data?.dns_servers ?? [])
    setNewServer('')
    setInputError('')
    setIsEditing(false)
  }

  const addServer = () => {
    const candidate = newServer.trim()
    if (!candidate) {
      setInputError('Enter a DNS server IP address.')
      return
    }
    if (!isValidIp(candidate)) {
      setInputError('Enter a valid IPv4 or IPv6 address.')
      return
    }
    if (draftServers.includes(candidate)) {
      setInputError('This DNS server is already in the list.')
      return
    }
    if (draftServers.length >= 3) {
      setInputError('Maximum 3 DNS servers per zone.')
      return
    }
    setDraftServers((current) => [...current, candidate])
    setNewServer('')
    setInputError('')
  }

  const removeServer = (server: string) => {
    setDraftServers((current) => current.filter((item) => item !== server))
    setInputError('')
  }

  const save = () => {
    if (draftServers.length === 0) {
      setInputError('Add at least one DNS server.')
      return
    }

    onSave({
      dns_servers: draftServers,
      description: data?.description || ZONE_META[zone].description,
    })
  }

  useEffect(() => {
    if (!isSaving) return
    setInputError('')
  }, [isSaving])

  const zoneError =
    !isSaving && data === undefined && !isLoading ? errorMessage || 'DNS zone settings are unavailable.' : ''

  return (
    <div
      style={{
        background: 'var(--bg-3)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 16,
        minHeight: 208,
      }}
    >
      <div className="flex items-center justify-between" style={{ marginBottom: 12, gap: 10 }}>
        <div className="flex items-center gap-3">
          <ZoneIcon type={ZONE_META[zone].icon} color={ZONE_META[zone].accent} />
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{ZONE_META[zone].title}</div>
            <div className="text-muted text-sm">{ZONE_META[zone].description}</div>
          </div>
        </div>
        {!isEditing && !zoneError && (
          <button className="btn btn-secondary btn-sm" onClick={startEdit} disabled={isLoading || !data}>
            Edit
          </button>
        )}
      </div>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}><span className="spinner" /></div>
      ) : zoneError ? (
        <div className="error-box" style={{ marginTop: 8 }}>{zoneError}</div>
      ) : !data ? (
        <div className="text-muted">Zone settings not found.</div>
      ) : isEditing ? (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
            {draftServers.map((server) => (
              <span
                key={server}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 10px',
                  borderRadius: 999,
                  background: 'var(--bg-4)',
                  border: '1px solid var(--border)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                }}
              >
                {server}
                <button
                  className="btn btn-ghost btn-icon"
                  onClick={() => removeServer(server)}
                  title={`Remove ${server}`}
                  style={{ padding: 2, minWidth: 20, height: 20 }}
                >
                  <CloseIcon />
                </button>
              </span>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'start' }}>
            <input
              className="form-input mono"
              value={newServer}
              onChange={(e) => setNewServer(e.target.value)}
              placeholder={zone === 'local' ? '77.88.8.8' : '1.1.1.1'}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addServer()
                }
              }}
            />
            <button className="btn btn-secondary" onClick={addServer} disabled={draftServers.length >= 3}>
              Add
            </button>
          </div>

          {inputError && (
            <div className="text-danger text-sm" style={{ marginTop: 8 }}>
              {inputError}
            </div>
          )}

          <div className="text-muted text-sm" style={{ marginTop: 8 }}>
            Up to 3 DNS servers. IPv4 or IPv6 addresses only.
          </div>

          <div className="flex gap-2" style={{ marginTop: 14 }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={isSaving}>
              {isSaving ? <span className="spinner" /> : 'Save'}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={cancelEdit} disabled={isSaving}>
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
            {data.dns_servers.map((server) => (
              <span
                key={server}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  padding: '6px 10px',
                  borderRadius: 999,
                  background: zone === 'local' ? 'var(--accent-dim)' : 'rgba(59,130,246,0.15)',
                  color: zone === 'local' ? 'var(--accent)' : '#93c5fd',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                {server}
              </span>
            ))}
          </div>
          <div className="text-muted text-sm">
            Updated {formatTimestamp(data.updated_at)}
          </div>
        </>
      )}
    </div>
  )
}

function DomainRow({
  domain: d, onToggle, onDelete, togglePending,
}: {
  domain: DnsDomain
  onToggle: () => void
  onDelete: () => void
  togglePending: boolean
}) {
  return (
    <>
      <div
        className="text-mono"
        style={{ fontSize: 13, opacity: d.enabled ? 1 : 0.45, userSelect: 'text' }}
      >
        {d.domain}
      </div>
      <div><UpstreamBadge upstream={d.upstream} /></div>
      <div>
        <label className="toggle" title={d.enabled ? 'Disable' : 'Enable'}>
          <input
            type="checkbox"
            checked={d.enabled}
            onChange={onToggle}
            disabled={togglePending}
          />
          <span className="toggle-slider" />
        </label>
      </div>
      <div>
        <button
          className="btn btn-ghost btn-icon"
          title="Delete"
          onClick={onDelete}
          style={{ color: 'var(--red)' }}
        >
          <TrashIcon />
        </button>
      </div>
    </>
  )
}

function InfoChip({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ fontSize: 13 }}>
      <div className="text-muted text-sm">{label}</div>
      <div
        className="text-mono"
        style={{ color: accent ? 'var(--accent)' : undefined, fontWeight: accent ? 600 : undefined }}
      >
        {value}
      </div>
    </div>
  )
}

function UpstreamBadge({ upstream }: { upstream: string }) {
  const isLocal = upstream === 'yandex'

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: isLocal ? 'var(--accent-dim)' : 'rgba(59,130,246,0.15)',
        color: isLocal ? 'var(--accent)' : '#93c5fd',
        borderRadius: 999,
        padding: '4px 10px',
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {isLocal ? <LocalIcon small /> : <ShieldIcon small />}
      {isLocal ? 'Local Zone' : 'VPN Zone'}
    </span>
  )
}

function AddDomainModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [domain, setDomain] = useState('')
  const [upstream, setUpstream] = useState<'yandex' | 'default'>('yandex')
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => createDnsDomain({ domain, upstream, enabled: true }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  return (
    <Modal open title="Add DNS domain" onClose={onClose}>
      {error && <div className="error-box">{error}</div>}
      <div className="form-group">
        <label className="form-label">Domain / TLD</label>
        <input
          className="form-input mono"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="example.ru"
          autoFocus
          onKeyDown={(e) => { if (e.key === 'Enter') mut.mutate() }}
        />
        <div className="text-muted text-sm" style={{ marginTop: 4 }}>
          Можно указать TLD (<span className="text-mono">ru</span>), домен (
          <span className="text-mono">example.ru</span>) или поддомен (
          <span className="text-mono">sub.example.ru</span>)
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Upstream DNS</label>
        <div style={{ display: 'flex', gap: 10 }}>
          {(['yandex', 'default'] as const).map((u) => (
            <label
              key={u}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: 'pointer',
                fontSize: 13,
                padding: '8px 14px',
                border: `1px solid ${upstream === u ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 6,
                background: upstream === u ? 'var(--accent-dim)' : 'var(--bg-3)',
                transition: 'all 0.15s',
              }}
            >
              <input
                type="radio"
                name="upstream"
                value={u}
                checked={upstream === u}
                onChange={() => setUpstream(u)}
                style={{ accentColor: 'var(--accent)' }}
              />
              <div>
                <div style={{ fontWeight: 500 }}>
                  {u === 'yandex' ? 'Local Zone DNS' : 'VPN Zone DNS'}
                </div>
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {u === 'yandex'
                    ? 'Domains through physical interface'
                    : 'All other traffic through VPN'}
                </div>
              </div>
            </label>
          ))}
        </div>
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button
          className="btn btn-primary"
          onClick={() => mut.mutate()}
          disabled={mut.isPending || !domain.trim()}
        >
          {mut.isPending ? <span className="spinner" /> : 'Add'}
        </button>
      </div>
    </Modal>
  )
}

function isValidIp(value: string) {
  if (IPV4_REGEX.test(value)) {
    return value.split('.').every((part) => {
      const num = Number(part)
      return Number.isInteger(num) && num >= 0 && num <= 255
    })
  }

  return value.includes(':') && IPV6_REGEX.test(value)
}

function getErrorMessage(error: unknown, fallback: string) {
  const axiosError = error as AxiosError<{ detail?: string | string[] }>
  const detail = axiosError.response?.data?.detail
  if (Array.isArray(detail)) {
    return detail.map((item) => String(item)).join(', ')
  }
  if (typeof detail === 'string') {
    return detail
  }
  return fallback
}

function formatTimestamp(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'recently'
  return date.toLocaleString()
}

function ZoneIcon({ type, color }: { type: 'local' | 'vpn'; color: string }) {
  return (
    <div
      style={{
        width: 36,
        height: 36,
        borderRadius: 10,
        display: 'grid',
        placeItems: 'center',
        background: type === 'local' ? 'var(--accent-dim)' : 'rgba(59,130,246,0.15)',
        color,
        flexShrink: 0,
      }}
    >
      {type === 'local' ? <LocalIcon /> : <ShieldIcon />}
    </div>
  )
}

function LocalIcon({ small = false }: { small?: boolean }) {
  return (
    <svg width={small ? 12 : 16} height={small ? 12 : 16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 11.5L12 4l9 7.5" />
      <path d="M5 10.5V20h14v-9.5" />
      <path d="M9 20v-6h6v6" />
    </svg>
  )
}

function ShieldIcon({ small = false }: { small?: boolean }) {
  return (
    <svg width={small ? 12 : 16} height={small ? 12 : 16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l7 3v6c0 5-3.5 8-7 9-3.5-1-7-4-7-9V6l7-3z" />
      <path d="M9.5 12.5l1.8 1.8 3.2-4.3" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6L6 18" />
      <path d="M6 6l12 12" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4h6v2" />
    </svg>
  )
}
