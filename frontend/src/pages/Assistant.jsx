import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../useAuth'
import DocumentsCard from '../components/DocumentsCard'
import VoiceAssistant from '../components/VoiceAssistant'

const STUDENT = [
  "What's my attendance?",
  'Can I take leave on Monday?',
  'Apply on-duty for a symposium on 30 November',
  'What classes do I have on Thursday?',
  'Status of my requests',
  'Explain BCNF with an example',
  'What circulars are there?',
]
const TEACHER = ['Show pending requests', 'Approve request 68', 'What is the on-duty proof deadline?', 'Show attendance for my class']

export default function Assistant() {
  const { user, refresh } = useAuth()
  const navigate = useNavigate()
  if (user === undefined) return <main className="narrow"><p className="muted">Loading…</p></main>
  if (!user) return <Navigate to="/login" replace />

  const first = user.full_name.split(' ')[0]
  const teacher = user.role === 'teacher'
  return (
    <main className="narrow wide">
      <section className="card">
        <h2>Hi {first}!</h2>
        <p className="muted">
          Your personal college assistant. A supervisor agent routes each request to the right specialist agents, checks the rulebook before
          submitting anything, and always asks before acting.
        </p>
        <VoiceAssistant
          mode="private"
          greeting={
            teacher
              ? `Hello ${user.full_name}. I can show and decide your students' leave and on-duty requests, and answer rulebook questions.`
              : `Hello ${first}. Ask me about attendance, timetable, rules, leave or on-duty, placements or your subjects.`
          }
          suggestions={teacher ? TEACHER : STUDENT}
          onUnauthorized={() => refresh().then(() => navigate('/login'))}
        />
      </section>
      <DocumentsCard title="Your documents" />
    </main>
  )
}
