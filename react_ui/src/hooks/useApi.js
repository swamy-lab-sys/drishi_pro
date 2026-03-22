/**
 * useApi — generic data-fetching hook with loading + error state.
 *
 * Usage:
 *   const { data, loading, error, refetch } = useApi('/api/qa?search=python')
 *   const { data, loading, error, refetch } = useApi('/api/qa', { deps: [tag] })
 */
import { useState, useEffect, useCallback, useRef } from 'react'

export function useApi(url, { deps = [], transform = null, skip = false } = {}) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(!skip)
  const [error,   setError]   = useState(null)
  const abortRef = useRef(null)

  const fetchData = useCallback(async (fetchUrl) => {
    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    setLoading(true)
    setError(null)
    try {
      const r = await fetch(fetchUrl, { signal: ctrl.signal, cache: 'no-store' })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const json = await r.json()
      setData(transform ? transform(json) : json)
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message)
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  useEffect(() => {
    if (!skip && url) fetchData(url)
    return () => abortRef.current?.abort()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, skip, ...deps])

  const refetch = useCallback(() => fetchData(url), [fetchData, url])
  return { data, loading, error, refetch }
}

// ── One-shot API call helper (for mutations: POST/PATCH/DELETE) ───────────────
export async function apiCall(url, { method = 'POST', body = null } = {}) {
  const opts = {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    cache: 'no-store',
  }
  if (body) opts.body = JSON.stringify(body)
  const r = await fetch(url, opts)
  const json = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(json.error || json.message || `HTTP ${r.status}`)
  return json
}
