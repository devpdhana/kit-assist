import { useCallback, useRef, useState } from 'react'

// Records the mic with MediaRecorder (NOT the Web Speech recognition API) and
// stops automatically when the speaker pauses (simple energy-based VAD).
const SILENCE_MS = 900
const NO_SPEECH_MS = 7000
const MAX_MS = 30000

export function useVoiceRecorder({ onRecorded, onNoSpeech, onError }) {
  const [listening, setListening] = useState(false)
  const [level, setLevel] = useState(0)
  const streamRef = useRef(null)
  const ctxRef = useRef(null)
  const recRef = useRef(null)

  const stop = useCallback(() => {
    if (recRef.current?.state === 'recording') recRef.current.stop()
  }, [])

  const start = useCallback(async () => {
    try {
      streamRef.current ??= await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
    } catch {
      onError?.('Microphone blocked - please allow mic access, or type below.')
      return
    }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || ''
    const chunks = []
    const rec = new MediaRecorder(streamRef.current, mime ? { mimeType: mime } : undefined)
    recRef.current = rec
    let spoke = false
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
    rec.onstop = () => {
      setListening(false)
      setLevel(0)
      if (!spoke) return onNoSpeech?.()
      onRecorded(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }))
    }
    rec.start(250)
    setListening(true)

    // ---- VAD ----
    ctxRef.current ??= new (window.AudioContext || window.webkitAudioContext)()
    const src = ctxRef.current.createMediaStreamSource(streamRef.current)
    const analyser = ctxRef.current.createAnalyser()
    analyser.fftSize = 1024
    src.connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    const t0 = performance.now()
    let lastVoice = t0
    let noise = 0.01
    const tick = () => {
      if (rec.state !== 'recording') return src.disconnect()
      analyser.getFloatTimeDomainData(buf)
      let sum = 0
      for (const x of buf) sum += x * x
      const rms = Math.sqrt(sum / buf.length)
      const now = performance.now()
      if (now - t0 < 300) noise = Math.max(noise, rms) // calibrate on room noise
      setLevel(Math.min(1, rms * 12))
      if (rms > Math.max(0.015, noise * 2.2)) {
        spoke = true
        lastVoice = now
      }
      if ((spoke && now - lastVoice > SILENCE_MS) || (!spoke && now - t0 > NO_SPEECH_MS) || now - t0 > MAX_MS) {
        return rec.stop()
      }
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, [onRecorded, onNoSpeech, onError])

  return { listening, level, start, stop }
}
