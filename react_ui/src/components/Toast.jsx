/**
 * Toast — lightweight notification system.
 *
 * Usage:
 *   import { useToast, ToastContainer } from './Toast'
 *   const toast = useToast()
 *   toast.success('Saved!')
 *   toast.error('Failed to save')
 *
 *   // In your app root:
 *   <ToastContainer />
 */
import { useState, useCallback, useEffect, useRef, createContext, useContext } from 'react'
import styles from './Toast.module.css'

const ToastCtx = createContext(null)

let _nextId = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const add = useCallback((msg, type = 'info', duration = 2800) => {
    const id = ++_nextId
    setToasts(t => [...t, { id, msg, type }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), duration)
  }, [])

  const ctx = {
    success: (m) => add(m, 'success'),
    error:   (m) => add(m, 'error'),
    info:    (m) => add(m, 'info'),
  }

  return (
    <ToastCtx.Provider value={ctx}>
      {children}
      <div className={styles.container}>
        {toasts.map(t => (
          <div key={t.id} className={`${styles.toast} ${styles[t.type]}`}>
            {t.type === 'success' && <span>✓</span>}
            {t.type === 'error'   && <span>✕</span>}
            {t.type === 'info'    && <span>ℹ</span>}
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastCtx)
  if (!ctx) throw new Error('useToast must be inside ToastProvider')
  return ctx
}
