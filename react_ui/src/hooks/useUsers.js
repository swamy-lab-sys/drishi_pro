/**
 * useUsers — fetches /api/users for the USER dropdown in the terminal bar.
 */
import { useState, useEffect } from 'react'

export function useUsers() {
  const [users, setUsers] = useState([])

  useEffect(() => {
    fetch('/api/users', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : [])
      .then(data => setUsers(Array.isArray(data) ? data : (data.users || [])))
      .catch(() => {})
  }, [])

  return users
}
