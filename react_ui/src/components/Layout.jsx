/**
 * Layout — wraps all pages with the sidebar + content area.
 */
import Sidebar from './Sidebar'

export default function Layout({ children }) {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{
        marginLeft: 220,
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100vh',
        background: 'var(--bg)',
        overflow: 'hidden',
      }}>
        {children}
      </div>
    </div>
  )
}
