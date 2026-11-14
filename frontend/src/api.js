// Thin fetch wrapper. Cookies carry the session; the custom header is our CSRF guard.
export async function api(url, { method = 'GET', json, body, headers } = {}) {
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'kit-assist',
      ...(json ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    body: json ? JSON.stringify(json) : body,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(data.detail || `Error ${res.status}`)
    err.status = res.status
    throw err
  }
  return data
}
