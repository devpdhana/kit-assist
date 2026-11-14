import { useEffect, useState } from 'react'
import { api } from '../api'
import VoiceAssistant from '../components/VoiceAssistant'

const inr = (n) => '₹' + Number(n).toLocaleString('en-IN')

export default function Home() {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/api/public/info').then(setInfo).catch(() => setError('Could not load college details right now.'))
  }, [])

  const p = info?.profile
  return (
    <main>
      <section className="hero">
        <h1>{p?.name || 'Knotopian Institute of Technology'}</h1>
        <p className="tagline">{p?.tagline || 'Excellence in Engineering and Applied Technology'}</p>
        {p?.description && <p className="muted">{p.description}</p>}
      </section>
      <div className="grid">
        <section className="card">
          <h2>🎙️ Talk to us</h2>
          <p className="muted">Ask about courses, eligibility, fees, scholarships, dates or documents - or apply for admission just by talking.</p>
          <VoiceAssistant
            mode="public"
            greeting="Hi! I am KIT Assist. Ask me about courses, fees, eligibility or admissions. Tap the mic and talk."
            suggestions={['Which courses do you offer?', 'Am I eligible for CSE with 72%?', 'What documents are needed?', 'I want to apply for admission']}
          />
        </section>
        <div className="stack">
          <section className="card">
            <h2>B.Tech courses 2027-28</h2>
            {error && <p className="err">{error}</p>}
            <table>
              <thead>
                <tr><th>Course</th><th>Min 12th %</th><th>Fee / year</th><th>Seats</th></tr>
              </thead>
              <tbody>
                {(info?.courses || []).map((c) => (
                  <tr key={c.code}>
                    <td>{c.name} <span className="muted">({c.code})</span></td>
                    <td>{c.min_12th_percent}%</td>
                    <td>{inr(c.annual_fee)}</td>
                    <td>{c.seats}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">Hostel fee is extra. Admission is on 12th PCM marks - no entrance exam, no application fee.</p>
          </section>
          {p?.contact && (
            <section className="card">
              <h2>Contact</h2>
              <p className="muted">{p.contact.address} · {p.contact.phone} · {p.contact.email}</p>
            </section>
          )}
        </div>
      </div>
    </main>
  )
}
