/**
 * KeywordLookup — browse Q&A database by keyword/tag.
 * Shows keyword cards with counts, click to filter results.
 * URL: /lookup
 */
import { useState, useMemo } from 'react'
import { useApi } from '../hooks/useApi'
import TopBar, { TopBadge } from '../components/TopBar'
import styles from './KeywordLookup.module.css'

export default function KeywordLookup() {
  const [search, setSearch]         = useState('')
  const [selectedKeyword, setSelectedKeyword] = useState(null)

  // Fetch all QA entries to extract keywords
  const { data: listData, loading } = useApi('/api/qa?limit=500')

  const items = useMemo(() => {
    if (!listData) return []
    return Array.isArray(listData) ? listData : (listData?.items || [])
  }, [listData])

  // Build keyword frequency map from tags
  const keywordMap = useMemo(() => {
    const map = new Map()
    items.forEach(item => {
      const tags = (item.tags || '').split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
      // Also extract words from keywords field if exists
      const kw = (item.keywords || '').split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
      const combined = [...new Set([...tags, ...kw])]
      combined.forEach(k => {
        if (k.length < 2) return
        map.set(k, (map.get(k) || 0) + 1)
      })
    })
    return map
  }, [items])

  // Sort keywords by count descending
  const sortedKeywords = useMemo(() => {
    return [...keywordMap.entries()]
      .sort((a, b) => b[1] - a[1])
      .filter(([k]) => !search || k.includes(search.toLowerCase()))
  }, [keywordMap, search])

  // Filter results by selected keyword
  const filteredResults = useMemo(() => {
    if (!selectedKeyword) return []
    const kw = selectedKeyword.toLowerCase()
    return items.filter(item => {
      const tags = (item.tags || '').toLowerCase()
      const kws = (item.keywords || '').toLowerCase()
      return tags.includes(kw) || kws.includes(kw) || item.question?.toLowerCase().includes(kw)
    })
  }, [items, selectedKeyword])

  const handleKeywordClick = (kw) => {
    setSelectedKeyword(prev => prev === kw ? null : kw)
  }

  return (
    <div className={styles.page}>
      <TopBar pageName="Keyword Lookup">
        <TopBadge variant="gray">{sortedKeywords.length} Keywords</TopBadge>
      </TopBar>

      <div className={styles.body}>

        {/* Search input */}
        <input
          className={styles.searchInput}
          placeholder="Filter keywords…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        {/* Loading */}
        {loading && <div className={styles.loadingMsg}>Loading keywords…</div>}

        {/* Keyword cards */}
        {!loading && sortedKeywords.length === 0 && (
          <div className={styles.empty}>No keywords found</div>
        )}

        <div className={styles.grid}>
          {sortedKeywords.slice(0, 60).map(([kw, count]) => (
            <div
              key={kw}
              className={`${styles.keywordCard} ${selectedKeyword === kw ? styles.selected : ''}`}
              onClick={() => handleKeywordClick(kw)}
            >
              <div className={styles.keywordCount}>{count}</div>
              <div className={styles.keywordText}>{kw}</div>
              <div className={styles.keywordLabel}>entries</div>
            </div>
          ))}
        </div>

        {/* Results for selected keyword */}
        {selectedKeyword && (
          <>
            <div className={styles.resultsHeader}>
              Results for "{selectedKeyword}"
              <span className={styles.resultCount}>{filteredResults.length} questions</span>
            </div>

            {filteredResults.length === 0 && (
              <div className={styles.empty}>No questions found for this keyword</div>
            )}

            {filteredResults.map(item => {
              const tags = (item.tags || '').split(',').map(t => t.trim()).filter(Boolean)
              return (
                <div key={item.id} className={styles.resultItem}>
                  <div className={styles.resultQ}>{item.question}</div>
                  <div className={styles.resultMeta}>
                    {tags.slice(0, 4).map(t => (
                      <span
                        key={t}
                        className={styles.resultTag}
                        onClick={e => { e.stopPropagation(); handleKeywordClick(t.toLowerCase()) }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )
            })}
          </>
        )}

      </div>
    </div>
  )
}
