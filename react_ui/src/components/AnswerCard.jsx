/**
 * AnswerCard — renders one Q/A pair.
 *
 * Props:
 *   question    string   — the question text
 *   answer      string   — answer text (may be partial if isStreaming)
 *   isStreaming boolean  — show blinking cursor
 *   source      string   — 'db' | 'llm' | 'cache' | 'intro'
 *   latencyMs   number   — answer latency in ms
 *   num         number   — card number (1-indexed)
 *   timestamp   string   — ISO timestamp
 */
import { useMemo, useCallback } from 'react'
import styles from './AnswerCard.module.css'

// ── Inline markdown: **bold** and `code` ─────────────────────────────────────
function inlineHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`\n]+)`/g, '<code class="ic">$1</code>')
}

// ── Answer renderer: bullets, code blocks, paragraphs ────────────────────────
function renderAnswer(text) {
  if (!text) return null

  const blocks = []
  const lines  = text.split('\n')

  let bulletGroup = []
  let inCode      = false
  let codeLang    = ''
  let codeLines   = []

  function flushBullets() {
    if (!bulletGroup.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} className={styles.bulletList}>
        {bulletGroup.map((item, i) => (
          <li key={i} className={styles.bulletItem}
            dangerouslySetInnerHTML={{ __html: inlineHtml(item) }} />
        ))}
      </ul>
    )
    bulletGroup = []
  }

  function flushCode() {
    if (!codeLines.length) return
    const code = codeLines.join('\n')
    blocks.push(
      <div key={`code-${blocks.length}`} className={styles.codeBlock}>
        {codeLang && (
          <div className={styles.codeHead}>
            <span className={styles.codeLang}>{codeLang.toUpperCase()}</span>
            <button
              className={styles.codeCopy}
              onClick={() => navigator.clipboard?.writeText(code)}
            >copy</button>
          </div>
        )}
        <pre className={styles.codePre}><code>{code}</code></pre>
      </div>
    )
    codeLines = []
    codeLang  = ''
    inCode    = false
  }

  for (const line of lines) {
    // Code fence start/end
    if (line.startsWith('```')) {
      if (inCode) {
        flushCode()
      } else {
        flushBullets()
        inCode   = true
        codeLang = line.slice(3).trim()
      }
      continue
    }

    if (inCode) {
      codeLines.push(line)
      continue
    }

    // Bullet line
    if (line.startsWith('- ') || line.startsWith('• ')) {
      bulletGroup.push(line.slice(2))
      continue
    }

    // Non-bullet: flush bullet group first
    flushBullets()

    if (line.trim() === '') {
      blocks.push(<div key={`sp-${blocks.length}`} className={styles.spacer} />)
      continue
    }

    blocks.push(
      <p key={`p-${blocks.length}`} className={styles.para}
        dangerouslySetInnerHTML={{ __html: inlineHtml(line) }} />
    )
  }

  flushBullets()
  if (inCode) flushCode()

  return blocks
}

// ── Source badge ─────────────────────────────────────────────────────────────
const SOURCE_LABEL = { db: 'DB', llm: 'LLM', cache: 'CACHE', intro: 'INTRO' }

function SourceBadge({ source }) {
  if (!source) return null
  return (
    <span className={`${styles.srcBadge} ${styles['src_' + source] || ''}`}>
      {SOURCE_LABEL[source] || source.toUpperCase()}
    </span>
  )
}

// ── Copy button ───────────────────────────────────────────────────────────────
function CopyBtn({ text }) {
  const copy = useCallback(() => {
    navigator.clipboard?.writeText(text)
  }, [text])
  return (
    <button className={styles.copyBtn} onClick={copy} title="Copy answer">
      copy
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export function AnswerCard({ question, answer, isStreaming, source, latencyMs, num, timestamp }) {
  const rendered = useMemo(() => renderAnswer(answer), [answer])

  const timeStr = useMemo(() => {
    if (!timestamp) return ''
    try {
      return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    } catch { return '' }
  }, [timestamp])

  const latStr = latencyMs
    ? latencyMs >= 1000 ? `${(latencyMs / 1000).toFixed(1)}s` : `${latencyMs}ms`
    : null

  return (
    <div className={`${styles.card} ${isStreaming ? styles.active : ''}`}>
      {/* ── Header ── */}
      <div className={styles.head}>
        {num != null && <span className={styles.num}>{num}</span>}
        <span className={styles.qtext}>{question}</span>
        <div className={styles.headRight}>
          {timeStr && <span className={styles.time}>{timeStr}</span>}
          {!isStreaming && answer && <CopyBtn text={answer} />}
        </div>
      </div>

      {/* ── Body ── */}
      <div className={styles.body}>
        {rendered}
        {isStreaming && (
          answer
            ? <span className={styles.cursor} aria-hidden>▋</span>
            : <ThinkingDots />
        )}
      </div>

      {/* ── Footer ── */}
      {!isStreaming && (source || latStr) && (
        <div className={styles.foot}>
          <SourceBadge source={source} />
          {latStr && <span className={styles.latency}>{latStr}</span>}
        </div>
      )}
    </div>
  )
}

function ThinkingDots() {
  return (
    <div className={styles.thinking} aria-label="Thinking">
      <span /><span /><span />
    </div>
  )
}
