import { useState, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import { downloadBackup, uploadBackup } from '../api'

export default function Backup() {
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const downloadMut = useMutation({
    mutationFn: async () => {
      const res = await downloadBackup()
      const url = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a')
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      a.href = url
      a.download = `awg-backup-${ts}.zip`
      a.click()
      URL.revokeObjectURL(url)
    },
  })

  const doUpload = async (file: File) => {
    setUploading(true)
    setUploadMsg('')
    setUploadError('')
    try {
      await uploadBackup(file)
      setUploadMsg(`Backup "${file.name}" imported successfully. Restart the service to apply.`)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Import failed'
      setUploadError(msg)
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) doUpload(file)
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) doUpload(file)
    e.target.value = ''
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Backup</div>
          <div className="page-subtitle">Export and import configuration</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Export */}
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>Export</div>
          <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16, lineHeight: 1.6 }}>
            Downloads a ZIP archive containing:
          </p>
          <ul style={{ color: 'var(--text-2)', fontSize: 13, paddingLeft: 20, marginBottom: 20, lineHeight: 1.8 }}>
            <li><code>config.db</code> — SQLite database (peers, interfaces, nodes)</li>
            <li><code>env_snapshot.json</code> — public config params</li>
            <li><code>wg_configs/</code> — WireGuard config files</li>
          </ul>
          <button
            className="btn btn-primary"
            onClick={() => downloadMut.mutate()}
            disabled={downloadMut.isPending}
          >
            {downloadMut.isPending ? <><span className="spinner" /> Preparing…</> : '⬇ Download backup'}
          </button>
          {downloadMut.isError && (
            <div className="error-box" style={{ marginTop: 12 }}>
              {String((downloadMut.error as { message?: string })?.message ?? 'Error')}
            </div>
          )}
        </div>

        {/* Import */}
        <div className="card">
          <div className="card-title" style={{ marginBottom: 12 }}>Import</div>
          <p style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 16, lineHeight: 1.6 }}>
            Restore from a previously exported backup ZIP. The service should be restarted after import.
          </p>

          {uploadMsg && <div className="info-box">{uploadMsg}</div>}
          {uploadError && <div className="error-box">{uploadError}</div>}

          <div
            style={{
              border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 8,
              padding: '32px 20px',
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'border-color 0.15s',
              background: dragging ? 'var(--accent-dim)' : 'var(--bg-3)',
            }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? (
              <div><span className="spinner" /><div className="text-muted mt-2">Importing…</div></div>
            ) : (
              <>
                <div style={{ fontSize: 28, marginBottom: 8 }}>📦</div>
                <div style={{ color: 'var(--text-2)', fontSize: 13 }}>
                  Drag & drop backup.zip here, or click to browse
                </div>
              </>
            )}
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".zip"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button
            className="btn btn-secondary"
            style={{ marginTop: 12, width: '100%' }}
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
          >
            Browse file
          </button>
        </div>
      </div>
    </>
  )
}
