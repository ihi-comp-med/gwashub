export interface Me { id: number, email: string, name: string, institution: string, is_admin: boolean }

export function useAuth() {
  const me = useState<Me | null>('me', () => null)
  const checked = useState('me-checked', () => false)

  async function load() {
    if (checked.value) return me.value
    try { me.value = await api<Me>('/me') }
    catch { me.value = null }
    checked.value = true
    return me.value
  }
  async function login(email: string, password = '') {
    const r = await api<{ projects: string[] }>('/auth/login', { method: 'POST', body: { email, password } })
    checked.value = false
    await load()
    navigateTo(r.projects.length === 1 ? `/projects/${r.projects[0]}` : '/projects')
  }
  async function logout() {
    const r = await api<{ logout_url?: string }>('/auth/logout', { method: 'POST' })
    me.value = null
    // Cognito: also end the hosted sign-in session, which then returns to /login.
    if (r.logout_url) window.location.href = r.logout_url
    else navigateTo('/login')
  }
  return { me, load, login, logout }
}
