/**
 * Settings page — mirrors Flask /settings.
 * Sections: Audio & STT · Launch Config · Interview Role · System Health
 */
import { useState, useEffect, useCallback } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar from '../components/TopBar'
import styles from './Settings.module.css'

const ROLES    = ['general', 'python', 'java', 'javascript', 'sql', 'saas', 'system_design']
const ROLE_LABELS = { general: 'gen', python: 'py', java: 'java', javascript: 'js', sql: 'sql', saas: 'saas', system_design: 'sys' }
const STT_BACKENDS = ['local', 'deepgram', 'sarvam', 'assemblyai']
const STT_MODELS   = [
  'tiny.en', 'base.en', 'small.en', 'medium.en',
  'Systran/faster-distil-whisper-small.en',
  'Systran/faster-distil-whisper-medium.en',
  'Systran/faster-whisper-large-v3',
]
const LLM_MODELS   = [
  { id: 'claude-haiku-4-5-20251001', label: 'haiku' },
  { id: 'claude-sonnet-4-6',         label: 'sonnet' },
]
const AUDIO_SOURCES = [
  { id: 'system',    label: 'System mic' },
  { id: 'extension', label: 'Extension' },
]
const SILENCE_PRESETS = [
  { label: 'Fast',   value: 0.6 },
  { label: 'Normal', value: 1.2 },
  { label: 'Slow',   value: 2.0 },
]

function Section({ title, children }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{title}</div>
      {children}
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <div className={styles.control}>{children}</div>
    </div>
  )
}

function Chips({ options, value, onSelect, getLabel, getId }) {
  return (
    <div className={styles.chips}>
      {options.map(opt => {
        const id  = getId ? getId(opt) : opt
        const lbl = getLabel ? getLabel(opt) : opt
        return (
          <button
            key={id}
            className={`${styles.chip} ${value === id ? styles.chipActive : ''}`}
            onClick={() => onSelect(id)}
          >
            {lbl}
          </button>
        )
      })}
    </div>
  )
}

function HealthDot({ ok }) {
  return <span className={ok ? styles.dotGreen : styles.dotRed} />
}

export default function Settings() {
  const toast = useToast()

  // ── Data fetches ────────────────────────────────────────────────────────────
  const { data: audioData,  refetch: refetchAudio }  = useApi('/api/audio_settings')
  const { data: launchData, refetch: refetchLaunch } = useApi('/api/launch_config')
  const { data: roleData,   refetch: refetchRole }   = useApi('/api/interview_role')
  const { data: healthData, refetch: refetchHealth } = useApi('/api/system/health')
  const { data: usersData }                          = useApi('/api/users')

  // ── Local form state ────────────────────────────────────────────────────────
  const [silence,    setSilence]    = useState(1.2)
  const [maxDur,     setMaxDur]     = useState(15)
  const [sttBackend, setSttBackend] = useState('local')
  const [sttModel,   setSttModel]   = useState('Systran/faster-distil-whisper-small.en')
  const [audioSrc,   setAudioSrc]   = useState('system')
  const [llmModel,   setLlmModel]   = useState('claude-haiku-4-5-20251001')
  const [userId,     setUserId]     = useState('')
  const [role,       setRole]       = useState('general')
  const [saving,     setSaving]     = useState(null)

  // ── Sync fetched data → local state ─────────────────────────────────────────
  useEffect(() => {
    if (!audioData) return
    setSilence(audioData.silence_duration ?? 1.2)
    setMaxDur(audioData.max_duration ?? 15)
    setSttBackend(audioData.stt_backend ?? 'local')
    setSttModel(audioData.stt_model ?? 'Systran/faster-distil-whisper-small.en')
  }, [audioData])

  useEffect(() => {
    if (!launchData) return
    setAudioSrc(launchData.audio_source ?? 'system')
    setLlmModel(launchData.llm_model ?? 'claude-haiku-4-5-20251001')
    setUserId(String(launchData.user_id_override ?? ''))
  }, [launchData])

  useEffect(() => {
    if (roleData?.role) setRole(roleData.role)
  }, [roleData])

  // ── Poll health every 5s ─────────────────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(refetchHealth, 5000)
    return () => clearInterval(id)
  }, [refetchHealth])

  // ── Savers ──────────────────────────────────────────────────────────────────
  const saveAudio = useCallback(async (patch) => {
    setSaving('audio')
    try {
      await apiCall('/api/audio_settings', { method: 'POST', body: patch })
      toast.success('Audio settings saved')
      refetchAudio()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(null)
    }
  }, [toast, refetchAudio])

  const saveLaunch = useCallback(async (patch) => {
    setSaving('launch')
    try {
      await apiCall('/api/launch_config', { method: 'POST', body: patch })
      toast.success('Config saved')
      refetchLaunch()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(null)
    }
  }, [toast, refetchLaunch])

  const saveLlm = useCallback(async (model) => {
    setSaving('llm')
    try {
      await apiCall('/api/set_llm_model', { method: 'POST', body: { model } })
      await apiCall('/api/launch_config', { method: 'POST', body: { llm_model: model } })
      setLlmModel(model)
      toast.success(`LLM → ${model.includes('haiku') ? 'haiku' : 'sonnet'}`)
      refetchLaunch()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(null)
    }
  }, [toast, refetchLaunch])

  const saveRole = useCallback(async (r) => {
    setSaving('role')
    try {
      await apiCall('/api/interview_role', { method: 'POST', body: { role: r } })
      setRole(r)
      toast.success(`Role → ${r}`)
      refetchRole()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(null)
    }
  }, [toast, refetchRole])

  const handleSilencePreset = (v) => {
    setSilence(v)
    saveAudio({ silence_duration: v })
  }

  const handleSilenceBlur = () => saveAudio({ silence_duration: silence })
  const handleMaxDurBlur  = () => saveAudio({ max_duration: maxDur })

  const handleSttBackend = (b) => {
    setSttBackend(b)
    saveAudio({ stt_backend: b })
  }

  const handleSttModel = (m) => {
    setSttModel(m)
    saveAudio({ stt_model: m })
  }

  const handleAudioSrc = (s) => {
    setAudioSrc(s)
    saveLaunch({ audio_source: s })
  }

  const handleUser = (e) => {
    const v = e.target.value
    setUserId(v)
    saveLaunch({ user_id_override: v })
  }

  const health = healthData || {}
  const cpu    = health.cpu    ?? '--'
  const ram    = health.ram    ?? '--'
  const sttOk  = health.stt_status  === 'ok'
  const llmOk  = health.llm_status  === 'ok'

  return (
    <div className={styles.page}>
      <TopBar pageName="Settings" />
      <div className={styles.body}>

        {/* ── Audio & STT ──────────────────────────────────────────────────── */}
        <Section title="Audio & STT">
          <Row label="STT backend">
            <Chips
              options={STT_BACKENDS}
              value={sttBackend}
              onSelect={handleSttBackend}
            />
          </Row>

          {sttBackend === 'local' && (
            <Row label="Whisper model">
              <select
                className={styles.select}
                value={sttModel}
                onChange={e => handleSttModel(e.target.value)}
              >
                {STT_MODELS.map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Row>
          )}

          <Row label="Silence threshold">
            <div className={styles.sliderRow}>
              <input
                type="range"
                className={styles.slider}
                min="0.4" max="3.0" step="0.1"
                value={silence}
                onChange={e => setSilence(Number(e.target.value))}
                onMouseUp={handleSilenceBlur}
                onTouchEnd={handleSilenceBlur}
              />
              <span className={styles.sliderVal}>{silence.toFixed(1)}s</span>
            </div>
            <div className={styles.presets}>
              {SILENCE_PRESETS.map(p => (
                <button
                  key={p.label}
                  className={`${styles.preset} ${silence === p.value ? styles.presetActive : ''}`}
                  onClick={() => handleSilencePreset(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </Row>

          <Row label="Max duration">
            <div className={styles.sliderRow}>
              <input
                type="range"
                className={styles.slider}
                min="5" max="30" step="1"
                value={maxDur}
                onChange={e => setMaxDur(Number(e.target.value))}
                onMouseUp={handleMaxDurBlur}
                onTouchEnd={handleMaxDurBlur}
              />
              <span className={styles.sliderVal}>{maxDur}s</span>
            </div>
          </Row>

          <Row label="Audio source">
            <Chips
              options={AUDIO_SOURCES}
              value={audioSrc}
              onSelect={handleAudioSrc}
              getId={o => o.id}
              getLabel={o => o.label}
            />
          </Row>
        </Section>

        {/* ── LLM Model ────────────────────────────────────────────────────── */}
        <Section title="LLM Model">
          <Row label="Model">
            <Chips
              options={LLM_MODELS}
              value={llmModel}
              onSelect={saveLlm}
              getId={o => o.id}
              getLabel={o => o.label}
            />
          </Row>

          <Row label="Active user">
            <select
              className={styles.select}
              value={userId}
              onChange={handleUser}
            >
              <option value="">— default —</option>
              {(usersData || []).map(u => (
                <option key={u.id} value={String(u.id)}>{u.name}</option>
              ))}
            </select>
          </Row>
        </Section>

        {/* ── Interview Role ───────────────────────────────────────────────── */}
        <Section title="Interview Role">
          <Row label="Role">
            <Chips
              options={ROLES}
              value={role}
              onSelect={saveRole}
              getLabel={r => ROLE_LABELS[r] || r}
            />
          </Row>
        </Section>

        {/* ── System Health ────────────────────────────────────────────────── */}
        <Section title="System Health">
          <Row label="CPU">
            <span className={styles.statVal}>{cpu}%</span>
          </Row>
          <Row label="RAM">
            <span className={styles.statVal}>{ram}%</span>
          </Row>
          <Row label="STT">
            <HealthDot ok={sttOk} />
            <span className={styles.healthLabel}>{sttOk ? 'ready' : 'not ready'}</span>
          </Row>
          <Row label="LLM">
            <HealthDot ok={llmOk} />
            <span className={styles.healthLabel}>{llmOk ? 'ready' : 'not ready'}</span>
          </Row>
          <Row label="">
            <button className={styles.refreshBtn} onClick={refetchHealth}>
              refresh
            </button>
          </Row>
        </Section>

      </div>
    </div>
  )
}
