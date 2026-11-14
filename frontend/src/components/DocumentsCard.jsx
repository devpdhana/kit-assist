import { useEffect, useState } from 'react'
import { api } from '../api'

const TYPE_LABEL = { handbook: 'Handbook', circular: 'Circular', brochure: 'Brochure', lesson_notes: 'Lesson notes', notice: 'Notice' }

// Official documents with trust status. PDFs are served through our backend
// proxy (/api/documents/{id}/file) so the college API key never reaches the browser.
// Pass `documents` to render a known list, or omit it to fetch the user's list.
export default function DocumentsCard({ documents: given, title = 'Official documents' }) {
  const [fetched, setFetched] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (given) return
    let cancelled = false
    api('/api/documents')
      .then((d) => !cancelled && setFetched(d.documents))
      .catch(() => !cancelled && setError('Could not load documents.'))
    return () => {
      cancelled = true
    }
  }, [given])

  const docs = given || fetched
  return (
    <section className="card">
      <h2>{title}</h2>
      {error && <p className="err">{error}</p>}
      {docs === null && !error && <p className="muted">Loading…</p>}
      {docs?.length === 0 && <p className="muted">No documents published yet.</p>}
      <ul className="docs">
        {docs?.map((d) => (
          <li key={d.id} className={d.status.startsWith('superseded') ? 'superseded' : ''}>
            <a href={d.download} target="_blank" rel="noopener noreferrer">{d.title}</a>
            <div className="doc-meta">
              <span className="badge">{TYPE_LABEL[d.type] || d.type}</span>
              <span className={`badge status-${d.status.split(' ')[0]}`}>{d.status}</span>
              {d.is_new && <span className="badge new">new</span>}
              {d.applies_to_you === true && <span className="badge">applies to you</span>}
              {d.effective_date && <span className="muted">from {d.effective_date}</span>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
