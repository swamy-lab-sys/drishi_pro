/**
 * Questions — browse and manage the Q&A database in table view.
 * URL: /questions
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar, { TopBtn, TopBadge } from '../components/TopBar'
import styles from './Questions.module.css'

const FILTER_TAGS = ['All', 'Theory', 'Coding', 'SQL', 'Match']

function typeLabel(item) {
  const tags = (item.tags || '').toLowerCase()
  if (item.answer_coding || tags.includes('coding')) return 'Coding'
  if (tags.includes('sql')) return 'SQL'
  if (tags.includes('theory')) return 'Theory'
  return 'General'
}

function typeCls(type, styles) {
  const map = {
    'Coding':  styles.typeCoding,
    'SQL':     styles.typeSQL,
    'Theory':  styles.typeTheory,
    'General': styles.typeGeneral,
  }
  return map[type] || styles.typeGeneral
}

export default function Questions() {
  const toast = useToast()
  const [search, setSearch]         = useState('')
  const [filter, setFilter]         = useState('All')
  const [listParams, setListParams] = useState({ search: '', tag: '' })
  const searchTimer = useRef(null)

  // Debounce search
  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      // 'Match' is client-side filter — don't pass to API
      const tagParam = (filter === 'All' || filter === 'Match') ? '' : filter.toLowerCase()
      setListParams({ search, tag: tagParam })
    }, 280)
    return () => clearTimeout(searchTimer.current)
  }, [search, filter])

  const listUrl = `/api/qa?search=${encodeURIComponent(listParams.search)}&tag=${encodeURIComponent(listParams.tag)}&limit=200`
  const { data: listData, loading, refetch } = useApi(listUrl)

  const rawItems = Array.isArray(listData) ? listData : (listData?.items || [])
  // Apply client-side Match filter (questions with at least one hit)
  const items = filter === 'Match' ? rawItems.filter(i => (i.hit_count || 0) > 0) : rawItems

  // Count by type
  const counts = {
    Theory:  items.filter(i => typeLabel(i) === 'Theory').length,
    Coding:  items.filter(i => typeLabel(i) === 'Coding').length,
    SQL:     items.filter(i => typeLabel(i) === 'SQL').length,
    Match:   items.filter(i => (i.hit_count || 0) > 0).length,
  }

  const totalHits = items.reduce((sum, i) => sum + (i.hit_count || 0), 0)

  const handleDelete = useCallback(async (id) => {
    if (!window.confirm('Delete this question?')) return
    try {
      await apiCall(`/api/qa/${id}`, { method: 'DELETE' })
      toast.success('Deleted')
      refetch()
    } catch (e) {
      toast.error(e.message)
    }
  }, [toast, refetch])

  const formatTime = (ms) => {
    if (ms == null) return '—'
    if (ms < 1000) return `${Math.round(ms)}ms`
    return `${(ms/1000).toFixed(1)}s`
  }

  return (
    <div className={styles.page}>
      <TopBar pageName="Questions">
        <TopBadge variant="gray">{items.length} entries</TopBadge>
        <TopBadge variant="gray">{counts.Match} used</TopBadge>
        <TopBadge variant="gray">{totalHits} hits</TopBadge>
        <TopBtn variant="ghost" onClick={() => {}}>+ CSV</TopBtn>
        <TopBtn variant="green" onClick={() => window.location.href = '/qa-manager'}>
          + Add Q&A
        </TopBtn>
      </TopBar>

      <div className={styles.body}>

        {/* Search */}
        <div className={styles.searchRow}>
          <input
            className={styles.searchInput}
            placeholder="Search questions, keywords…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        {/* Filter chips */}
        <div className={styles.filterRow}>
          {FILTER_TAGS.map(tag => (
            <button
              key={tag}
              className={`${styles.filterChip} ${filter === tag ? styles.active : ''}`}
              onClick={() => setFilter(tag)}
            >
              {tag}
              {tag !== 'All' && counts[tag] != null && (
                <span className={styles.chipCount}>{counts[tag] || 0}</span>
              )}
              {tag === 'All' && (
                <span className={styles.chipCount}>{items.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Meta */}
        <div className={styles.metaRow}>
          {loading ? 'Loading…' : `${items.length} questions`}
        </div>

        {/* Table */}
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th>
                <th>Question</th>
                <th>Type</th>
                <th>Tags</th>
                <th>Hit</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr className={styles.emptyRow}>
                  <td colSpan={6}>{loading ? 'Loading…' : 'No questions found'}</td>
                </tr>
              )}
              {items.map((item, i) => {
                const type = typeLabel(item)
                const tags = (item.tags || '').split(',').map(t => t.trim()).filter(Boolean)
                return (
                  <tr key={item.id}>
                    <td className={styles.numCell}>{i + 1}</td>
                    <td className={styles.questionCell}>
                      <div className={styles.questionText}>{item.question}</div>
                      <div className={styles.tagPills}>
                        {tags.slice(0, 3).map(t => (
                          <span key={t} className={styles.tagPill}>{t}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span className={`${styles.typeBadge} ${typeCls(type, styles)}`}>
                        {type}
                      </span>
                    </td>
                    <td className={styles.hitCell}>
                      {tags.length > 0 && (
                        <span className={styles.hitBadge}>{tags.length}</span>
                      )}
                    </td>
                    <td className={styles.hitCell}>
                      {item.hit_count != null ? item.hit_count : '—'}
                    </td>
                    <td>
                      <div className={styles.actionsCell}>
                        <button
                          className={styles.iconBtn}
                          title="Edit"
                          onClick={() => window.location.href = '/qa-manager'}
                        >
                          ✎
                        </button>
                        <button
                          className={`${styles.iconBtn} ${styles.deleteIcon}`}
                          title="Delete"
                          onClick={() => handleDelete(item.id)}
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

      </div>
    </div>
  )
}
