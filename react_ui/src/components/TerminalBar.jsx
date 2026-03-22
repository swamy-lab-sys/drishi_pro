/**
 * TerminalBar — dark chip bar at the top of the main dashboard.
 *
 * Replicates the exact API calls from index.html's terminal bar:
 *   STT chips   → POST /api/audio_settings
 *   LLM chips   → POST /api/set_llm_model + /api/launch_config
 *   USER select → POST /api/launch_config
 *   ROLE chips  → POST /api/interview_role
 */
import { useState, useCallback, useEffect } from 'react'
import styles from './TerminalBar.module.css'

// ── API helpers ───────────────────────────────────────────────────────────────
async function postJson(url, body) {
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ── STT options ───────────────────────────────────────────────────────────────
const STT_OPTS = [
  { label: 'tiny',    backend: 'local',    model: 'tiny.en' },
  { label: 'small',   backend: 'local',    model: 'Systran/faster-distil-whisper-small.en' },
  { label: 'sarvam',  backend: 'sarvam',   model: 'sarvam-saarika-v2' },
  { label: 'deepgram',backend: 'deepgram', model: '' },
]

// ── ROLE options ──────────────────────────────────────────────────────────────
const ROLE_OPTS = [
  { label: 'gen',  role: 'general' },
  { label: 'py',   role: 'python' },
  { label: 'java', role: 'java' },
  { label: 'js',   role: 'javascript' },
  { label: 'sql',  role: 'sql' },
  { label: 'saas', role: 'saas' },
]

// ── ChipGroup ─────────────────────────────────────────────────────────────────
function ChipGroup({ label, chips, active, onSelect, variant = 'stt' }) {
  return (
    <div className={styles.group}>
      <span className={styles.label}>{label}</span>
      {chips.map(chip => (
        <button
          key={chip.label}
          className={`${styles.chip} ${
            active === chip.label
              ? variant === 'llm' ? styles.activeLlm : styles.active
              : ''
          }`}
          onClick={() => onSelect(chip)}
        >
          {chip.label}
        </button>
      ))}
    </div>
  )
}

// ── STT live status badge ─────────────────────────────────────────────────────
function SttStatus({ sttPhase }) {
  const { backend, ms, phase } = sttPhase || {}
  if (!backend) return null

  const label = backend === 'local' ? 'whisper' : backend
  let dot, text, cls

  if (phase === 'start') {
    dot = '◌'
    text = `${label} · transcribing…`
    cls = styles.sttBusy
  } else if (phase === 'done') {
    dot = '●'
    text = `${label} · ${ms}ms`
    cls = styles.sttDone
  } else if (phase === 'silent') {
    dot = '·'
    text = `${label} · silent`
    cls = styles.sttSilent
  } else {
    dot = '●'
    text = label
    cls = styles.sttIdle
  }

  return (
    <div className={`${styles.sttStatus} ${cls}`}>
      <span className={phase === 'start' ? styles.dotPulse : styles.dot}>{dot}</span>
      <span>{text}</span>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export function TerminalBar({ sessionInfo, users, launchConfig, sttPhase }) {
  const [sttActive, setSttActive]   = useState(null)
  const [llmActive, setLlmActive]   = useState(null)
  const [roleActive, setRoleActive] = useState(null)
  const [userId,    setUserId]      = useState('')

  // Sync user select from launchConfig (has user_id_override)
  useEffect(() => {
    if (launchConfig?.user_id_override != null) {
      setUserId(String(launchConfig.user_id_override))
    }
  }, [launchConfig])

  // Derive active states from sessionInfo on first load
  const sttLabel  = sttActive  ?? (sessionInfo?.stt  === 'local' ? 'small' : sessionInfo?.stt)   ?? 'small'
  const llmLabel  = llmActive  ?? (sessionInfo?.llm  === 'haiku' ? 'haiku' : sessionInfo?.llm)   ?? 'haiku'
  const roleLabel = roleActive ?? ROLE_OPTS.find(o => o.role === sessionInfo?.mode)?.label ?? 'gen'

  const setSTT = useCallback(async (chip) => {
    setSttActive(chip.label)
    await postJson('/api/audio_settings', {
      stt_backend: chip.backend,
      stt_model:   chip.model,
    })
  }, [])

  const setLLM = useCallback(async (chip) => {
    setLlmActive(chip.label)
    await postJson('/api/set_llm_model', { model: chip.label })
    await postJson('/api/launch_config',  { llm_model: chip.label })
  }, [])

  const setUser = useCallback(async (e) => {
    const id = e.target.value
    setUserId(id)
    await postJson('/api/launch_config', { user_id_override: id })
  }, [])

  const setRole = useCallback(async (chip) => {
    setRoleActive(chip.label)
    await postJson('/api/interview_role', { role: chip.role })
  }, [])

  const avgMs = sessionInfo?.avg_latency_ms
  const latLabel = avgMs != null ? `${Math.round(avgMs)}ms` : '—'

  return (
    <div className={styles.bar}>
      <span className={styles.prompt}>$</span>

      <ChipGroup
        label="STT"
        chips={STT_OPTS}
        active={sttLabel}
        onSelect={setSTT}
        variant="stt"
      />

      <span className={styles.sep}>|</span>

      <ChipGroup
        label="LLM"
        chips={[{ label: 'haiku' }, { label: 'sonnet' }]}
        active={llmLabel}
        onSelect={setLLM}
        variant="llm"
      />

      <span className={styles.sep}>|</span>

      {/* USER dropdown */}
      <div className={styles.group}>
        <span className={styles.label}>USER</span>
        <select className={styles.userSelect} value={userId} onChange={setUser}>
          <option value="">—</option>
          {(users || []).map(u => (
            <option key={u.id} value={String(u.id)}>{u.name}</option>
          ))}
        </select>
      </div>

      <span className={styles.sep}>|</span>

      <ChipGroup
        label="ROLE"
        chips={ROLE_OPTS}
        active={roleLabel}
        onSelect={setRole}
        variant="stt"
      />

      <span className={styles.sep}>|</span>

      {/* Latency */}
      <div className={styles.latency}>
        avg <span className={styles.latVal}>{latLabel}</span>
      </div>

      <span className={styles.sep}>|</span>

      {/* Live STT status */}
      <SttStatus sttPhase={sttPhase} />
    </div>
  )
}
