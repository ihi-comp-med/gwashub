export default defineNuxtRouteMiddleware(async (to) => {
  if (!to.path.startsWith('/projects')) return
  const { load } = useAuth()
  if (!(await load())) return navigateTo('/login')
})
