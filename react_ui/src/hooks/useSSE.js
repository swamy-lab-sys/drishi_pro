/**
 * useSSE — connects to /api/stream (SSE) and provides real-time answer state.
 *
 * SSE events from event_bus.py:
 *   'init'         → {session_id, answers[]}   — stream connected
 *   'question'     → {question}                — new question detected
 *   'chunk'        → {q, c}                    — token chunk (q=question, c=chunk)
 *   'answer'       → {question, answer, is_complete, metrics}  — final answer
 *   'transcribing' → {text}                    — live STT text
 *   'status'       → {msg}                     — pipeline status message
 *   'ping'         → (no data)                 — keep-alive
 */
import { useState, useEffect, useRef, useCallback } from 'react'

const RETRY_INIT = 500
const RETRY_MAX  = 8000

export function useSSE() {
  const [current, setCurrent] = useState({
    question: '',
    answer: '',
    source: '',
    isStreaming: false,
    latencyMs: null,
  })
  const [transcript, setTranscript] = useState('')
  const [status, setStatus] = useState('')
  const [sessionId, setSessionId] = useState(null)
  // STT live status: { backend, ms, phase: 'start'|'done'|'silent'|'error' }
  const [sttPhase, setSttPhase] = useState({ backend: '', ms: 0, phase: 'idle' })

  // Chunk buffer — batched per requestAnimationFrame to avoid per-token re-renders
  const chunkBufRef = useRef('')
  const rafRef      = useRef(null)
  const retryRef    = useRef(RETRY_INIT)
  const esRef       = useRef(null)

  const flushChunks = useCallback(() => {
    if (chunkBufRef.current) {
      const buf = chunkBufRef.current
      chunkBufRef.current = ''
      setCurrent(prev => ({ ...prev, answer: prev.answer + buf }))
    }
    rafRef.current = null
  }, [])

  const scheduleFlush = useCallback(() => {
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(flushChunks)
    }
  }, [flushChunks])

  useEffect(() => {
    let destroyed = false

    function connect() {
      if (destroyed) return

      const es = new EventSource('/api/stream')
      esRef.current = es

      es.addEventListener('init', e => {
        retryRef.current = RETRY_INIT
        try {
          const d = JSON.parse(e.data)
          setSessionId(d.session_id || null)
        } catch {}
      })

      es.addEventListener('question', e => {
        try {
          const d = JSON.parse(e.data)
          chunkBufRef.current = ''
          if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
          setCurrent({
            question: d.question || '',
            answer: '',
            source: '',
            isStreaming: true,
            latencyMs: null,
          })
        } catch {}
      })

      es.addEventListener('chunk', e => {
        try {
          const d = JSON.parse(e.data)
          chunkBufRef.current += (d.c || d.chunk || '')
          scheduleFlush()
        } catch {}
      })

      es.addEventListener('answer', e => {
        try {
          const d = JSON.parse(e.data)
          // Flush any remaining buffered chunks first
          if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
          chunkBufRef.current = ''
          const metrics = d.metrics || {}
          const src = (metrics.source || '').replace(/^(db|cache|llm|intro).*/, '$1')
          setCurrent({
            question: d.question || '',
            answer: d.answer || '',
            source: src,
            isStreaming: false,
            latencyMs: metrics.latency_ms || null,
          })
        } catch {}
      })

      es.addEventListener('transcribing', e => {
        try {
          const d = JSON.parse(e.data)
          setTranscript(d.text || '')
        } catch {}
      })

      es.addEventListener('status', e => {
        try {
          const d = JSON.parse(e.data)
          setStatus(d.msg || '')
          setTimeout(() => setStatus(''), 3000)
        } catch {}
      })

      es.addEventListener('stt', e => {
        try {
          const d = JSON.parse(e.data)
          setSttPhase({ backend: d.backend || '', ms: d.ms || 0, phase: d.phase || 'idle' })
        } catch {}
      })

      // ping — no-op, just resets the retry counter
      es.addEventListener('ping', () => {
        retryRef.current = RETRY_INIT
      })

      es.onerror = () => {
        es.close()
        if (!destroyed) {
          setTimeout(connect, retryRef.current)
          retryRef.current = Math.min(retryRef.current * 2, RETRY_MAX)
        }
      }
    }

    connect()

    return () => {
      destroyed = true
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      if (esRef.current) esRef.current.close()
    }
  }, [scheduleFlush])

  return { current, transcript, status, sessionId, sttPhase }
}
