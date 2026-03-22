import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { ToastProvider } from './components/Toast'
import Layout from './components/Layout'

const Dashboard    = lazy(() => import('./pages/Dashboard'))
const Monitor      = lazy(() => import('./pages/Monitor'))
const Settings     = lazy(() => import('./pages/Settings'))
const QAManager    = lazy(() => import('./pages/QAManager'))
const ExtUsers     = lazy(() => import('./pages/ExtUsers'))
const Profiles     = lazy(() => import('./pages/Profiles'))
const Questions    = lazy(() => import('./pages/Questions'))
const KeywordLookup = lazy(() => import('./pages/KeywordLookup'))
const APIKeys      = lazy(() => import('./pages/APIKeys'))

function Loading() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', color: 'var(--text-muted)',
      fontFamily: 'var(--font-sans)', fontSize: 13,
    }}>
      Loading…
    </div>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <Suspense fallback={<Loading />}>
        <Layout>
          <Routes>
            <Route path="/"           element={<Dashboard />} />
            <Route path="/monitor"    element={<Monitor />} />
            <Route path="/settings"   element={<Settings />} />
            <Route path="/qa-manager" element={<QAManager />} />
            <Route path="/ext-users"  element={<ExtUsers />} />
            <Route path="/profiles"   element={<Profiles />} />
            <Route path="/questions"  element={<Questions />} />
            <Route path="/lookup"     element={<KeywordLookup />} />
            <Route path="/api-keys"   element={<APIKeys />} />
            <Route path="*"           element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </Suspense>
    </ToastProvider>
  )
}
