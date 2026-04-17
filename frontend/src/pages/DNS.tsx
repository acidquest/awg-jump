import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'

import {
  createDnsDomain,
  createDnsManualAddress,
  createDnsZone,
  deleteDnsDomain,
  deleteDnsManualAddress,
  deleteDnsZone,
  getDnsDomains,
  getDnsManualAddresses,
  getDnsStatus,
  getDnsZones,
  reloadDns,
  toggleDnsDomain,
  toggleDnsManualAddress,
  updateDnsZone,
} from '../api'
import Modal from '../components/Modal'
import { DnsDomain, DnsManualAddress, DnsStatus, DnsZone } from '../types'
import { formatDateTimeLocal } from '../utils/time'

type Notice = { type: 'success' | 'error'; message: string } | null
type ZonePayload = { name: string; dns_servers: string[]; description?: string }

const IPV4_REGEX = /^(\d{1,3}\.){3}\d{1,3}$/
const IPV6_REGEX = /^[0-9a-fA-F:]+$/
const HOSTNAME_REGEX = /^(?=.{1,253}$)(?!-)(?:[a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}\.?$/i

export default function DNS() {
  const qc = useQueryClient()
  const [addDomainOpen, setAddDomainOpen] = useState(false)
  const [addZoneOpen, setAddZoneOpen] = useState(false)
  const [editZone, setEditZone] = useState<DnsZone | null>(null)
  const [deleteZoneTarget, setDeleteZoneTarget] = useState<DnsZone | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<DnsDomain | null>(null)
  const [deleteManualAddressTarget, setDeleteManualAddressTarget] = useState<DnsManualAddress | null>(null)
  const [domainFilter, setDomainFilter] = useState('')
  const [manualAddressFilter, setManualAddressFilter] = useState('')
  const [addManualAddressOpen, setAddManualAddressOpen] = useState(false)
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

  const { data: domains = [], isLoading: domainsLoading } = useQuery<DnsDomain[]>({
    queryKey: ['dns-domains'],
    queryFn: () => getDnsDomains().then((r) => r.data),
  })

  const { data: manualAddresses = [], isLoading: manualAddressesLoading } = useQuery<DnsManualAddress[]>({
    queryKey: ['dns-manual-addresses'],
    queryFn: () => getDnsManualAddresses().then((r) => r.data),
  })

  const refreshDnsData = () => {
    qc.invalidateQueries({ queryKey: ['dns-status'] })
    qc.invalidateQueries({ queryKey: ['dns-domains'] })
    qc.invalidateQueries({ queryKey: ['dns-manual-addresses'] })
    qc.invalidateQueries({ queryKey: ['dns-zones'] })
  }

  const reloadMut = useMutation({
    mutationFn: reloadDns,
    onSuccess: () => {
      setNotice({ type: 'success', message: 'dnsmasq reloaded.' })
      refreshDnsData()
    },
    onError: () => setNotice({ type: 'error', message: 'Failed to reload dnsmasq.' }),
  })

  const updateZoneMut = useMutation({
    mutationFn: ({ zone, payload }: { zone: string; payload: ZonePayload }) => updateDnsZone(zone, payload),
    onSuccess: () => {
      setEditZone(null)
      setNotice({ type: 'success', message: 'DNS zone updated.' })
      refreshDnsData()
    },
    onError: () => setNotice({ type: 'error', message: 'Failed to update DNS zone.' }),
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

  const toggleManualAddressMut = useMutation({
    mutationFn: (id: number) => toggleDnsManualAddress(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns-manual-addresses'] }),
  })

  const deleteManualAddressMut = useMutation({
    mutationFn: (id: number) => deleteDnsManualAddress(id),
    onSuccess: () => {
      setDeleteManualAddressTarget(null)
      qc.invalidateQueries({ queryKey: ['dns-manual-addresses'] })
    },
  })

  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(null), 3500)
    return () => window.clearTimeout(timer)
  }, [notice])

  const filteredDomains = useMemo(
    () => domains.filter((d) => !domainFilter || d.domain.toLowerCase().includes(domainFilter.toLowerCase())),
    [domains, domainFilter],
  )
  const filteredManualAddresses = useMemo(
    () => manualAddresses.filter((item) => !manualAddressFilter || item.domain.toLowerCase().includes(manualAddressFilter.toLowerCase())),
    [manualAddresses, manualAddressFilter],
  )

  const disabledCount = domains.filter((d) => !d.enabled).length
  const localCount = domains.filter((d) => d.enabled && d.zone === 'local').length
  const zoneMap = new Map(zones.map((zone) => [zone.zone, zone]))
  const zoneColumnsClass = getZoneColumnsClass(zones.length)

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Split DNS</div>
          <div className="page-subtitle">Policy-based domain name resolution</div>
        </div>
        <div className="flex gap-2">
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => reloadMut.mutate()}
            disabled={reloadMut.isPending}
          >
            {reloadMut.isPending ? <span className="spinner" /> : 'Reload dnsmasq'}
          </button>
          <button className="btn btn-primary" onClick={() => setAddZoneOpen(true)}>
            + Add Zone
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div className="flex items-center gap-3">
            <div
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: status?.running ? 'var(--green)' : 'var(--red)',
                flexShrink: 0,
              }}
            />
            <div style={{ fontWeight: 600, fontSize: 14, color: status?.running ? undefined : 'var(--red)' }}>
              dnsmasq {status?.running ? 'running' : 'stopped'}
            </div>
          </div>

          <InfoChip label="Listen" value={status ? `${status.listen_ip}:53, 127.0.0.1:53` : '—'} />
          <InfoChip label="Local" value={zoneMap.get('local')?.dns_servers.join(', ') ?? '—'} accent />
          <InfoChip label="Upstream" value={zoneMap.get('vpn')?.dns_servers.join(', ') ?? '—'} />

          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>{localCount}</div>
              <div className="text-muted text-sm">local domains</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{domains.length}</div>
              <div className="text-muted text-sm">total</div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header">
          <div className="card-title">DNS Zone Settings</div>
        </div>

        {notice ? (
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
        ) : null}

        {zonesLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner" /></div>
        ) : zonesError ? (
          <div className="error-box">{getErrorMessage(zonesQueryError, 'DNS zones API is unavailable')}</div>
        ) : (
          <div className={`card-grid ${zoneColumnsClass}`}>
            {zones.map((zone) => (
              <ZoneCard
                key={zone.zone}
                zone={zone}
                onEdit={() => setEditZone(zone)}
                onDelete={() => setDeleteZoneTarget(zone)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header" style={{ alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div className="card-title">
            Domains
            {disabledCount > 0 ? (
              <span className="text-muted text-sm" style={{ marginLeft: 8, fontWeight: 400 }}>
                ({disabledCount} disabled)
              </span>
            ) : null}
          </div>
          <div className="flex gap-2" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
            <input
              className="form-input"
              placeholder="Filter domains…"
              value={domainFilter}
              onChange={(e) => setDomainFilter(e.target.value)}
              style={{ width: 220, fontSize: 13 }}
            />
            <button className="btn btn-primary btn-sm" onClick={() => setAddDomainOpen(true)}>
              + Add Domain
            </button>
          </div>
        </div>

        {domainsLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner" /></div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>Zone</th>
                  <th>Status</th>
                  <th style={{ width: 70 }} />
                </tr>
              </thead>
              <tbody>
                {filteredDomains.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>
                      {domainFilter ? 'No domains match the filter.' : 'No domains configured.'}
                    </td>
                  </tr>
                ) : filteredDomains.map((domain) => (
                  <DomainRow
                    key={domain.id}
                    domain={domain}
                    zone={zoneMap.get(domain.zone)}
                    onToggle={() => toggleMut.mutate(domain.id)}
                    onDelete={() => setDeleteTarget(domain)}
                    togglePending={toggleMut.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-header" style={{ alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div className="card-title">Manual Replace Addresses</div>
          <div className="flex gap-2" style={{ marginLeft: 'auto', flexWrap: 'wrap' }}>
            <input
              className="form-input"
              placeholder="Filter domains…"
              value={manualAddressFilter}
              onChange={(e) => setManualAddressFilter(e.target.value)}
              style={{ width: 220, fontSize: 13 }}
            />
            <button className="btn btn-primary btn-sm" onClick={() => setAddManualAddressOpen(true)}>
              + Add
            </button>
          </div>
        </div>

        {manualAddressesLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner" /></div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>Address</th>
                  <th>Status</th>
                  <th style={{ width: 70 }} />
                </tr>
              </thead>
              <tbody>
                {filteredManualAddresses.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>
                      {manualAddressFilter ? 'No manual addresses match the filter.' : 'No manual replace addresses configured.'}
                    </td>
                  </tr>
                ) : filteredManualAddresses.map((item) => (
                  <ManualAddressRow
                    key={item.id}
                    item={item}
                    onToggle={() => toggleManualAddressMut.mutate(item.id)}
                    onDelete={() => setDeleteManualAddressTarget(item)}
                    togglePending={toggleManualAddressMut.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {addDomainOpen ? (
        <AddDomainModal
          zones={zones}
          onClose={() => setAddDomainOpen(false)}
          onSaved={() => {
            setAddDomainOpen(false)
            qc.invalidateQueries({ queryKey: ['dns-domains'] })
          }}
        />
      ) : null}

      {addZoneOpen ? (
        <AddZoneModal
          onClose={() => setAddZoneOpen(false)}
          onSaved={() => {
            setAddZoneOpen(false)
            refreshDnsData()
          }}
        />
      ) : null}

      {addManualAddressOpen ? (
        <AddManualAddressModal
          onClose={() => setAddManualAddressOpen(false)}
          onSaved={() => {
            setAddManualAddressOpen(false)
            qc.invalidateQueries({ queryKey: ['dns-manual-addresses'] })
          }}
        />
      ) : null}

      {editZone ? (
        <EditZoneModal
          zone={editZone}
          saving={updateZoneMut.isPending}
          onClose={() => setEditZone(null)}
          onSave={(payload) => updateZoneMut.mutate({ zone: editZone.zone, payload })}
        />
      ) : null}

      {deleteZoneTarget ? (
        <Modal open title="Delete zone" onClose={() => setDeleteZoneTarget(null)}>
          <div style={{ marginBottom: 16, fontSize: 14 }}>
            Delete zone <span className="text-mono">{deleteZoneTarget.name}</span> and all domains attached to it?
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setDeleteZoneTarget(null)}>Cancel</button>
            <button
              className="btn btn-danger"
              onClick={async () => {
                await deleteDnsZone(deleteZoneTarget.zone)
                setDeleteZoneTarget(null)
                refreshDnsData()
              }}
            >
              Delete
            </button>
          </div>
        </Modal>
      ) : null}

      {deleteTarget ? (
        <Modal open title="Delete domain" onClose={() => setDeleteTarget(null)}>
          <div style={{ marginBottom: 16, fontSize: 14 }}>
            Delete <span className="text-mono">{deleteTarget.domain}</span> from split DNS?
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>Cancel</button>
            <button
              className="btn btn-danger"
              onClick={() => deleteMut.mutate(deleteTarget.id)}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? <span className="spinner" /> : 'Delete'}
            </button>
          </div>
        </Modal>
      ) : null}

      {deleteManualAddressTarget ? (
        <Modal open title="Delete manual replace address" onClose={() => setDeleteManualAddressTarget(null)}>
          <div style={{ marginBottom: 16, fontSize: 14 }}>
            Delete manual replace address for <span className="text-mono">{deleteManualAddressTarget.domain}</span>?
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setDeleteManualAddressTarget(null)}>Cancel</button>
            <button
              className="btn btn-danger"
              onClick={() => deleteManualAddressMut.mutate(deleteManualAddressTarget.id)}
              disabled={deleteManualAddressMut.isPending}
            >
              {deleteManualAddressMut.isPending ? <span className="spinner" /> : 'Delete'}
            </button>
          </div>
        </Modal>
      ) : null}
    </>
  )
}

function ZoneCard({
  zone,
  onEdit,
  onDelete,
}: {
  zone: DnsZone
  onEdit: () => void
  onDelete: () => void
}) {
  const isLocal = zone.zone === 'local'
  const isUpstream = zone.zone === 'vpn'
  const canDelete = !zone.is_builtin && !isLocal && !isUpstream
  const accent = isLocal ? 'var(--accent)' : isUpstream ? '#60a5fa' : '#a78bfa'
  const bg = isLocal ? 'var(--accent-dim)' : isUpstream ? 'rgba(59,130,246,0.15)' : 'rgba(167,139,250,0.14)'

  return (
    <div
      style={{
        background: 'var(--bg-3)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: 14,
        minHeight: 136,
      }}
    >
      <div className="flex items-center justify-between" style={{ marginBottom: 10, gap: 10 }}>
        <div className="flex items-center gap-3">
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 10,
              display: 'grid',
              placeItems: 'center',
              background: bg,
              color: accent,
              flexShrink: 0,
            }}
          >
            {isLocal ? <LocalIcon /> : <ShieldIcon />}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.2 }}>{zone.name}</div>
            <div className="text-muted text-sm">{zone.zone}</div>
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={onEdit}>Edit</button>
          {canDelete ? (
            <button className="btn btn-danger btn-sm" onClick={onDelete}>Delete</button>
          ) : null}
        </div>
      </div>
      <div className="text-mono" style={{ fontSize: 13, wordBreak: 'break-word' }}>
        {zone.dns_servers.join(', ')}
      </div>
      <div className="text-muted text-sm" style={{ marginTop: 8 }}>
        Updated {formatTimestamp(zone.updated_at)}
      </div>
    </div>
  )
}

function DomainRow({
  domain,
  zone,
  onToggle,
  onDelete,
  togglePending,
}: {
  domain: DnsDomain
  zone?: DnsZone
  onToggle: () => void
  onDelete: () => void
  togglePending: boolean
}) {
  return (
    <tr>
      <td className="text-mono" style={{ opacity: domain.enabled ? 1 : 0.45 }}>{domain.domain}</td>
      <td><ZoneBadge zone={zone} zoneKey={domain.zone} /></td>
      <td>
        <label className="toggle" title={domain.enabled ? 'Disable' : 'Enable'}>
          <input
            type="checkbox"
            checked={domain.enabled}
            onChange={onToggle}
            disabled={togglePending}
          />
          <span className="toggle-slider" />
        </label>
      </td>
      <td>
        <button
          className="btn btn-ghost btn-icon"
          title="Delete"
          onClick={onDelete}
          style={{ color: 'var(--red)' }}
        >
          <TrashIcon />
        </button>
      </td>
    </tr>
  )
}

function ManualAddressRow({
  item,
  onToggle,
  onDelete,
  togglePending,
}: {
  item: DnsManualAddress
  onToggle: () => void
  onDelete: () => void
  togglePending: boolean
}) {
  return (
    <tr>
      <td className="text-mono" style={{ opacity: item.enabled ? 1 : 0.45 }}>{item.domain}</td>
      <td className="text-mono" style={{ opacity: item.enabled ? 1 : 0.45 }}>{item.address}</td>
      <td>
        <label className="toggle" title={item.enabled ? 'Disable' : 'Enable'}>
          <input type="checkbox" checked={item.enabled} onChange={onToggle} disabled={togglePending} />
          <span className="toggle-slider" />
        </label>
      </td>
      <td>
        <button className="btn btn-ghost btn-icon" title="Delete" onClick={onDelete} style={{ color: 'var(--red)' }}>
          <TrashIcon />
        </button>
      </td>
    </tr>
  )
}

function ZoneBadge({ zone, zoneKey }: { zone?: DnsZone; zoneKey: string }) {
  const isLocal = zoneKey === 'local'
  const isUpstream = zoneKey === 'vpn'

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: isLocal ? 'var(--accent-dim)' : isUpstream ? 'rgba(59,130,246,0.15)' : 'rgba(167,139,250,0.14)',
        color: isLocal ? 'var(--accent)' : isUpstream ? '#93c5fd' : '#c4b5fd',
        borderRadius: 999,
        padding: '4px 10px',
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {isLocal ? <LocalIcon small /> : <ShieldIcon small />}
      {zone?.name ?? zoneKey}
    </span>
  )
}

function AddDomainModal({
  zones,
  onClose,
  onSaved,
}: {
  zones: DnsZone[]
  onClose: () => void
  onSaved: () => void
}) {
  const [domain, setDomain] = useState('')
  const selectableZones = zones.filter((zone) => !zone.is_builtin)
  const [zone, setZone] = useState(selectableZones.length ? selectableZones[0].zone : 'local')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selectableZones.length) {
      setZone('local')
      return
    }
    if (!selectableZones.some((item) => item.zone === zone)) {
      setZone(selectableZones[0].zone)
    }
  }, [selectableZones, zone])

  const mut = useMutation({
    mutationFn: () => createDnsDomain({ domain, zone: selectableZones.length ? zone : 'local', enabled: true }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  return (
    <Modal open title="Add domain" onClose={onClose}>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="form-group">
        <label className="form-label">Domain / TLD</label>
        <input
          className="form-input mono"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="example.com"
          autoFocus
          onKeyDown={(e) => { if (e.key === 'Enter') mut.mutate() }}
        />
      </div>
      <div className="form-group">
        <label className="form-label">Zone</label>
        <select
          className="form-input"
          value={selectableZones.length ? zone : 'local'}
          onChange={(e) => setZone(e.target.value)}
          disabled={!selectableZones.length}
        >
          {selectableZones.length ? selectableZones.map((item) => (
            <option key={item.zone} value={item.zone}>{item.name}</option>
          )) : (
            <option value="local">Local</option>
          )}
        </select>
        {!selectableZones.length ? (
          <div className="text-muted text-sm" style={{ marginTop: 4 }}>
            Only Local and Upstream zones exist. New domains are added to Local.
          </div>
        ) : null}
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

function AddManualAddressModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [domain, setDomain] = useState('')
  const [address, setAddress] = useState('')
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => createDnsManualAddress({ domain, address, enabled: true }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  return (
    <Modal open title="Add manual replace address" onClose={onClose}>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="form-group">
        <label className="form-label">Domain</label>
        <input
          className="form-input mono"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="example.com"
          autoFocus
        />
        <div className="text-muted text-sm" style={{ marginTop: 4 }}>
          Use a dnsmasq-style domain target without wildcards, for example <span className="text-mono">example.com</span>, <span className="text-mono">sub.example.com</span> or <span className="text-mono">com</span>.
        </div>
      </div>
      <div className="form-group">
        <label className="form-label">Address</label>
        <input
          className="form-input mono"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="192.168.1.100"
        />
        <div className="text-muted text-sm" style={{ marginTop: 4 }}>
          IPv4 or IPv6 address. dnsmasq will generate <span className="text-mono">address=/domain/ip</span>.
        </div>
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button
          className="btn btn-primary"
          onClick={() => mut.mutate()}
          disabled={mut.isPending || !domain.trim() || !address.trim()}
        >
          {mut.isPending ? <span className="spinner" /> : 'Add'}
        </button>
      </div>
    </Modal>
  )
}

function AddZoneModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState('')
  const [server, setServer] = useState('')
  const [domains, setDomains] = useState('')
  const [error, setError] = useState('')

  const mut = useMutation({
    mutationFn: () => createDnsZone({
      name,
      dns_servers: [server],
      domains: splitItems(domains),
    }),
    onSuccess: onSaved,
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Error'
      setError(msg)
    },
  })

  return (
    <Modal open title="Add zone" onClose={onClose}>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="form-group">
        <label className="form-label">Zone name</label>
        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Gemini" autoFocus />
      </div>
      <div className="form-group">
        <label className="form-label">Zone DNS server</label>
        <input className="form-input mono" value={server} onChange={(e) => setServer(e.target.value)} placeholder="1.2.3.4 or dns.example.com" />
      </div>
      <div className="form-group">
        <label className="form-label">Domain names</label>
        <textarea
          className="form-input mono"
          rows={8}
          value={domains}
          onChange={(e) => setDomains(e.target.value)}
          placeholder={'gemini.com\napi.gemini.com'}
        />
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button
          className="btn btn-primary"
          onClick={() => {
            if (!isValidDnsServer(server)) {
              setError('Enter a valid DNS server IP or hostname.')
              return
            }
            mut.mutate()
          }}
          disabled={mut.isPending || !name.trim() || !server.trim()}
        >
          {mut.isPending ? <span className="spinner" /> : 'Add Zone'}
        </button>
      </div>
    </Modal>
  )
}

function EditZoneModal({
  zone,
  saving,
  onClose,
  onSave,
}: {
  zone: DnsZone
  saving: boolean
  onClose: () => void
  onSave: (payload: ZonePayload) => void
}) {
  const [name, setName] = useState(zone.name)
  const [servers, setServers] = useState(zone.dns_servers.join('\n'))
  const [error, setError] = useState('')

  return (
    <Modal open title="Edit zone" onClose={onClose}>
      {error ? <div className="error-box">{error}</div> : null}
      <div className="form-group">
        <label className="form-label">Zone name</label>
        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </div>
      <div className="form-group">
        <label className="form-label">DNS servers</label>
        <textarea
          className="form-input mono"
          rows={4}
          value={servers}
          onChange={(e) => setServers(e.target.value)}
          placeholder={'77.88.8.8\n1.1.1.1'}
        />
      </div>
      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
        <button
          className="btn btn-primary"
          onClick={() => {
            const dnsServers = splitItems(servers)
            if (!name.trim()) {
              setError('Zone name is required.')
              return
            }
            if (!dnsServers.length) {
              setError('At least one DNS server is required.')
              return
            }
            if (!dnsServers.every(isValidDnsServer)) {
              setError('Enter valid DNS server IPs or hostnames.')
              return
            }
            onSave({ name, dns_servers: dnsServers, description: zone.description })
          }}
          disabled={saving}
        >
          {saving ? <span className="spinner" /> : 'Save'}
        </button>
      </div>
    </Modal>
  )
}

function InfoChip({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ fontSize: 13 }}>
      <div className="text-muted text-sm">{label}</div>
      <div className="text-mono" style={{ color: accent ? 'var(--accent)' : undefined, fontWeight: accent ? 600 : undefined }}>
        {value}
      </div>
    </div>
  )
}

function splitItems(value: string) {
  return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean)
}

function getZoneColumnsClass(count: number) {
  if (count >= 3 && count % 3 === 0) return 'card-grid-3'
  if (count >= 2 && count % 2 === 0) return 'card-grid-2'
  return 'card-grid-3'
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

function isValidDnsServer(value: string) {
  const trimmed = value.trim()
  return isValidIp(trimmed) || HOSTNAME_REGEX.test(trimmed)
}

function getErrorMessage(error: unknown, fallback: string) {
  const axiosError = error as AxiosError<{ detail?: string | string[] }>
  const detail = axiosError.response?.data?.detail
  if (Array.isArray(detail)) return detail.map((item) => String(item)).join(', ')
  if (typeof detail === 'string') return detail
  return fallback
}

function formatTimestamp(value: string) {
  const formatted = formatDateTimeLocal(value)
  return formatted === '—' ? 'recently' : formatted
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
