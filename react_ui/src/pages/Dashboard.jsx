/**
 * Dashboard — main interview screen.
 *
 * Layout:
 *   TopBar (breadcrumb + session controls)
 *   TerminalBar (STT/LLM/ROLE chips)
 *   SearchBar (filter displayed answers)
 *   TranscriptBar (live STT text)
 *   AnswerFeed (streaming current + history)
 *
 * URL: /
 */
import { useMemo, useState } from 'react'
import { useSSE }         from '../hooks/useSSE'
import { useAnswers }     from '../hooks/useAnswers'
import { useSessionInfo } from '../hooks/useSessionInfo'
import { useUsers }       from '../hooks/useUsers'
import { useApi }         from '../hooks/useApi'
import { TerminalBar }    from '../components/TerminalBar'
import { AnswerCard }     from '../components/AnswerCard'
import TopBar, { TopBadge, TopBtn } from '../components/TopBar'
import styles from './Dashboard.module.css'

// ── Transcript bar ────────────────────────────────────────────────────────────
function TranscriptBar({ text }) {
  if (!text) return null
  return (
    <div className={styles.transcriptBar}>
      <span className={styles.micIcon}>🎤</span>
      <span className={styles.transcriptText}>{text}</span>
    </div>
  )
}

// ── Stats bar ─────────────────────────────────────────────────────────────────
function StatsBar({ info }) {
  if (!info) return null
  const latMs = info.avg_latency_ms
  const latColor = latMs == null ? '' : latMs < 500 ? styles.green : latMs < 2000 ? styles.amber : styles.red
  return (
    <div className={styles.statsBar}>
      {info.user_name && (
        <span className={styles.stat}>
          <span className={styles.statLabel}>user</span> {info.user_name}
        </span>
      )}
      {info.stt && (
        <span className={styles.stat}>
          <span className={styles.statLabel}>stt</span> {info.stt}
        </span>
      )}
      {info.db_count != null && (
        <span className={styles.stat}>
          <span className={styles.statLabel}>db</span> {info.db_count}q
        </span>
      )}
      {info.cache_hits != null && (
        <span className={styles.stat}>
          <span className={styles.statLabel}>cache</span> {info.cache_hits}%
        </span>
      )}
      {latMs != null && (
        <span className={`${styles.stat} ${latColor}`}>
          <span className={styles.statLabel}>avg</span>{' '}
          {latMs >= 1000 ? `${(latMs/1000).toFixed(1)}s` : `${Math.round(latMs)}ms`}
        </span>
      )}
    </div>
  )
}

// ── Answer feed ───────────────────────────────────────────────────────────────
function AnswerFeed({ current, history, searchQuery }) {
  const { question, answer, isStreaming, source, latencyMs } = current
  const hasCurrentQ = Boolean(question)

  const q = searchQuery.toLowerCase().trim()

  // Build display list: streaming current first, then history (skip duplicate)
  const historyFiltered = useMemo(() => {
    let h = hasCurrentQ ? history.filter(h => h.question !== question) : history
    if (q) {
      h = h.filter(item =>
        item.question?.toLowerCase().includes(q) ||
        item.answer?.toLowerCase().includes(q)
      )
    }
    return h
  }, [history, question, hasCurrentQ, q])

  const showCurrent = hasCurrentQ && (!q || question.toLowerCase().includes(q) || answer?.toLowerCase().includes(q))

  return (
    <div className={styles.feed}>
      {/* Current streaming answer */}
      {showCurrent && (
        <AnswerCard
          key="current"
          question={question}
          answer={answer}
          isStreaming={isStreaming}
          source={source}
          latencyMs={latencyMs}
        />
      )}

      {/* History */}
      {historyFiltered.map((a, i) => {
        const src = (a.metrics?.source || '').replace(/^(db|cache|llm|intro).*/, '$1')
        return (
          <AnswerCard
            key={`${a.question}-${i}`}
            num={historyFiltered.length - i}
            question={a.question}
            answer={a.answer}
            isStreaming={false}
            source={src}
            latencyMs={a.metrics?.latency_ms}
            timestamp={a.timestamp}
          />
        )
      })}

      {!hasCurrentQ && history.length === 0 && (
        <div className={styles.empty}>
          Waiting for the interview to start…
        </div>
      )}

      {hasCurrentQ || history.length > 0 ? (
        q && historyFiltered.length === 0 && !showCurrent ? (
          <div className={styles.empty}>No answers match "{searchQuery}"</div>
        ) : null
      ) : null}
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { current, transcript, sttPhase } = useSSE()
  const history                 = useAnswers()
  const sessionInfo             = useSessionInfo()
  const users                   = useUsers()
  const { data: launchConfig }  = useApi('/api/launch_config')
  const [searchQuery, setSearchQuery] = useState('')
  const [paused, setPaused]     = useState(false)
  const [clearing, setClearing] = useState(false)

  const isActive = Boolean(current?.question || history.length > 0)

  async function handleClearSession() {
    if (!window.confirm('Clear all answers from this session?')) return
    setClearing(true)
    try {
      await fetch('/api/clear_session', { method: 'POST' })
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className={styles.page}>
      {/* Top bar */}
      <TopBar pageName="Interview">
        <TopBadge variant={isActive ? 'green' : 'gray'}>
          {isActive ? 'Active Session' : 'Idle'}
        </TopBadge>
        <TopBtn
          variant={paused ? 'ghost' : 'green'}
          onClick={() => setPaused(p => !p)}
        >
          {paused ? 'Resume' : 'Pause'}
        </TopBtn>
        <TopBtn variant="ghost" onClick={handleClearSession} disabled={clearing}>
          {clearing ? 'Clearing…' : 'Clear'}
        </TopBtn>
      </TopBar>

      {/* Terminal chip bar */}
      <TerminalBar sessionInfo={sessionInfo} users={users} launchConfig={launchConfig} sttPhase={sttPhase} />

      {/* Stats */}
      <StatsBar info={sessionInfo} />

      {/* Search bar */}
      <div className={styles.searchWrap}>
        <input
          className={styles.searchInput}
          type="text"
          placeholder="Search interview answers…"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
        />
        {searchQuery && (
          <button className={styles.searchClear} onClick={() => setSearchQuery('')}>✕</button>
        )}
      </div>

      {/* Live transcript */}
      <TranscriptBar text={transcript} />

      {/* Answer feed */}
      <AnswerFeed current={current} history={history} searchQuery={searchQuery} />
    </div>
  )
}
