import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { AuthContext } from './useAuth'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined) // undefined = loading, null = visitor

  const refresh = useCallback(() => api('/api/me').then((d) => setUser(d.user)).catch(() => setUser(null)), [])
  useEffect(() => {
    refresh()
  }, [refresh])

  const login = async (login_id, password) => {
    const d = await api('/api/auth/login', { method: 'POST', json: { login_id, password } })
    setUser(d.user)
  }
  const logout = async () => {
    await api('/api/auth/logout', { method: 'POST' })
    setUser(null)
  }
  return <AuthContext.Provider value={{ user, login, logout, refresh }}>{children}</AuthContext.Provider>
}
