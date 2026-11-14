import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import AgentTrace from '../components/AgentTrace'

const when = (s) => new Date(s).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })

export default function History() {
  const [items, setItems] = useState(null)
  const [open, setOpen] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => api('/api/history').then((d) => setItems(d.conversations)).catch((e) => setError(e.message)), [])
  useEffect(() => {
    load()
  }, [load])

  const show = async (id) => setOpen(await api(`/api/history/${encodeURIComponent(id)}`))
  const remove = async (id) => {
    await api(`/api/history/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (open?.id === id) setOpen(null)
    load()
  }

  return (
    <main>
      <div className="grid">
        <section className="card">
          <h2>Past conversations</h2>
          {error && <p className="err">{error}</p>}
          {items === null && <p className="muted">Loading…</p>}
          {items?.length === 0 && <p className="muted">No conversations yet. Talk to the assistant and they will appear here.</p>}
          {items?.map((c) => (
            <div key={c.id} className="history-item">
              <div>
                <button type="button" className="link" onClick={() => show(c.id)}>{c.title}</button>
                <div className="muted">
                  {when(c.updated_at)} · {c.message_count} messages · {c.scope === 'account' ? c.role : 'public page'}
                </div>
              </div>
              <button type="button" className="link danger" onClick={() => remove(c.id)}>Delete</button>
            </div>
          ))}
        </section>
        <section className="card">
          <h2>{open ? open.title : 'Select a conversation'}</h2>
          {open && (
            <div className="assistant">
              <div className="log">
                {open.messages.map((m, i) => (
                  <div key={i} className={`msg ${m.sender}`}>
                    <div className="bubble">{m.text}</div>
                    <div className="meta">{when(m.created_at)}{m.meta?.latency_ms ? ` · ${(m.meta.latency_ms / 1000).toFixed(1)}s` : ''}</div>
                    {m.meta?.trace && <AgentTrace trace={m.meta.trace} evaluation={m.meta.evaluation} />}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
