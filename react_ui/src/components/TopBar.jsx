/**
 * TopBar — sticky page header with breadcrumb + action buttons.
 * Props:
 *   pageName  — string shown after "Drishi >"
 *   children  — right-side action buttons/badges
 */
import styles from './TopBar.module.css'

export default function TopBar({ pageName, children }) {
  return (
    <div className={styles.topBar}>
      <div className={styles.breadcrumb}>
        <span style={{ color: 'var(--text-muted)' }}>Drishi</span>
        <span className={styles.breadcrumbSep}>›</span>
        <span>{pageName}</span>
      </div>
      {children && (
        <div className={styles.actions}>
          {children}
        </div>
      )}
    </div>
  )
}

// Re-export useful button/badge components for convenience
export function TopBadge({ children, variant = 'green' }) {
  const cls = {
    green: styles.badgeGreen,
    amber: styles.badgeAmber,
    red:   styles.badgeRed,
    gray:  styles.badgeGray,
  }[variant] || styles.badgeGray
  return <span className={`${styles.badge} ${cls}`}>{children}</span>
}

export function TopBtn({ children, variant = 'ghost', onClick, disabled }) {
  const cls = variant === 'green' ? styles.btnGreen : styles.btnGhost
  return (
    <button className={`${styles.btn} ${cls}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  )
}
