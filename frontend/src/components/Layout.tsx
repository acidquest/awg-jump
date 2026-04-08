import { NavLink, useNavigate } from 'react-router-dom'
import { logout } from '../api'

const NAV = [
  { to: '/', label: 'Dashboard', icon: GridIcon },
  { to: '/interfaces', label: 'Interfaces', icon: CpuIcon },
  { to: '/peers', label: 'Peers', icon: UsersIcon },
  { to: '/nodes', label: 'Nodes', icon: ServerIcon },
  { to: '/routing', label: 'Routing', icon: RouteIcon },
  { to: '/geoip', label: 'GeoIP', icon: GlobeIcon },
  { to: '/dns', label: 'Split DNS', icon: DnsIcon },
  { to: '/backup', label: 'Backup', icon: ArchiveIcon },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()

  const handleLogout = async () => {
    try { await logout() } catch {}
    localStorage.removeItem('token')
    navigate('/login')
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo" aria-label="AWG Jump">
          <FaviconMark />
        </div>
        <nav className="sidebar-nav">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="btn btn-ghost btn-sm" style={{ width: '100%' }} onClick={handleLogout}>
            <LogoutIcon size={14} />
            Logout
          </button>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  )
}

// ── Inline SVG icons ──────────────────────────────────────────────────────
function FaviconMark() {
  return (
    <svg className="sidebar-mark" viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="awgJumpBg" x1="8" y1="6" x2="58" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0f172a" />
          <stop offset="1" stopColor="#0a1018" />
        </linearGradient>
        <linearGradient id="awgJumpBeam" x1="16" y1="14" x2="45" y2="49" gradientUnits="userSpaceOnUse">
          <stop stopColor="#34f5c5" />
          <stop offset="1" stopColor="#00d4aa" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#awgJumpBg)" />
      <path d="M13 18c0-2.761 2.239-5 5-5h28c2.761 0 5 2.239 5 5v4h-4v-3a2 2 0 0 0-2-2H19a2 2 0 0 0-2 2v26a2 2 0 0 0 2 2h10v4H18c-2.761 0-5-2.239-5-5V18Z" fill="#1d2733" />
      <circle cx="23" cy="24" r="4" fill="#38bdf8" />
      <circle cx="23" cy="40" r="4" fill="#38bdf8" opacity="0.9" />
      <circle cx="42" cy="32" r="5" fill="#34f5c5" />
      <path d="M26.5 24h8.5l-5 5" fill="none" stroke="url(#awgJumpBeam)" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M26.5 40h8.5" fill="none" stroke="url(#awgJumpBeam)" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M35 24v16" fill="none" stroke="url(#awgJumpBeam)" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M35 32h12" fill="none" stroke="url(#awgJumpBeam)" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M42 27v10l6-5-6-5Z" fill="#34f5c5" />
    </svg>
  )
}

function GridIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
      <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
    </svg>
  )
}

function CpuIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="6" height="6"/>
      <path d="M15 9V5h-2M9 9V5h2M9 15v4h2M15 15v4h-2M20 9h-4M20 15h-4M4 9h4M4 15h4"/>
      <rect x="3" y="3" width="18" height="18" rx="2"/>
    </svg>
  )
}

function UsersIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
      <circle cx="9" cy="7" r="4"/>
      <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>
    </svg>
  )
}

function ServerIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="8" rx="2"/>
      <rect x="2" y="14" width="20" height="8" rx="2"/>
      <line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
    </svg>
  )
}

function RouteIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/>
      <circle cx="18" cy="5" r="3"/>
    </svg>
  )
}

function GlobeIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <line x1="2" y1="12" x2="22" y2="12"/>
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
    </svg>
  )
}

function ArchiveIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="21 8 21 21 3 21 3 8"/>
      <rect x="1" y="3" width="22" height="5"/>
      <line x1="10" y1="12" x2="14" y2="12"/>
    </svg>
  )
}

function DnsIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a10 10 0 1 0 10 10"/>
      <path d="M12 6v6l4 2"/>
      <path d="M18 14h4M20 12v4"/>
    </svg>
  )
}

function LogoutIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
      <polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
    </svg>
  )
}
