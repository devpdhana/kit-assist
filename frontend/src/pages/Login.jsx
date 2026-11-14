import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ login_id: '', password: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/assistant" replace />

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await login(form.login_id, form.password)
      navigate('/assistant')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="narrow">
      <section className="card">
        <h2>Student / Teacher login</h2>
        <form onSubmit={submit}>
          <label htmlFor="login_id">Login ID</label>
          <input id="login_id" type="text" autoComplete="username" required value={form.login_id} onChange={(e) => setForm({ ...form, login_id: e.target.value })} />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          {error && <p className="err" role="alert">{error}</p>}
          <button type="submit" className="primary full" disabled={busy}>{busy ? 'Logging in…' : 'Log in'}</button>
        </form>
        <p className="muted">Demo accounts: students arjun, priya, kavya, rahul… · teachers lakshmi, senthil</p>
      </section>
    </main>
  )
}
