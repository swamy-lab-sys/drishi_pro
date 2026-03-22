/**
 * Monitor — real-time answer display for 2nd screen / phone.
 *
 * Modes:
 *   Admin (no token):    SSE from /api/stream → instant streaming
 *   Ext-user (?token=):  Poll /api/answers?user_token=xxx → 500ms delay
 *
 * URL: http://localhost:5173/monitor
 * With token: http://localhost:5173/monitor?token=venkata
 */
import { useEffect, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '../hooks/useSSE'
import { useAnswers } from '../hooks/useAnswers'
import { AnswerCard } from '../components/AnswerCard'
import styles from './Monitor.module.css'

// ── Status indicator pill ─────────────────────────────────────────────────────
function StatusPill({ isStreaming, transcript }) {
  if (transcript) {
    return (
      <div className={`${styles.pill} ${styles.pillListening}`}>
        <span className={styles.dot} />
        {transcript.length > 60 ? transcript.slice(0, 60) + '…' : transcript}
      </div>
    )
  }
  if (isStreaming) {
    return (
      <div className={`${styles.pill} ${styles.pillGenerating}`}>
        <span className={styles.dot} />
        generating…
      </div>
    )
  }
  return (
    <div className={`${styles.pill} ${styles.pillIdle}`}>
      <span className={styles.dot} />
      listening
    </div>
  )
}

// ── Admin mode: SSE real-time ─────────────────────────────────────────────────
function AdminMonitor() {
  const { current, transcript } = useSSE()
  const { question, answer, isStreaming, source, latencyMs } = current
  const hasContent = question || answer

  return (
    <div className={styles.page}>
      <StatusPill isStreaming={isStreaming} transcript={transcript} />
      <div className={styles.feed}>
        {hasContent ? (
          <AnswerCard
            question={question}
            answer={answer}
            isStreaming={isStreaming}
            source={source}
            latencyMs={latencyMs}
          />
        ) : (
          <div className={styles.idle}>
            Waiting for a question…
          </div>
        )}
      </div>
    </div>
  )
}

// ── Ext-user mode: polling ────────────────────────────────────────────────────
function UserMonitor({ token }) {
  const answers = useAnswers(token)

  const latest    = answers[0] || null
  const isStreaming = latest && !latest.is_complete

  return (
    <div className={styles.page}>
      <StatusPill
        isStreaming={isStreaming}
        transcript={isStreaming ? latest?.question || '' : ''}
      />
      <div className={styles.feed}>
        {latest ? (
          <AnswerCard
            question={latest.question}
            answer={latest.answer}
            isStreaming={!latest.is_complete}
            source={latest.metrics?.source?.replace(/^(db|cache|llm|intro).*/, '$1')}
            latencyMs={latest.metrics?.latency_ms}
          />
        ) : (
          <div className={styles.idle}>
            Waiting for a question…
          </div>
        )}
      </div>
    </div>
  )
}

// ── Entry point ───────────────────────────────────────────────────────────────
export default function Monitor() {
  const [params] = useSearchParams()
  const token = params.get('token') || params.get('user_token') || ''

  return token ? <UserMonitor token={token} /> : <AdminMonitor />
}
