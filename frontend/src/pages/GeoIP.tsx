import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'

import {
  createGeoipSource,
  deleteGeoipSource,
  getGeoipSources,
  getGeoipStatus,
  triggerGeoipUpdate,
  updateGeoipSource,
} from '../api'
import Modal from '../components/Modal'
import StatusBadge from '../components/StatusBadge'
import { openSSE } from '../sse'
import { GeoipSource, GeoipStatus } from '../types'
import { formatDateTimeLocal, parseUtcDate } from '../utils/time'

const DEFAULT_GEOIP_SOURCE_BASE = 'https://www.ipdeny.com/ipblocks/data/countries/'

type ProgressLine = { ts: string; msg: string }
type GeoipCreatePayload = { country_code: string; display_name: string; url?: string | null }
type GeoipUpdatePayload = { display_name?: string; enabled?: boolean; url?: string | null }

export default function GeoIP() {
  const qc = useQueryClient()
  const [lines, setLines] = useState<ProgressLine[]>([])
  const [updating, setUpdating] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<GeoipSource | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<GeoipSource | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const termRef = useRef<HTMLDivElement>(null)

  const { data: status, refetch: refetchStatus } = useQuery<GeoipStatus>({
    queryKey: ['geoip-status'],
    queryFn: () => getGeoipStatus().then((r) => r.data),
    refetchInterval: 10_000,
  })

  const { data: sources = [], isLoading } = useQuery<GeoipSource[]>({
    queryKey: ['geoip-sources'],
    queryFn: () => getGeoipSources().then((r) => r.data),
    refetchInterval: 10_000,
  })

  const refreshGeoip = () => {
    qc.invalidateQueries({ queryKey: ['geoip-status'] })
    qc.invalidateQueries({ queryKey: ['geoip-sources'] })
  }

  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight
  }, [lines])

  useEffect(() => () => {
    cleanupRef.current?.()
    cleanupRef.current = null
  }, [])

  const attachProgressStream = () => {
    cleanupRef.current?.()
    const cleanup = openSSE(
      '/api/geoip/progress',
      (raw) => {
        const payload = raw as { message?: string }
        const msg = payload.message ?? (typeof raw === 'string' ? raw : '')
        if (!msg) return
        if (msg === '__done__') {
          cleanup()
          cleanupRef.current = null
          setUpdating(false)
          refreshGeoip()
          return
        }
        setLines((current) => [...current, { ts: new Date().toLocaleTimeString(), msg }])
      },
      () => {
        cleanupRef.current = null
        setUpdating(false)
        refetchStatus()
      }
    )
    cleanupRef.current = cleanup
  }

  useEffect(() => {
    if (status?.update_running && !cleanupRef.current) {
      setUpdating(true)
      attachProgressStream()
    }
  }, [status?.update_running])

  const startUpdate = async () => {
    setUpdating(true)
    setLines([])

    try {
      await triggerGeoipUpdate()
    } catch (error) {
      const axiosError = error as AxiosError<{ detail?: string }>
      if (axiosError.response?.status !== 409) {
        setLines([{ ts: new Date().toLocaleTimeString(), msg: getErrorMessage(error, 'Update failed') }])
      }
    }

    attachProgressStream()
  }

  const createMut = useMutation({
    mutationFn: (payload: GeoipCreatePayload) => createGeoipSource(payload),
    onSuccess: () => {
      setAddOpen(false)
      refreshGeoip()
    },
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: GeoipUpdatePayload }) =>
      updateGeoipSource(id, payload),
    onSuccess: () => {
      setEditTarget(null)
      refreshGeoip()
    },
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteGeoipSource(id),
    onSuccess: () => {
      setDeleteTarget(null)
      refreshGeoip()
    },
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      updateGeoipSource(id, { enabled }),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: ['geoip-sources'] })
      await qc.cancelQueries({ queryKey: ['geoip-status'] })

      const previousSources = qc.getQueryData<GeoipSource[]>(['geoip-sources'])
      const previousStatus = qc.getQueryData<GeoipStatus>(['geoip-status'])

      qc.setQueryData<GeoipSource[]>(['geoip-sources'], (current = []) =>
        current.map((source) => source.id === id ? { ...source, enabled } : source)
      )

      qc.setQueryData<GeoipStatus>(['geoip-status'], (current) =>
        current ? {
          ...current,
          sources: current.sources.map((source) => source.id === id ? { ...source, enabled } : source),
        } : current
      )

      return { previousSources, previousStatus }
    },
    onError: (_error, _vars, context) => {
      if (context?.previousSources) {
        qc.setQueryData(['geoip-sources'], context.previousSources)
      }
      if (context?.previousStatus) {
        qc.setQueryData(['geoip-status'], context.previousStatus)
      }
    },
    onSettled: () => refreshGeoip(),
  })

  const totalPrefixes = status?.total_prefixes ?? sources.reduce((sum, source) => sum + (source.prefix_count ?? 0), 0)
  const lastUpdated = status?.last_updated ?? latestUpdatedAt(sources)

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Local Routing Zones</div>
          <div className="page-subtitle">Countries routed through physical interface (eth0) instead of VPN</div>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-secondary btn-sm" onClick={refreshGeoip}>Refresh</button>
          <button className="btn btn-primary btn-sm" onClick={() => setAddOpen(true)}>+ Add Country</button>
          <button
            className="btn btn-primary btn-sm"
            onClick={startUpdate}
            disabled={updating || status?.update_running}
          >
            {(updating || status?.update_running) ? <><span className="spinner" /> Updating…</> : 'Update now'}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center justify-between" style={{ gap: 16, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 28, fontWeight: 700 }}>{sources.length}</div>
            <div className="text-muted text-sm">countries in local routing zone</div>
          </div>
          <div>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)' }}>
              {totalPrefixes.toLocaleString()}
            </div>
            <div className="text-muted text-sm">total prefixes in local routing zone</div>
          </div>
          <div style={{ minWidth: 180 }}>
            <div className="text-muted text-sm">Last aggregated update</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {formatRelativeTime(lastUpdated)}
            </div>
          </div>
          <div>
            <StatusBadge status={status?.update_running || updating ? 'running' : 'success'} />
          </div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">Countries</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Flag</th>
                <th>Country</th>
                <th>Display name</th>
                <th>Prefixes</th>
                <th>Last updated</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', padding: 24 }}>
                    <span className="spinner" />
                  </td>
                </tr>
              ) : sources.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-muted" style={{ textAlign: 'center', padding: 24 }}>
                    No local routing zones configured
                  </td>
                </tr>
              ) : sources.map((source) => (
                <tr key={source.id}>
                  <td style={{ fontSize: 20 }}>{countryFlag(source.country_code)}</td>
                  <td>
                    <span className="badge badge-unknown">{source.country_code.toUpperCase()}</span>
                  </td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{source.display_name}</div>
                    <div className="text-muted text-sm text-mono" style={{ marginTop: 2 }}>{source.url}</div>
                  </td>
                  <td className="text-mono">{(source.prefix_count ?? 0).toLocaleString()}</td>
                  <td className="text-muted text-sm">{formatRelativeTime(source.last_updated)}</td>
                  <td>
                    <StatusBadge status={source.enabled ? 'online' : 'offline'} />
                  </td>
                  <td>
                    <div className="flex items-center gap-2" style={{ justifyContent: 'flex-end' }}>
                      <label className="toggle" title={source.enabled ? 'Disable zone' : 'Enable zone'}>
                        <input
                          type="checkbox"
                          checked={source.enabled}
                          onChange={() => toggleMut.mutate({ id: source.id, enabled: !source.enabled })}
                          disabled={toggleMut.isPending}
                        />
                        <span className="toggle-slider" />
                      </label>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setEditTarget(source)}
                      >
                        Edit
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => setDeleteTarget(source)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {(lines.length > 0 || updating || status?.update_running) && (
        <div className="section">
          <div className="section-title">Update progress</div>
          <div className="terminal" ref={termRef}>
            {lines.map((line, index) => (
              <div key={`${line.ts}-${index}`} className="terminal-line">
                <span className="ts">{line.ts}</span>
                <span className="msg">{line.msg}</span>
              </div>
            ))}
            {(updating || status?.update_running) && (
              <div className="terminal-line">
                <span className="ts" />
                <span className="msg"><span className="spinner" /></span>
              </div>
            )}
          </div>
        </div>
      )}

      {addOpen && (
        <AddCountryModal
          pending={createMut.isPending}
          onClose={() => setAddOpen(false)}
          onSubmit={(payload) => createMut.mutateAsync(payload)}
        />
      )}

      {editTarget && (
        <EditCountryModal
          source={editTarget}
          pending={updateMut.isPending}
          onClose={() => setEditTarget(null)}
          onSubmit={(payload) => updateMut.mutateAsync({ id: editTarget.id, payload })}
        />
      )}

      {deleteTarget && (
        <Modal open title="Remove local routing zone" onClose={() => setDeleteTarget(null)}>
          <div style={{ marginBottom: 16, fontSize: 14 }}>
            Remove {deleteTarget.display_name} ({deleteTarget.country_code.toUpperCase()}) from local routing zones?
          </div>
          <div className="modal-actions">
            <button className="btn btn-secondary" onClick={() => setDeleteTarget(null)}>Cancel</button>
            <button
              className="btn btn-danger"
              onClick={() => deleteMut.mutate(deleteTarget.id)}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? <span className="spinner" /> : 'Remove'}
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}

function AddCountryModal({
  pending,
  onClose,
  onSubmit,
}: {
  pending: boolean
  onClose: () => void
  onSubmit: (payload: GeoipCreatePayload) => Promise<unknown>
}) {
  const [countryCode, setCountryCode] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [useCustomUrl, setUseCustomUrl] = useState(false)
  const [customUrl, setCustomUrl] = useState('')
  const [error, setError] = useState('')

  const normalizedCode = normalizeCountryCode(countryCode)
  const previewUrl = normalizedCode ? buildDefaultGeoipUrl(normalizedCode) : `${DEFAULT_GEOIP_SOURCE_BASE}{cc}.zone`

  const handleSubmit = async () => {
    setError('')

    if (!/^[a-z]{2}$/.test(normalizedCode)) {
      setError('Country code must contain exactly 2 letters.')
      return
    }
    if (!displayName.trim()) {
      setError('Display name is required.')
      return
    }
    if (useCustomUrl && !customUrl.trim()) {
      setError('Custom URL is required when enabled.')
      return
    }

    try {
      await onSubmit({
        country_code: normalizedCode,
        display_name: displayName.trim(),
        url: useCustomUrl ? customUrl.trim() : null,
      })
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to add local routing zone'))
    }
  }

  return (
    <Modal open title="Add local routing zone" onClose={onClose}>
      <div className="form-group">
        <label className="form-label">Country code</label>
        <input
          className="form-input mono"
          value={countryCode}
          onChange={(e) => setCountryCode(normalizeCountryCode(e.target.value))}
          placeholder="ru"
          maxLength={2}
        />
      </div>

      <div className="form-group">
        <label className="form-label">Display name</label>
        <input
          className="form-input"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Russia"
        />
      </div>

      <div className="form-group">
        <label className="form-label" style={{ marginBottom: 8 }}>
          <input
            type="checkbox"
            checked={useCustomUrl}
            onChange={(e) => setUseCustomUrl(e.target.checked)}
            style={{ marginRight: 8 }}
          />
          Use custom URL
        </label>

        {useCustomUrl ? (
          <input
            className="form-input mono"
            value={customUrl}
            onChange={(e) => setCustomUrl(e.target.value)}
            placeholder="https://example.com/ru.zone"
          />
        ) : (
          <div
            className="text-muted text-sm"
            style={{ background: 'var(--bg-3)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 12px' }}
          >
            Will use: <span className="text-mono">{previewUrl}</span>
          </div>
        )}
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 13, marginTop: 10 }}>
          {error}
        </div>
      )}

      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={handleSubmit} disabled={pending}>
          {pending ? <span className="spinner" /> : 'Add Zone'}
        </button>
      </div>
    </Modal>
  )
}

function EditCountryModal({
  source,
  pending,
  onClose,
  onSubmit,
}: {
  source: GeoipSource
  pending: boolean
  onClose: () => void
  onSubmit: (payload: GeoipUpdatePayload) => Promise<unknown>
}) {
  const [displayName, setDisplayName] = useState(source.display_name)
  const [url, setUrl] = useState(source.url)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    setError('')
    if (!displayName.trim()) {
      setError('Display name is required.')
      return
    }

    try {
      await onSubmit({
        display_name: displayName.trim(),
        url: url.trim() || null,
      })
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to update local routing zone'))
    }
  }

  return (
    <Modal open title={`Edit ${source.country_code.toUpperCase()} zone`} onClose={onClose}>
      <div className="form-group">
        <label className="form-label">Display name</label>
        <input
          className="form-input"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label className="form-label">GeoIP URL</label>
        <input
          className="form-input mono"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </div>

      {error && (
        <div style={{ color: 'var(--danger)', fontSize: 13, marginTop: 10 }}>
          {error}
        </div>
      )}

      <div className="modal-actions">
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={handleSubmit} disabled={pending}>
          {pending ? <span className="spinner" /> : 'Save'}
        </button>
      </div>
    </Modal>
  )
}

function normalizeCountryCode(value: string) {
  return value.toLowerCase().replace(/[^a-z]/g, '').slice(0, 2)
}

function buildDefaultGeoipUrl(countryCode: string) {
  return `${DEFAULT_GEOIP_SOURCE_BASE}${countryCode}.zone`
}

function countryFlag(countryCode: string) {
  if (!/^[a-z]{2}$/i.test(countryCode)) return '🌐'
  const cc = countryCode.toLowerCase()
  return String.fromCodePoint(0x1f1e6 + cc.charCodeAt(0) - 97)
    + String.fromCodePoint(0x1f1e6 + cc.charCodeAt(1) - 97)
}

function formatRelativeTime(value: string | null | undefined) {
  if (!value) return 'Never'

  const date = parseUtcDate(value)
  if (!date) return 'Never'
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.max(0, Math.round(diffMs / 1000))

  if (diffSec < 60) return 'just now'
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  if (diffSec < 604800) return `${Math.floor(diffSec / 86400)}d ago`
  return formatDateTimeLocal(value)
}

function latestUpdatedAt(sources: GeoipSource[]) {
  const timestamps = sources
    .map((source) => source.last_updated)
    .filter((value): value is string => Boolean(value))
    .sort()
  return timestamps.length > 0 ? timestamps[timestamps.length - 1] : null
}

function getErrorMessage(error: unknown, fallback: string) {
  const axiosError = error as AxiosError<{ detail?: string }>
  return axiosError.response?.data?.detail ?? fallback
}
