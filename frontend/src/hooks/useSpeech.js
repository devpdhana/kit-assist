import { useCallback, useEffect, useRef, useState } from 'react'

// Text-to-speech via speechSynthesis (allowed: only *recognition* is banned).
// Speaks sentence by sentence to avoid Chrome's long-utterance cut-off.
export function useSpeech() {
  const [speaking, setSpeaking] = useState(false)
  const voiceRef = useRef(null)

  useEffect(() => {
    const pick = () => {
      const voices = window.speechSynthesis?.getVoices?.() || []
      voiceRef.current =
        voices.find((v) => /en-IN/i.test(v.lang)) ||
        voices.find((v) => /en-GB/i.test(v.lang) && /google|natural/i.test(v.name)) ||
        voices.find((v) => /^en/i.test(v.lang)) ||
        null
    }
    pick()
    window.speechSynthesis?.addEventListener?.('voiceschanged', pick)
    return () => window.speechSynthesis?.removeEventListener?.('voiceschanged', pick)
  }, [])

  const cancel = useCallback(() => {
    window.speechSynthesis?.cancel()
    setSpeaking(false)
  }, [])

  const speak = useCallback((text, onEnd) => {
    if (!window.speechSynthesis || !text) return onEnd?.()
    window.speechSynthesis.cancel()
    const parts = text.match(/[^.!?]+[.!?]*/g) || [text]
    setSpeaking(true)
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p.trim())
      if (voiceRef.current) u.voice = voiceRef.current
      u.lang = voiceRef.current?.lang || 'en-IN'
      u.rate = 1.05
      if (i === parts.length - 1) {
        u.onend = () => {
          setSpeaking(false)
          onEnd?.()
        }
        u.onerror = () => setSpeaking(false)
      }
      window.speechSynthesis.speak(u)
    })
  }, [])

  return { speaking, speak, cancel }
}
