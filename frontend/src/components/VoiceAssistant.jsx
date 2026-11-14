import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useSpeech } from '../hooks/useSpeech'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import AgentTrace from './AgentTrace'

const STATUS = {
  idle: 'Tap the mic and speak',
  listening: 'Listening… (stops when you pause)',
  thinking: 'Thinking…',
  speaking: 'Speaking… tap the mic to interrupt',
}

export default function VoiceAssistant({ mode, greeting, suggestions = [], onUnauthorized }) {
  const [messages, setMessages] = useState(() => [{ who: 'assistant', text: greeting }])
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(null)
  const [note, setNote] = useState('')
  const [handsFree, setHandsFree] = useState(false)
  const [input, setInput] = useState('')
  const logRef = useRef(null)
  const handsFreeRef = useRef(false)
  handsFreeRef.current = handsFree
  const { speaking, speak, cancel } = useSpeech()
  const startRef = useRef(() => {})

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const showReply = useCallback(
    (data) => {
      const latency = ((data.latency_ms || 0) + (data.stt?.ms || 0)) / 1000
      setMessages((m) => [...m, { who: 'assistant', text: data.reply, trace: data.trace, evaluation: data.evaluation, latency }])
      setPending(data.pending)
      speak(data.reply, () => {
        if (handsFreeRef.current) setTimeout(() => startRef.current(), 250)
      })
    },
    [speak],
  )

  const fail = useCallback(
    (err) => {
      if (err.status === 401) return onUnauthorized?.()
      setMessages((m) => [...m, { who: 'assistant', text: err.message || 'Something went wrong.' }])
    },
    [onUnauthorized],
  )

  const sendText = useCallback(
    async (text) => {
      text = String(text || '').trim()
      if (!text || busy) return
      cancel()
      setInput('')
      setNote('')
      setMessages((m) => [...m, { who: 'user', text }])
      setBusy(true)
      try {
        showReply(await api('/api/chat', { method: 'POST', json: { text, mode } }))
      } catch (err) {
        fail(err)
      } finally {
        setBusy(false)
      }
    },
    [busy, cancel, fail, mode, showReply],
  )

  const onRecorded = useCallback(
    async (blob) => {
      setBusy(true)
      setMessages((m) => [...m, { who: 'user', text: '…', placeholder: true }])
      try {
        const data = await api(`/api/voice?mode=${mode}`, {
          method: 'POST',
          body: blob,
          headers: { 'Content-Type': blob.type.split(';')[0] || 'audio/webm' },
        })
        setMessages((m) => {
          const rest = m.filter((x) => !x.placeholder)
          return data.transcript ? [...rest, { who: 'user', text: data.transcript }] : rest
        })
        showReply(data)
      } catch (err) {
        setMessages((m) => m.filter((x) => !x.placeholder))
        if (err.status === 503) setNote('Voice is unavailable - please type below.')
        fail(err)
      } finally {
        setBusy(false)
      }
    },
    [fail, mode, showReply],
  )

  const recorder = useVoiceRecorder({
    onRecorded,
    onNoSpeech: () => {
      setHandsFree(false)
      setNote("I didn't hear anything. Tap the mic to try again.")
    },
    onError: setNote,
  })
  startRef.current = recorder.start

  const onMic = () => {
    setNote('')
    if (speaking) {
      cancel()
      return recorder.start()
    }
    if (recorder.listening) return recorder.stop()
    if (!busy) recorder.start()
  }

  const newConversation = async () => {
    cancel()
    await api('/api/conversation/new', { method: 'POST', json: { mode } }).catch(() => {})
    setPending(null)
    setMessages([{ who: 'assistant', text: 'Started a new conversation. How can I help?' }])
  }

  const state = recorder.listening ? 'listening' : busy ? 'thinking' : speaking ? 'speaking' : 'idle'

  return (
    <div className="assistant" data-state={state}>
      <div className="log" ref={logRef} aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.who}`}>
            <div className="bubble">{m.text}</div>
            {m.latency ? <div className="meta">{m.latency.toFixed(1)}s</div> : null}
            {m.trace && <AgentTrace trace={m.trace} evaluation={m.evaluation} />}
          </div>
        ))}
      </div>

      {pending && (
        <div className="confirm">
          <div>⏳ Waiting for your OK: {pending.question}</div>
          <div className="confirm-btns">
            <button type="button" className="yes" onClick={() => sendText('Yes')}>Yes, go ahead</button>
            <button type="button" onClick={() => sendText('No')}>No</button>
          </div>
        </div>
      )}

      <div className="controls">
        <button type="button" className="mic" style={{ '--level': recorder.level }} onClick={onMic} aria-label="Talk">🎤</button>
        <div>
          <div className="status">{note || STATUS[state]}</div>
          <label className="hf">
            <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} /> Hands-free conversation
          </label>
        </div>
      </div>

      <form className="textrow" onSubmit={(e) => (e.preventDefault(), sendText(input))}>
        <input type="text" value={input} maxLength={500} placeholder="Or type your question…" aria-label="Type a message" onChange={(e) => setInput(e.target.value)} />
        <button type="submit" className="primary" disabled={busy}>Send</button>
      </form>

      <div className="suggestions">
        {suggestions.map((s) => (
          <button key={s} type="button" className="chip" onClick={() => sendText(s)}>{s}</button>
        ))}
      </div>
      <button type="button" className="link" onClick={newConversation}>New conversation</button>
    </div>
  )
}
