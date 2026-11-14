import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { AuthProvider } from './auth'
import { useAuth } from './useAuth'
import Assistant from './pages/Assistant'
import History from './pages/History'
import Home from './pages/Home'
import Login from './pages/Login'

function Header() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  return (
    <header className="top">
      <Link className="logo" to="/">KIT</Link>
      <nav>
        <Link to="/history">History</Link>
        {user ? (
          <>
            <Link to="/assistant">My assistant</Link>
            <span className="badge">{user.full_name} · {user.role}</span>
            <button type="button" className="link" onClick={() => logout().then(() => navigate('/'))}>Log out</button>
          </>
        ) : (
          <Link to="/login">Student / Teacher login</Link>
        )}
      </nav>
    </header>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Header />
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/history" element={<History />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
