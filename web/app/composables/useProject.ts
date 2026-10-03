/** The current project (from the route), shared by every project page. */
export function useProject() {
  const route = useRoute()
  const pid = computed(() => String(route.params.pid))
  const { data: project, error, refresh } = useApiGet<any>(() => `/projects/${pid.value}`)
  const isReviewer = computed(() => project.value?.role === 'reviewer')
  return { pid, project, error, refresh, isReviewer }
}
