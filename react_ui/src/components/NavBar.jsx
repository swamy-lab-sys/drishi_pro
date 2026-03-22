/**
 * NavBar — top navigation for the React UI.
 * Links to all migrated pages and provides quick link back to Flask UI.
 */
import { NavLink } from 'react-router-dom'
import { useState, useEffect } from 'react'
import styles from './NavBar.module.css'

const LINKS = [
  { to: '/',            label: 'Dashboard' },
  { to: '/monitor',     label: 'Monitor'   },
  { to: '/settings',    label: 'Settings'  },
  { to: '/qa-manager',  label: 'QA DB'     },
  { to: '/ext-users',   label: 'Ext Users' },
]

export function NavBar() {
  const [flaskUrl, setFlaskUrl] = useState('http://localhost:8000')

  // Resolve the actual server URL (handles ngrok, LAN IP, etc.)
  useEffect(() => {
    fetch('/api/public_url', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.url) setFlaskUrl(d.url) })
      .catch(() => {})
  }, [])

  return (
    <nav className={styles.nav}>
      <span className={styles.brand}>drishi</span>
      <div className={styles.links}>
        {LINKS.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `${styles.link} ${isActive ? styles.active : ''}`
            }
          >
            {label}
          </NavLink>
        ))}
      </div>
      <a
        href={flaskUrl}
        className={styles.flaskLink}
        target="_blank"
        rel="noopener noreferrer"
        title="Open Flask UI"
      >
        Flask ↗
      </a>
    </nav>
  )
}
