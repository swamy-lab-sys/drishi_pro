/**
 * Profiles — manage candidate user profiles.
 * Shows active user card + list of all profiles.
 * URL: /profiles
 */
import { useState, useCallback } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar, { TopBtn } from '../components/TopBar'
import styles from './Profiles.module.css'

// Deterministic color from name string
const AVATAR_COLORS = ['#3B82F6','#8B5CF6','#F59E0B','#EF4444','#00C896','#F97316','#06B6D4','#EC4899']
function avatarColor(name) {
  if (!name) return AVATAR_COLORS[0]
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % AVATAR_COLORS.length
  return AVATAR_COLORS[h]
}

function initials(name) {
  if (!name) return '?'
  return name.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase()
}

const ROLE_OPTIONS = ['general', 'python', 'java', 'javascript', 'sql', 'saas', 'system_design']

export default function Profiles() {
  const toast = useToast()
  const { data: users, refetch } = useApi('/api/users')
  const { data: launchConfig }   = useApi('/api/launch_config')
  const [showForm, setShowForm]  = useState(false)
  const [form, setForm]          = useState({ name: '', role: 'general', experience_years: 3 })
  const [saving, setSaving]      = useState(false)

  const activeUserId = launchConfig?.user_id_override
    ? parseInt(launchConfig.user_id_override, 10)
    : null

  const activeUser = Array.isArray(users)
    ? users.find(u => u.id === activeUserId) || users[0]
    : null

  const handleActivate = useCallback(async (id) => {
    try {
      await apiCall(`/api/users/activate/${id}`, { method: 'POST' })
      await apiCall('/api/launch_config', { method: 'POST', body: { user_id_override: String(id) } })
      toast.success('Profile activated')
      refetch()
    } catch (e) {
      toast.error(e.message)
    }
  }, [toast, refetch])

  const handleDelete = useCallback(async (id, name) => {
    if (!window.confirm(`Delete profile "${name}"?`)) return
    try {
      await apiCall(`/api/users/${id}`, { method: 'DELETE' })
      toast.success('Profile deleted')
      refetch()
    } catch (e) {
      toast.error(e.message)
    }
  }, [toast, refetch])

  const handleCreate = useCallback(async () => {
    if (!form.name.trim()) { toast.error('Name is required'); return }
    setSaving(true)
    try {
      await apiCall('/api/users', { method: 'POST', body: form })
      toast.success('Profile created')
      setForm({ name: '', role: 'general', experience_years: 3 })
      setShowForm(false)
      refetch()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }, [form, toast, refetch])

  const userList = Array.isArray(users) ? users : []

  return (
    <div className={styles.page}>
      <TopBar pageName="Profiles">
        <TopBtn variant="green" onClick={() => setShowForm(v => !v)}>
          {showForm ? 'Cancel' : '+ New Profile'}
        </TopBtn>
      </TopBar>

      <div className={styles.body}>

        {/* Active user card */}
        {activeUser && (
          <div className={styles.activeCard}>
            <div className={styles.activeAvatar} style={{ background: avatarColor(activeUser.name) }}>
              {initials(activeUser.name)}
            </div>
            <div className={styles.activeInfo}>
              <div className={styles.activeName}>{activeUser.name}</div>
              <div className={styles.activeRole}>
                {activeUser.role || 'General'} · {activeUser.experience_years || 0}y exp
              </div>
            </div>
            <div className={styles.activeBadge}>
              <span className={styles.activeDot} />
              Active Session
            </div>
          </div>
        )}

        {/* Create form */}
        {showForm && (
          <div className={styles.createCard}>
            <div className={styles.createTitle}>New Profile</div>
            <div className={styles.formRow}>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Name</label>
                <input
                  className={styles.input}
                  placeholder="Full name"
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Role</label>
                <select
                  className={styles.select}
                  value={form.role}
                  onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                >
                  {ROLE_OPTIONS.map(r => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </div>
              <div className={styles.formGroup}>
                <label className={styles.formLabel}>Experience (years)</label>
                <input
                  className={styles.input}
                  type="number"
                  min="0"
                  max="30"
                  style={{ minWidth: 90 }}
                  value={form.experience_years}
                  onChange={e => setForm(f => ({ ...f, experience_years: parseInt(e.target.value) || 0 }))}
                />
              </div>
              <button
                className={styles.createBtn}
                onClick={handleCreate}
                disabled={saving}
              >
                {saving ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        )}

        {/* Profiles list */}
        <div>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>
              Candidate Profiles ({userList.length})
            </span>
            <button className={styles.combineBtn}>Combine</button>
          </div>

          <div className={styles.profilesGrid}>
            {userList.length === 0 && (
              <div className={styles.emptyState}>No profiles yet. Create one to get started.</div>
            )}
            {userList.map(user => {
              const isActive = user.id === activeUserId
              const color = avatarColor(user.name)
              return (
                <div
                  key={user.id}
                  className={`${styles.profileCard} ${isActive ? styles.isActive : ''}`}
                >
                  <div className={styles.avatar} style={{ background: color }}>
                    {initials(user.name)}
                  </div>
                  <div className={styles.profileInfo}>
                    <div className={styles.profileName}>{user.name}</div>
                    <div className={styles.profileRole}>
                      {user.role || 'General'} · {user.experience_years || 0}y experience
                    </div>
                  </div>
                  <div className={styles.profileActions}>
                    {isActive && (
                      <span className={styles.activeLabel}>Active</span>
                    )}
                    <button
                      className={styles.actionBtn}
                      onClick={() => window.open(`/api/users/${user.id}/profile`, '_blank')}
                    >
                      Profile
                    </button>
                    <button
                      className={styles.actionBtn}
                      onClick={() => window.location.href = '/settings'}
                    >
                      Setup
                    </button>
                    {!isActive && (
                      <button
                        className={`${styles.actionBtn} ${styles.activateBtn}`}
                        onClick={() => handleActivate(user.id)}
                      >
                        Activate
                      </button>
                    )}
                    <button
                      className={styles.deleteBtn}
                      onClick={() => handleDelete(user.id, user.name)}
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

      </div>
    </div>
  )
}
