/**
 * useAnswers — polls /api/answers for the full session answer history.
 *
 * Used in two modes:
 *   1. Admin mode (no token): polls /api/answers every 2s for history list
 *   2. Ext-user mode (token set): polls /api/answers?user_token=xxx every 500ms
 *      for per-user isolated answers (faster for real-time feel)
 *
 * Response shape: [{question, answer, timestamp, is_complete, metrics, _num}]
 */
import { useState, useEffect, useRef } from 'react'

export function useAnswers(userToken = '') {
  const [answers, setAnswers] = useState([])
  const isExtUser = Boolean(userToken)
  const pollMs    = isExtUser ? 500 : 2000
  // Track last seen question to detect new answers
  const lastQRef  = useRef('')

  useEffect(() => {
    const url = isExtUser
      ? `/api/answers?user_token=${encodeURIComponent(userToken)}`
      : '/api/answers'

    let cancelled = false

    async function poll() {
      if (cancelled) return
      try {
        const r = await fetch(url, { cache: 'no-store' })
        if (!r.ok || cancelled) return
        const data = await r.json()
        const list = Array.isArray(data) ? data : (data.answers || [])
        setAnswers(list)
        if (list.length > 0) lastQRef.current = list[0].question || ''
      } catch {}
    }

    poll()
    const id = setInterval(poll, pollMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [userToken, isExtUser, pollMs])

  return answers
}
