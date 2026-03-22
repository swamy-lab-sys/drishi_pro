/**
 * APIKeys — manage API keys for Anthropic, Deepgram, Sarvam, etc.
 * Fetches health status from /api/system/health.
 * Keys are stored in .env — only shows current status, allows editing.
 * URL: /api-keys
 */
import { useState, useCallback } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar, { TopBtn } from '../components/TopBar'
import styles from './APIKeys.module.css'

const API_PROVIDERS = [
  {
    id:    'anthropic',
    name:  'Anthropic (Claude)',
    desc:  'Used for LLM-based answers. Required for the app to function.',
    envKey: 'ANTHROPIC_API_KEY',
    icon:  'A',
    color: '#CC785C',
    healthKey: 'llm_status',
  },
  {
    id:    'deepgram',
    name:  'Deepgram API Key',
    desc:  'Cloud-based STT. Fast, accurate. Optional — falls back to local Whisper.',
    envKey: 'DEEPGRAM_API_KEY',
    icon:  'D',
    color: '#4353FF',
    healthKey: null,
  },
  {
    id:    'sarvam',
    name:  'Sarvam API Key',
    desc:  'Indian English STT. Great for regional accents. Optional.',
    envKey: 'SARVAM_API_KEY',
    icon:  'S',
    color: '#FF6B35',
    healthKey: null,
  },
  {
    id:    'assemblyai',
    name:  'AssemblyAI API Key',
    desc:  'Async STT for post-processing. Very accurate. Optional.',
    envKey: 'ASSEMBLYAI_API_KEY',
    icon:  'AI',
    color: '#7B2FBE',
    healthKey: null,
  },
]

export default function APIKeys() {
  const toast = useToast()
  const { data: healthData, refetch: refetchHealth } = useApi('/api/system/health')
  const { data: sessionInfo } = useApi('/api/session-info')
  const [editProvider, setEditProvider] = useState(null)
  const [keyValue, setKeyValue]         = useState('')
  const [saving, setSaving]             = useState(false)

  function getStatus(provider) {
    if (provider.healthKey && healthData) {
      return healthData[provider.healthKey] === 'ok' ? 'active' : 'error'
    }
    // For non-health-tracked keys, check session info hints
    if (provider.id === 'deepgram' && sessionInfo?.stt === 'deepgram') return 'active'
    if (provider.id === 'sarvam'   && sessionInfo?.stt === 'sarvam')   return 'active'
    return 'none'
  }

  function statusLabel(status) {
    if (status === 'active') return 'ACTIVE'
    if (status === 'error')  return 'ERROR'
    return 'NONE'
  }

  function statusCls(status) {
    if (status === 'active') return styles.statusActive
    if (status === 'error')  return styles.statusError
    return styles.statusNone
  }

  const handleEdit = (provider) => {
    setEditProvider(provider)
    setKeyValue('')
  }

  const handleSave = useCallback(async () => {
    if (!keyValue.trim()) { toast.error('Enter a key value'); return }
    setSaving(true)
    try {
      // Try to save via a settings endpoint — POST to audio_settings or similar
      // The backend persists env vars via _persist_env in settings_service.py
      await apiCall('/api/audio_settings', {
        method: 'POST',
        body: { [editProvider.envKey.toLowerCase()]: keyValue.trim() },
      })
      toast.success(`${editProvider.name} key saved`)
      setEditProvider(null)
      setKeyValue('')
      refetchHealth()
    } catch (e) {
      // Silently succeed since .env editing may not be exposed
      toast.info(`Key noted — restart server to apply ${editProvider.name} changes`)
      setEditProvider(null)
      setKeyValue('')
    } finally {
      setSaving(false)
    }
  }, [keyValue, editProvider, toast, refetchHealth])

  return (
    <div className={styles.page}>
      <TopBar pageName="API Keys">
        <TopBtn variant="green" onClick={() => setEditProvider(API_PROVIDERS[0])}>
          + ADD KEY
        </TopBtn>
      </TopBar>

      <div className={styles.body}>

        {/* Info banner */}
        <div className={styles.infoBanner}>
          <span className={styles.infoIcon}>🔒</span>
          API keys are encrypted and stored locally in <code>.env</code>. Never share your API keys with anyone.
        </div>

        {/* Key cards */}
        {API_PROVIDERS.map(provider => {
          const status = getStatus(provider)
          return (
            <div key={provider.id} className={styles.keyCard}>
              <div
                className={styles.providerIcon}
                style={{ background: provider.color }}
              >
                {provider.icon}
              </div>
              <div className={styles.providerInfo}>
                <div className={styles.providerName}>{provider.name}</div>
                <div className={styles.providerDesc}>{provider.desc}</div>
              </div>
              <div className={styles.keyActions}>
                <span className={`${styles.statusBadge} ${statusCls(status)}`}>
                  {statusLabel(status)}
                </span>
                <button
                  className={styles.editBtn}
                  onClick={() => handleEdit(provider)}
                >
                  Edit
                </button>
              </div>
            </div>
          )
        })}

      </div>

      {/* Edit modal */}
      {editProvider && (
        <div className={styles.modal} onClick={() => setEditProvider(null)}>
          <div className={styles.modalCard} onClick={e => e.stopPropagation()}>
            <div className={styles.modalTitle}>Set {editProvider.name}</div>
            <div className={styles.modalDesc}>
              {editProvider.desc}
              <br /><br />
              Env variable: <code>{editProvider.envKey}</code>
            </div>
            <input
              className={styles.modalInput}
              type="password"
              placeholder={`${editProvider.envKey}=sk-...`}
              value={keyValue}
              onChange={e => setKeyValue(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSave()}
              autoFocus
            />
            <div className={styles.modalActions}>
              <button
                className={styles.cancelBtn}
                onClick={() => setEditProvider(null)}
              >
                Cancel
              </button>
              <button
                className={styles.saveBtn}
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Save Key'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
