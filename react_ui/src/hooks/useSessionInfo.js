/**
 * useSessionInfo — polls /api/session-info for runtime stats.
 *
 * Returns: {user_name, user_role, stt, llm, avg_latency_ms, db_count,
 *           cache_hits, confidence, elapsed, mode}
 */
import { useState, useEffect } from 'react'

export function useSessionInfo(pollMs = 3000) {
  const [info, setInfo] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function poll() {
      if (cancelled) return
      try {
        const r = await fetch('/api/session-info', { cache: 'no-store' })
        if (r.ok && !cancelled) setInfo(await r.json())
      } catch {}
    }

    poll()
    const id = setInterval(poll, pollMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [pollMs])

  return info
}
