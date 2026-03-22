/**
 * Ext Users — manage Chrome extension users.
 * Table of users + inline create form + usage panel.
 */
import { useState, useCallback } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar from '../components/TopBar'
import styles from './ExtUsers.module.css'

const BLANK_FORM = {
  token: '',
  name: '',
  role: '',
  coding_language: 'python',
  db_user_id: 1,
}

const LANGS = ['python', 'java', 'javascript', 'sql']
const ROLES = ['', 'general', 'python', 'java', 'javascript', 'sql', 'saas', 'system_design']

function StatusDot({ active }) {
  return (
    <span
      className={active ? styles.dotOn : styles.dotOff}
      title={active ? 'active' : 'inactive'}
    />
  )
}

function UsagePanel({ token, onClose }) {
  const { data, loading } = useApi(`/api/ext_users/${encodeURIComponent(token)}/usage`)
  if (loading) return <div className={styles.usagePanel}><span className={styles.loading}>loading…</span></div>
  if (!data)   return <div className={styles.usagePanel}><span className={styles.empty}>No usage data</span></div>
  return (
    <div className={styles.usagePanel}>
      <div className={styles.usageHeader}>
        <span className={styles.usageName}>{data.name || token}</span>
        <button className={styles.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div className={styles.usageStats}>
        <span>Questions: <strong>{data.total_questions ?? 0}</strong></span>
        <span>LLM hits: <strong>{data.total_llm_hits ?? 0}</strong></span>
        {data.last_seen && <span>Last seen: <strong>{data.last_seen.slice(0, 16)}</strong></span>}
      </div>
      <div className={styles.usageLog}>
        {(data.log || []).length === 0 ? (
          <div className={styles.empty}>No log entries</div>
        ) : (
          <table className={styles.logTable}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Source</th>
                <th>Question</th>
              </tr>
            </thead>
            <tbody>
              {data.log.slice(0, 50).map((entry, i) => (
                <tr key={i}>
                  <td className={styles.logTime}>{(entry.ts || entry.created_at || '').slice(0, 16)}</td>
                  <td><span className={styles.srcBadge}>{entry.source || '—'}</span></td>
                  <td className={styles.logQ}>{entry.question}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default function ExtUsers() {
  const toast = useToast()

  const { data: usersRaw, loading, refetch } = useApi('/api/ext_users')
  const { data: dbUsersRaw } = useApi('/api/users')

  const [form,     setForm]     = useState(BLANK_FORM)
  const [creating, setCreating] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [usageToken, setUsageToken] = useState(null)
  const [toggling, setToggling] = useState(null)

  const users   = Array.isArray(usersRaw) ? usersRaw : []
  const dbUsers = Array.isArray(dbUsersRaw) ? dbUsersRaw : []

  const handleFormChange = (field, value) => {
    setForm(f => ({ ...f, [field]: value }))
  }

  const handleCreate = async () => {
    if (!form.token.trim() || !form.name.trim()) {
      toast.error('Token and name are required')
      return
    }
    setCreating(true)
    try {
      await apiCall('/api/ext_users', { method: 'POST', body: form })
      toast.success(`User "${form.name}" created`)
      setForm(BLANK_FORM)
      setShowCreate(false)
      refetch()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleToggleActive = useCallback(async (user) => {
    setToggling(user.token)
    try {
      await apiCall(`/api/ext_users/${encodeURIComponent(user.token)}`, {
        method: 'PATCH',
        body: { active: user.active ? 0 : 1 },
      })
      toast.info(`${user.name} ${user.active ? 'deactivated' : 'activated'}`)
      refetch()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setToggling(null)
    }
  }, [toast, refetch])

  const handleDelete = useCallback(async (user) => {
    if (!window.confirm(`Delete user "${user.name}"? This cannot be undone.`)) return
    try {
      await apiCall(`/api/ext_users/${encodeURIComponent(user.token)}`, { method: 'DELETE' })
      toast.success(`Deleted ${user.name}`)
      if (usageToken === user.token) setUsageToken(null)
      refetch()
    } catch (e) {
      toast.error(e.message)
    }
  }, [toast, refetch, usageToken])

  const handleCopyToken = (token) => {
    navigator.clipboard.writeText(token).then(() => toast.success('Token copied'))
  }

  return (
    <div className={styles.page}>
      <TopBar pageName="Ext Users" />
      <div className={styles.body}>

        {/* ── Main panel ──────────────────────────────────────────────────── */}
        <div className={styles.main}>

          {/* Header row */}
          <div className={styles.header}>
            <span className={styles.title}>
              Extension Users
              {users.length > 0 && <span className={styles.count}>{users.length}</span>}
            </span>
            <button
              className={styles.createBtn}
              onClick={() => setShowCreate(v => !v)}
            >
              {showCreate ? '✕ Cancel' : '+ New user'}
            </button>
          </div>

          {/* Create form */}
          {showCreate && (
            <div className={styles.createForm}>
              <div className={styles.formRow}>
                <label className={styles.formLabel}>Token</label>
                <input
                  className={styles.input}
                  value={form.token}
                  onChange={e => handleFormChange('token', e.target.value)}
                  placeholder="unique-token-123"
                />
              </div>
              <div className={styles.formRow}>
                <label className={styles.formLabel}>Name</label>
                <input
                  className={styles.input}
                  value={form.name}
                  onChange={e => handleFormChange('name', e.target.value)}
                  placeholder="User name"
                />
              </div>
              <div className={styles.formRow}>
                <label className={styles.formLabel}>Role</label>
                <select
                  className={styles.select}
                  value={form.role}
                  onChange={e => handleFormChange('role', e.target.value)}
                >
                  {ROLES.map(r => (
                    <option key={r} value={r}>{r || '— none —'}</option>
                  ))}
                </select>
              </div>
              <div className={styles.formRow}>
                <label className={styles.formLabel}>Language</label>
                <select
                  className={styles.select}
                  value={form.coding_language}
                  onChange={e => handleFormChange('coding_language', e.target.value)}
                >
                  {LANGS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
              <div className={styles.formRow}>
                <label className={styles.formLabel}>DB User</label>
                <select
                  className={styles.select}
                  value={form.db_user_id}
                  onChange={e => handleFormChange('db_user_id', Number(e.target.value))}
                >
                  {dbUsers.map(u => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
                </select>
              </div>
              <div className={styles.formActions}>
                <button
                  className={styles.saveBtn}
                  onClick={handleCreate}
                  disabled={creating}
                >
                  {creating ? 'creating…' : 'Create user'}
                </button>
              </div>
            </div>
          )}

          {/* Users table */}
          {loading ? (
            <div className={styles.loadingMsg}>loading…</div>
          ) : users.length === 0 ? (
            <div className={styles.emptyMsg}>No extension users. Create one above.</div>
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Name</th>
                    <th>Token</th>
                    <th>Role</th>
                    <th>Lang</th>
                    <th>Questions</th>
                    <th>Last seen</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.token} className={!u.active ? styles.rowInactive : ''}>
                      <td><StatusDot active={u.active} /></td>
                      <td className={styles.cellName}>{u.name}</td>
                      <td>
                        <span
                          className={styles.tokenChip}
                          onClick={() => handleCopyToken(u.token)}
                          title="Click to copy"
                        >
                          {u.token}
                        </span>
                      </td>
                      <td className={styles.cellMuted}>{u.role || '—'}</td>
                      <td className={styles.cellMuted}>{u.coding_language || '—'}</td>
                      <td className={styles.cellNum}>{u.total_questions ?? 0}</td>
                      <td className={styles.cellMuted}>
                        {u.last_seen ? u.last_seen.slice(0, 10) : '—'}
                      </td>
                      <td>
                        <div className={styles.rowActions}>
                          <button
                            className={styles.actionBtn}
                            onClick={() => setUsageToken(usageToken === u.token ? null : u.token)}
                            title="Usage log"
                          >
                            log
                          </button>
                          <button
                            className={`${styles.actionBtn} ${u.active ? styles.deactivateBtn : styles.activateBtn}`}
                            onClick={() => handleToggleActive(u)}
                            disabled={toggling === u.token}
                          >
                            {u.active ? 'deactivate' : 'activate'}
                          </button>
                          <button
                            className={`${styles.actionBtn} ${styles.deleteBtn}`}
                            onClick={() => handleDelete(u)}
                          >
                            delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Usage panel (slide in when token selected) ───────────────────── */}
        {usageToken && (
          <UsagePanel token={usageToken} onClose={() => setUsageToken(null)} />
        )}

      </div>
    </div>
  )
}
