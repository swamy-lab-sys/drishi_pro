/**
 * QA Manager — browse, search, edit, add and delete Q&A pairs.
 * Left panel: searchable list with tag filter.
 * Right panel: editor for the selected entry.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useApi, apiCall } from '../hooks/useApi'
import { useToast } from '../components/Toast'
import TopBar, { TopBtn } from '../components/TopBar'
import styles from './QAManager.module.css'

const BLANK = { question: '', answer_theory: '', answer_coding: '', tags: '' }

function TagBadge({ tag }) {
  return <span className={styles.tag}>{tag}</span>
}

function ListItem({ item, selected, onClick }) {
  const tags = (item.tags || '').split(',').map(t => t.trim()).filter(Boolean)
  return (
    <div
      className={`${styles.item} ${selected ? styles.itemActive : ''}`}
      onClick={() => onClick(item)}
    >
      <div className={styles.itemQ}>{item.question}</div>
      <div className={styles.itemMeta}>
        {tags.slice(0, 3).map(t => <TagBadge key={t} tag={t} />)}
        {tags.length > 3 && <span className={styles.tagMore}>+{tags.length - 3}</span>}
      </div>
    </div>
  )
}

export default function QAManager() {
  const toast = useToast()

  const [search,   setSearch]   = useState('')
  const [tag,      setTag]      = useState('')
  const [selected, setSelected] = useState(null)   // {id, question, answer, tags} | null
  const [form,     setForm]     = useState(BLANK)
  const [isNew,    setIsNew]    = useState(false)
  const [saving,   setSaving]   = useState(false)
  const [deleting, setDeleting] = useState(false)
  const searchTimer = useRef(null)

  // ── Fetch list ───────────────────────────────────────────────────────────────
  const [listParams, setListParams] = useState({ search: '', tag: '' })
  const listUrl = `/api/qa?search=${encodeURIComponent(listParams.search)}&tag=${encodeURIComponent(listParams.tag)}&limit=200`
  const { data: listData, loading: listLoading, refetch: refetchList } = useApi(listUrl)

  // ── Fetch tags ───────────────────────────────────────────────────────────────
  const { data: tagsData } = useApi('/api/qa/tags')
  // tags API returns {tag: count, ...} — extract sorted keys
  const allTags = tagsData
    ? (Array.isArray(tagsData) ? tagsData : Object.keys(tagsData).sort())
    : []

  // Debounce search
  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setListParams({ search, tag })
    }, 280)
    return () => clearTimeout(searchTimer.current)
  }, [search, tag])

  // Sync selected → form
  useEffect(() => {
    if (selected) {
      setForm({
        question:      selected.question      || '',
        answer_theory: selected.answer_theory || '',
        answer_coding: selected.answer_coding || '',
        tags:          selected.tags          || '',
      })
      setIsNew(false)
    }
  }, [selected])

  const handleNew = () => {
    setSelected(null)
    setForm(BLANK)
    setIsNew(true)
  }

  const handleSelect = (item) => {
    setSelected(item)
    setIsNew(false)
  }

  const handleFormChange = (field, value) => {
    setForm(f => ({ ...f, [field]: value }))
  }

  const handleSave = async () => {
    if (!form.question.trim() || (!form.answer_theory.trim() && !form.answer_coding.trim())) {
      toast.error('Question and at least one answer field are required')
      return
    }
    setSaving(true)
    try {
      if (isNew) {
        const res = await apiCall('/api/qa', { method: 'POST', body: { ...form, force: true } })
        toast.success('Entry added')
        refetchList()
        setIsNew(false)
        setSelected({ ...form, id: res.id })
      } else if (selected) {
        await apiCall(`/api/qa/${selected.id}`, { method: 'PUT', body: form })
        toast.success('Saved')
        setSelected({ ...selected, ...form })
        refetchList()
      }
    } catch (e) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!selected) return
    if (!window.confirm('Delete this entry?')) return
    setDeleting(true)
    try {
      await apiCall(`/api/qa/${selected.id}`, { method: 'DELETE' })
      toast.success('Deleted')
      setSelected(null)
      setForm(BLANK)
      refetchList()
    } catch (e) {
      toast.error(e.message)
    } finally {
      setDeleting(false)
    }
  }

  const items = Array.isArray(listData) ? listData : (listData?.items || [])
  const total = listData?.total ?? items.length

  return (
    <div className={styles.page}>
      <TopBar pageName="QA Database">
        <TopBtn variant="green" onClick={handleNew}>+ New</TopBtn>
      </TopBar>
      <div className={styles.body}>

        {/* ── Left panel ──────────────────────────────────────────────────── */}
        <div className={styles.left}>
          <div className={styles.listHeader}>
            <input
              className={styles.searchInput}
              placeholder="Search…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <button className={styles.newBtn} onClick={handleNew}>+ New</button>
          </div>

          <div className={styles.tagBar}>
            <button
              className={`${styles.tagFilter} ${tag === '' ? styles.tagFilterActive : ''}`}
              onClick={() => setTag('')}
            >
              all
            </button>
            {allTags.slice(0, 20).map(t => {
              const name = typeof t === 'string' ? t : t.tag || t.name || String(t)
              return (
                <button
                  key={name}
                  className={`${styles.tagFilter} ${tag === name ? styles.tagFilterActive : ''}`}
                  onClick={() => setTag(name)}
                >
                  {name}
                </button>
              )
            })}
          </div>

          <div className={styles.listMeta}>
            {listLoading ? 'loading…' : `${items.length} entries`}
          </div>

          <div className={styles.list}>
            {items.map(item => (
              <ListItem
                key={item.id}
                item={item}
                selected={selected?.id === item.id}
                onClick={handleSelect}
              />
            ))}
            {!listLoading && items.length === 0 && (
              <div className={styles.empty}>No results</div>
            )}
          </div>
        </div>

        {/* ── Right panel ─────────────────────────────────────────────────── */}
        <div className={styles.right}>
          {(selected || isNew) ? (
            <>
              <div className={styles.editorHeader}>
                <span className={styles.editorTitle}>
                  {isNew ? 'New entry' : `#${selected?.id}`}
                </span>
                <div className={styles.editorActions}>
                  {!isNew && (
                    <button
                      className={styles.deleteBtn}
                      onClick={handleDelete}
                      disabled={deleting}
                    >
                      {deleting ? 'deleting…' : 'delete'}
                    </button>
                  )}
                  <button
                    className={styles.saveBtn}
                    onClick={handleSave}
                    disabled={saving}
                  >
                    {saving ? 'saving…' : 'save'}
                  </button>
                </div>
              </div>

              <div className={styles.fields}>
                <div className={styles.fieldGroup}>
                  <label className={styles.fieldLabel}>Question</label>
                  <textarea
                    className={styles.textarea}
                    rows={3}
                    value={form.question}
                    onChange={e => handleFormChange('question', e.target.value)}
                    placeholder="Interview question…"
                  />
                </div>

                <div className={styles.fieldGroup}>
                  <label className={styles.fieldLabel}>Answer (theory)</label>
                  <textarea
                    className={`${styles.textarea} ${styles.textareaAnswer}`}
                    rows={10}
                    value={form.answer_theory}
                    onChange={e => handleFormChange('answer_theory', e.target.value)}
                    placeholder="Theory answer…"
                  />
                </div>

                <div className={styles.fieldGroup}>
                  <label className={styles.fieldLabel}>Answer (code)</label>
                  <textarea
                    className={`${styles.textarea} ${styles.textareaCode}`}
                    rows={6}
                    value={form.answer_coding}
                    onChange={e => handleFormChange('answer_coding', e.target.value)}
                    placeholder="Code answer (optional)…"
                  />
                </div>

                <div className={styles.fieldGroup}>
                  <label className={styles.fieldLabel}>Tags (comma-separated)</label>
                  <input
                    className={styles.input}
                    value={form.tags}
                    onChange={e => handleFormChange('tags', e.target.value)}
                    placeholder="python, oop, decorators"
                  />
                </div>
              </div>
            </>
          ) : (
            <div className={styles.placeholder}>
              Select an entry to edit, or click <strong>+ New</strong>
            </div>
          )}
        </div>

      </div>
    </div>
  )
}
