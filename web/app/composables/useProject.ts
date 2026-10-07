/** The current project (from the route), shared by every project page and component: one copy, so a refresh after a
 * change (e.g. new metadata fields) reaches every component that shows it. */
export function useProject() {
  const route = useRoute()
  const pid = computed(() => String(route.params.pid))
  const state = useState<{ pid: string | null, data: any, error: string | null }>('current-project', () => ({ pid: null, data: null, error: null }))
  async function refresh() {
    const id = pid.value
    try {
      const data = await api<any>(`/projects/${id}`)
      if (pid.value === id) state.value = { pid: id, data, error: null }
    }
    catch (e: any) {
      if (pid.value === id) state.value = { pid: id, data: null, error: e.message }
    }
  }
  // The first component to ask for a project loads it; the others see its pid and share the result.
  watch(pid, (id) => {
    if (!route.params.pid || state.value.pid === id) return
    state.value = { pid: id, data: null, error: null }
    refresh()
  }, { immediate: true })
  // A new page of the same project: refresh in the background (counts etc. may have changed), once per page.
  const lastPath = useState<string | null>('current-project-path', () => null)
  watch(() => route.path, (path) => {
    if (!route.params.pid || lastPath.value === path) return
    const fresh = lastPath.value === null
    lastPath.value = path
    if (!fresh && state.value.pid === pid.value && state.value.data) refresh()
  }, { immediate: true })
  const project = computed(() => (state.value.pid === pid.value ? state.value.data : null))
  const error = computed(() => (state.value.pid === pid.value ? state.value.error : null))
  const isReviewer = computed(() => project.value?.role === 'reviewer')
  return { pid, project, error, refresh, isReviewer }
}
