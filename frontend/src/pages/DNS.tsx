import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getDnsStatus,
  getDnsDomains,
  createDnsDomain,
  deleteDnsDomain,
  toggleDnsDomain,
  reloadDns,
} from '../api'
import { DnsDomain, DnsStatus } from '../types'
import Modal from '../components/Modal'

export default function DNS() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DnsDomain | null>(null)
  const [filter, setFilter] = useState('')

  const { data: status } = useQuery<DnsStatus>({
    queryKey: ['dns-status'],
    queryFn: () => getDnsStatus().then((r) => r.data),
    refetchInterval: 15_000,
  })

  const { data: domains = [], isLoading } = useQuery<DnsDomain[]>({
    queryKey: ['dns-domains'],
    queryFn: () => getDnsDomains().then((r) => r.data),
  })

  const reloadMut = useMutation({
    mutationFn: reloadDns,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns-status'] }),
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

  const filtered = domains.filter((d) =>
    !filter || d.domain.toLowerCase().includes(filter.toLowerCase())
  )

  const yandexCount = domains.filter((d) => d.enabled && d.upstream === 'yandex').length
  const disabledCount = domains.filter((d) => !d.enabled).length

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

      {/* Status card */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          {/* dnsmasq status */}
          <div className="flex items-center gap-3">
            <div style={{
              width: 10, height: 10, borderRadius: '50%',
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

          {/* Listen address */}
          <InfoChip label="Listen" value={status ? `${status.listen_ip}:53, 127.0.0.1:53` : '—'} />

          {/* Upstreams */}
          <InfoChip label="RU DNS" value={status?.yandex_dns ?? '—'} accent />
          <InfoChip label="Default DNS" value={status?.default_dns?.join(', ') ?? '—'} />

          {/* Domain stats */}
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>{yandexCount}</div>
              <div className="text-muted text-sm">RU domains</div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{domains.length}</div>
              <div className="text-muted text-sm">total</div>
            </div>
          </div>
        </div>

        {/* How it works */}
        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)',
          fontSize: 12, color: 'var(--text-2)', lineHeight: 1.7,
        }}>
          Клиенты AWG используют <span className="text-mono">{status?.listen_ip ?? '...'}</span> как DNS.
          Домены из списка (upstream=Яндекс) резолвятся через <span className="text-mono">{status?.yandex_dns ?? '77.88.8.8'}</span>, остальные — через{' '}
          <span className="text-mono">{status?.default_dns?.join(', ') ?? '1.1.1.1, 8.8.8.8'}</span>.
          Трафик DNS-сервера маршрутизируется по GeoIP так же, как клиентский:
          RU IP → eth0, остальное → awg1.
        </div>
      </div>

      {/* Domain list */}
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
            {/* Header */}
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Domain</div>
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Upstream</div>
            <div className="text-muted text-sm" style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Status</div>
            <div style={{ paddingBottom: 4, borderBottom: '1px solid var(--border)' }} />

            {/* Rows */}
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

      {/* Add domain modal */}
      {addOpen && (
        <AddDomainModal
          onClose={() => setAddOpen(false)}
          onSaved={() => {
            setAddOpen(false)
            qc.invalidateQueries({ queryKey: ['dns-domains'] })
          }}
        />
      )}

      {/* Delete confirm modal */}
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
  if (upstream === 'yandex') {
    return (
      <span style={{
        background: 'rgba(59,130,246,0.15)', color: '#60a5fa',
        borderRadius: 4, padding: '2px 8px', fontSize: 12, fontWeight: 500,
      }}>
        Yandex 77.88.8.8
      </span>
    )
  }
  return (
    <span style={{
      background: 'var(--bg-3)', color: 'var(--text-2)',
      borderRadius: 4, padding: '2px 8px', fontSize: 12,
    }}>
      Default
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
                display: 'flex', alignItems: 'center', gap: 8,
                cursor: 'pointer', fontSize: 13, padding: '8px 14px',
                border: `1px solid ${upstream === u ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 6, background: upstream === u ? 'var(--accent-dim)' : 'var(--bg-3)',
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
                  {u === 'yandex' ? 'Yandex DNS' : 'Default'}
                </div>
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {u === 'yandex' ? '77.88.8.8' : '1.1.1.1 / 8.8.8.8'}
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

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6l-1 14H6L5 6"/>
      <path d="M10 11v6M14 11v6"/>
      <path d="M9 6V4h6v2"/>
    </svg>
  )
}
