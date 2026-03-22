/**
 * Sidebar — left navigation panel.
 * Fixed 220px wide. Active item highlighted in green.
 */
import { NavLink, useLocation } from 'react-router-dom'
import { useState, useEffect } from 'react'
import styles from './Sidebar.module.css'

// ── SVG Icons ─────────────────────────────────────────────────────────────────

const MicIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 2a3 3 0 0 1 3 3v7a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>
    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
    <line x1="12" y1="19" x2="12" y2="23"/>
    <line x1="8" y1="23" x2="16" y2="23"/>
  </svg>
)

const MonitorIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="2" y="3" width="20" height="14" rx="2"/>
    <line x1="8" y1="21" x2="16" y2="21"/>
    <line x1="12" y1="17" x2="12" y2="21"/>
  </svg>
)

const UserIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
    <circle cx="12" cy="7" r="4"/>
  </svg>
)

const ListIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="8" y1="6" x2="21" y2="6"/>
    <line x1="8" y1="12" x2="21" y2="12"/>
    <line x1="8" y1="18" x2="21" y2="18"/>
    <line x1="3" y1="6" x2="3.01" y2="6"/>
    <line x1="3" y1="12" x2="3.01" y2="12"/>
    <line x1="3" y1="18" x2="3.01" y2="18"/>
  </svg>
)

const SearchIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="11" cy="11" r="8"/>
    <line x1="21" y1="21" x2="16.65" y2="16.65"/>
  </svg>
)

const SettingsIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>
)

const KeyIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>
  </svg>
)

const DBIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <ellipse cx="12" cy="5" rx="9" ry="3"/>
    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
  </svg>
)

const UsersIcon = () => (
  <svg className={styles.navIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>
)

// ── Nav items ──────────────────────────────────────────────────────────────────

const NAV_GROUPS = [
  {
    label: 'MAIN',
    items: [
      { path: '/',        label: 'Interview', icon: MicIcon,     end: true },
      { path: '/monitor', label: 'Monitor',   icon: MonitorIcon, end: false },
    ],
  },
  {
    label: 'LIBRARY',
    items: [
      { path: '/profiles',  label: 'Profiles',       icon: UserIcon,    end: false },
      { path: '/questions', label: 'Questions',       icon: ListIcon,    end: false },
      { path: '/lookup',    label: 'Keyword Lookup',  icon: SearchIcon,  end: false },
      { path: '/qa-manager',label: 'QA Database',    icon: DBIcon,      end: false },
    ],
  },
  {
    label: 'ADMIN',
    items: [
      { path: '/settings',  label: 'Settings',   icon: SettingsIcon, end: false },
      { path: '/api-keys',  label: 'API Keys',   icon: KeyIcon,      end: false },
      { path: '/ext-users', label: 'Ext Users',  icon: UsersIcon,    end: false },
    ],
  },
]

// ── Component ──────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const [connected, setConnected] = useState(false)
  const location = useLocation()

  useEffect(() => {
    fetch('/api/system/health', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(d => setConnected(d?.stt_status === 'ok' || d?.llm_status === 'ok'))
      .catch(() => setConnected(false))

    const id = setInterval(() => {
      fetch('/api/system/health', { cache: 'no-store' })
        .then(r => r.ok ? r.json() : null)
        .then(d => setConnected(d?.stt_status === 'ok' || d?.llm_status === 'ok'))
        .catch(() => setConnected(false))
    }, 10000)
    return () => clearInterval(id)
  }, [])

  return (
    <aside className={styles.sidebar}>
      {/* Logo */}
      <div className={styles.logo}>
        <div className={styles.logoCircle}>D</div>
        <span className={styles.logoText}>DRISHI</span>
      </div>

      {/* Primary nav */}
      <nav className={styles.nav}>
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.label}>
            {gi > 0 && <div className={styles.divider} />}
            <div className={styles.groupLabel}>{group.label}</div>
            {group.items.map(({ path, label, icon: Icon, end }) => (
              <NavLink
                key={path}
                to={path}
                end={end}
                className={({ isActive }) =>
                  `${styles.navItem} ${isActive ? styles.active : ''}`
                }
              >
                <Icon />
                {label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      {/* Status */}
      <div className={styles.status}>
        <span className={`${styles.statusDot} ${connected ? '' : styles.disconnected}`} />
        {connected ? 'Connected' : 'Not connected'}
      </div>
    </aside>
  )
}
