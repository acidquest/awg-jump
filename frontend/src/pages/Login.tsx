import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../api'

function makeIpv4(seed: number) {
  const octet = (value: number) => (value % 254) + 1
  return [
    octet(seed * 17 + 11),
    octet(seed * 29 + 23),
    octet(seed * 43 + 47),
    octet(seed * 61 + 89),
  ].join('.')
}

function makeRainStream(length: number, offset: number) {
  return Array.from({ length }, (_, index) => makeIpv4(offset * 31 + index)).join('  ')
}

export default function Login() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [streams] = useState(() =>
    Array.from({ length: 26 }, (_, index) => ({
      id: index,
      text: makeRainStream(30 + (index % 6) * 6, index),
      duration: 20 + (index % 5) * 4,
      delay: (index % 7) * -1.6,
      left: `${index * 3.9}%`,
      opacity: 0.16 + (index % 4) * 0.05,
      size: 12 + (index % 3),
    }))
  )

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await login(username, password)
      localStorage.setItem('token', res.data.access_token)
      navigate('/')
    } catch {
      setError('Invalid credentials')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-shell">
      <div className="login-rain" aria-hidden="true">
        {streams.map((stream) => (
          <div
            key={stream.id}
            className="login-rain-column"
            style={{
              left: stream.left,
              animationDuration: `${stream.duration}s`,
              animationDelay: `${stream.delay}s`,
              opacity: stream.opacity,
              fontSize: stream.size,
            }}
          >
            {stream.text}
          </div>
        ))}
      </div>

      <div className="login-panel" style={{ width: 360 }}>
        <div style={{ marginBottom: 28, textAlign: 'center' }}>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 24,
              fontWeight: 700,
              color: 'var(--accent)',
              letterSpacing: 2,
              marginBottom: 4,
            }}
          >
            AWG Jump
          </div>
        </div>

        <div className="card">
          {error && <div className="error-box">{error}</div>}
          <form onSubmit={submit}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                className="form-input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: '100%', justifyContent: 'center', marginTop: 4 }}
              disabled={loading}
            >
              {loading ? <span className="spinner" /> : 'Sign in'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
