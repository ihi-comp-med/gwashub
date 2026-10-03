// Thin client for the GWASHub API + shared display helpers.
export type Role = 'uploader' | 'reviewer'
export type DatasetStatus = 'passed' | 'review' | 'failed' | 'processing' | 'uploading'
export type StepState = 'pass' | 'warn' | 'fail' | 'running' | 'queued' | 'skipped' | 'error'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T = any>(path: string, opts: { method?: string, body?: any, query?: Record<string, any> } = {}): Promise<T> {
  try {
    return await $fetch<T>(`/api${path}`, { method: (opts.method ?? 'GET') as any, body: opts.body, query: opts.query, credentials: 'include' })
  }
  catch (e: any) {
    const status = e?.response?.status ?? 0
    const detail = e?.data?.detail
    const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: any) => d.msg).join('; ') : (e?.message ?? 'Request failed')
    if (status === 401 && !path.startsWith('/auth')) {
      useState<any>('me').value = null
      navigateTo('/login')
    }
    throw new ApiError(status, msg)
  }
}

/** Reactive GET with loading/error state and a refresh(). */
export function useApiGet<T = any>(path: MaybeRefOrGetter<string | null>, opts: { poll?: number } = {}) {
  const data = shallowRef<T | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)
  let timer: ReturnType<typeof setInterval> | undefined
  async function refresh() {
    const p = toValue(path)
    if (!p) return
    loading.value = !data.value
    try {
      data.value = await api<T>(p)
      error.value = null
    }
    catch (e: any) { error.value = e.message }
    finally { loading.value = false }
  }
  watch(() => toValue(path), () => { data.value = null; refresh() }, { immediate: true })
  if (opts.poll) {
    onMounted(() => { timer = setInterval(refresh, opts.poll) })
    onBeforeUnmount(() => clearInterval(timer))
  }
  return { data, error, loading, refresh }
}

export const STATUS_META: Record<DatasetStatus, { label: string, severity: string, icon: string }> = {
  passed: { label: 'Passed QC', severity: 'success', icon: 'pi pi-check-circle' },
  review: { label: 'For review', severity: 'warn', icon: 'pi pi-eye' },
  failed: { label: 'Failed', severity: 'danger', icon: 'pi pi-times-circle' },
  processing: { label: 'Processing', severity: 'info', icon: 'pi pi-spin pi-spinner' },
  uploading: { label: 'Upload not finished', severity: 'secondary', icon: 'pi pi-cloud-upload' },
}

export const STEP_META: Record<StepState, { severity: string, icon: string }> = {
  pass: { severity: 'success', icon: 'pi pi-check' },
  warn: { severity: 'warn', icon: 'pi pi-exclamation-triangle' },
  fail: { severity: 'danger', icon: 'pi pi-times' },
  error: { severity: 'danger', icon: 'pi pi-bolt' },
  running: { severity: 'info', icon: 'pi pi-spin pi-spinner' },
  queued: { severity: 'secondary', icon: 'pi pi-clock' },
  skipped: { severity: 'secondary', icon: 'pi pi-minus' },
}

export const ANCESTRIES = ['EUR', 'AFR', 'EAS', 'SAS', 'AMR', 'Multi'] as const
export const SEXES = ['Combined', 'Female', 'Male'] as const

export const fmtNum = (v: unknown) => typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : (Math.abs(v) < 1e-3 && v !== 0 ? v.toExponential(2) : v.toLocaleString(undefined, { maximumFractionDigits: 4 }))) : String(v ?? '–')
export const fmtDate = (s?: string | null) => s ? new Date(s).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '–'
export const fmtSize = (b?: number) => !b ? '–' : b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`
export const fmtP = (p?: number | null) => p == null ? '–' : p < 1e-3 ? p.toExponential(1) : p.toFixed(3)
export const initials = (n: string) => n.split(' ').map(x => x[0]).join('').slice(0, 2).toUpperCase()
