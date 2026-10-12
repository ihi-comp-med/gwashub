<script setup lang="ts">
// Right-hand drawer (80% width): QC tab (steps, outputs, plots, lead loci, review) and Details tab
// (descriptive fields + study metadata, editable by the uploader or a reviewer, with an audit trail).
const props = defineProps<{ project: any, datasetId: number | null }>()
const emit = defineEmits<{ changed: [] }>()
const visible = defineModel<boolean>('visible', { default: false })
const toast = useToast()
const isReviewer = computed(() => props.project.role === 'reviewer')

const d = ref<any>(null)
const plotData = ref<any>(null)
const leads = ref<any[]>([])
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  if (!props.datasetId) return
  try {
    d.value = await api(`/datasets/${props.datasetId}`)
    if (d.value.status !== 'processing') {
      clearInterval(timer)
      timer = undefined
      await loadArtifacts()
    }
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not load dataset', detail: e.message, life: 4000 }) }
}
async function loadArtifacts() {
  plotData.value = null
  leads.value = []
  const steps = d.value?.run?.steps ?? []
  const plots = steps.find((s: any) => s.artifacts?.some((a: any) => a.path === 'plot_data.json'))
  if (plots) plotData.value = await api(`/datasets/${d.value.id}/files/artifact`, { query: { path: `${plots.dir}/plot_data.json` } }).catch(() => null)
  const stats = steps.find((s: any) => s.artifacts?.some((a: any) => a.path === 'lead_loci.tsv'))
  if (stats) {
    const tsv = await $fetch<string>(`/api/datasets/${d.value.id}/files/artifact`, { query: { path: `${stats.dir}/lead_loci.tsv` }, responseType: 'text' }).catch(() => '')
    const [head, ...lines] = tsv.trim().split('\n')
    const cols = head?.split('\t') ?? []
    leads.value = lines.filter(Boolean).map(l => Object.fromEntries(l.split('\t').map((v, i) => [cols[i], v])))
  }
}
watch(() => [visible.value, props.datasetId], () => {
  clearInterval(timer)
  d.value = null
  if (visible.value && props.datasetId) {
    load()
    timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
  }
}, { immediate: true })
onBeforeUnmount(() => clearInterval(timer))

const steps = computed(() => d.value?.run?.steps ?? [])
const fileUrl = (kind: string) => `/api/datasets/${d.value?.id}/files/${kind}`
const dur = (s?: number) => s == null ? '' : s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
const srcLabel = (s: any) => !s.source ? '' : s.source.repo === 'gwashub' ? s.source.path : `${String(s.source.repo).split('/').slice(-2).join('/')}@${String(s.source.commit).slice(0, 7)} · ${s.source.path}`
const note = ref('')
const busy = ref(false)
const overriding = ref(false)
// Processing steps: one line each (title, verdict); expanded, the step log laid out as the step contract.
const openSteps = ref(new Set<number>())
function toggleStep(i: number) {
  const n = new Set(openSteps.value)
  n.has(i) ? n.delete(i) : n.add(i)
  openSteps.value = n
  const s = steps.value[i]
  if (n.has(i) && s?.dir && d.value?.run?.id && !stepFiles.value[s.dir]) loadStepFiles(s.dir)
}
watch(() => props.datasetId, () => { openSteps.value = new Set(); stepFiles.value = {} })
const stepFiles = ref<Record<string, { path: string, size: number }[]>>({})  // sizes of each step's files, once expanded
async function loadStepFiles(dir: string) {
  const files = await api<any[]>(`/datasets/${d.value.id}/runs/${d.value.run.id}/files`, { query: { dir } }).catch(() => [])
  stepFiles.value = { ...stepFiles.value, [dir]: files }
}
const fileSize = (s: any, path: string) => stepFiles.value[s.dir]?.find(f => f.path === `${s.dir}/${path}`)?.size
const metricLabel = (k: string) => k.replaceAll('_', ' ')
const metricValue = (k: string, v: unknown) => typeof v === 'number' && k.endsWith('_fraction') && v >= 0 && v <= 1 ? `${(100 * v).toFixed(2)}%` : fmtNum(v)
const rowsLine = (s: any) => {
  if (s.rows_after == null) return ''
  if (s.rows_before == null) return `${s.rows_after.toLocaleString()} rows`
  const gone = s.rows_before - s.rows_after
  return `${s.rows_before.toLocaleString()} → ${s.rows_after.toLocaleString()}` + (gone > 0 ? ` (−${gone.toLocaleString()}, ${(100 * gone / Math.max(s.rows_before, 1)).toFixed(2)}%)` : gone < 0 ? ` (+${(-gone).toLocaleString()})` : ' (none removed)')
}
// A step's parameters as the run recorded them (pipeline/runner/executor.py used_params): one "label: value" each.
function fmtParam(p: any): string {
  const v = p.value
  if (p.type === 'range' && v && typeof v === 'object') {
    const lim = [v.min_on ? `min ${v.min}` : '', v.max_on ? `max ${v.max}` : ''].filter(Boolean).join(', ')
    return v.on ? (lim || 'on') : `off${lim ? ` (when on: ${lim})` : ''}`
  }
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return v === null || v === undefined || v === '' ? '–' : String(v)
}
// How long ago the running step's output tail was published (it refreshes about every 15 seconds).
function ago(iso: string) {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000))
  return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`
}
const ranStep = (s: any) => !['queued', 'running', 'skipped'].includes(s.state)
const ART_ICON: Record<string, string> = { image: 'pi pi-image', table: 'pi pi-table', text: 'pi pi-align-left', report: 'pi pi-file', html: 'pi pi-file' }
const artUrl = (s: any, a: any, download = false) => `/api/datasets/${d.value?.id}/files/artifact?path=${encodeURIComponent(`${s.dir}/${a.path}`)}${d.value?.run?.id ? `&run_id=${d.value.run.id}` : ''}${download ? '&download=1' : ''}`

// Changes requested: the uploader (or a reviewer) uploads a corrected file in place of this one. It replaces the old
// file, which is deleted, and QC starts.
const fileInput = ref<HTMLInputElement>()
const replacing = ref(false)
const replaceProgress = ref(0)
async function replaceFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f) return
  if (!window.confirm(`Replace ${d.value.file_name} with ${f.name}?\n\nThe current file will be deleted. This can't be undone. QC starts as soon as the upload finishes.`)) return
  replacing.value = true
  replaceProgress.value = 0
  try {
    const id = d.value.id
    const start = await api<{ part_size: number, parts: number }>(`/datasets/${id}/replacement`, { method: 'POST', body: { file_name: f.name, file_size: f.size } })
    const parts = await uploadInParts(f, {
      partSize: start.part_size, parts: start.parts, onProgress: (x) => { replaceProgress.value = Math.round(99 * x) },
      urlFor: async n => (await api<{ urls: Record<string, string> }>(`/datasets/${id}/replacement/urls`, { method: 'POST', body: { parts: [n] } })).urls[String(n)]!,
    })
    await api(`/datasets/${id}/replacement/complete`, { method: 'POST', body: { parts } })
    toast.add({ severity: 'success', summary: 'File replaced: QC started', life: 4000 })
    await load()
    clearInterval(timer)
    timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not replace the file', detail: e.message, life: 7000 }) }
  finally { replacing.value = false }
}

async function decide(decision: string) {
  busy.value = true
  try {
    await api(`/datasets/${d.value.id}/review`, { method: 'POST', body: { decision, note: note.value } })
    toast.add({ severity: 'success', summary: { approve: 'Approved', reject: 'Rejected', request_changes: 'Changes requested' }[decision], life: 2500 })
    note.value = ''
    overriding.value = false
    await load()
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Review failed', detail: e.message, life: 4000 }) }
  finally { busy.value = false }
}
async function rerun() {
  const first = d.value.status === 'pending'
  try {
    await api(`/datasets/${d.value.id}/rerun`, { method: 'POST' })
    toast.add({ severity: 'info', summary: first ? 'QC started' : 'QC re-run started', detail: 'Using the current pipeline version.', life: 2500 })
    await load()
    clearInterval(timer)
    timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: first ? 'Could not start QC' : 'Re-run failed', detail: e.message, life: 4000 }) }
}

// QC pipeline: the dataset follows the project default unless a reviewer pins it to another (re-run advised, not automatic).
const pipelineOptions = computed(() => (props.project.pipelines ?? []).map((p: any) => ({ value: p.is_default ? 0 : p.id, label: p.is_default ? `${p.name} (project default)` : p.name })))
const pipelineChoice = computed(() => d.value?.pipeline?.pinned ? d.value.pipeline.pipeline_id : 0)
async function setPipeline(id: number) {
  try {
    await api(`/datasets/${d.value.id}`, { method: 'PATCH', body: { pipeline_id: id } })
    toast.add({ severity: 'warn', summary: 'Pipeline changed', detail: 'Re-run QC to use it.', life: 4000 })
    await load()
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not change the pipeline', detail: e.message, life: 4000 }) }
}

async function stopQc() {
  if (!window.confirm('Stop QC on this dataset? The run is recorded as stopped and the dataset goes back to pending; start QC again when you are ready.')) return
  try {
    await api(`/datasets/${d.value.id}/stop`, { method: 'POST' })
    toast.add({ severity: 'info', summary: 'QC stopped', life: 2500 })
    clearInterval(timer)
    await load()
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not stop QC', detail: e.message, life: 4000 }) }
}
async function remove() {
  if (!window.confirm(`Delete ${d.value.cohort} · ${d.value.phenotype} · ${d.value.ancestry} · ${d.value.sex}? The uploaded file and all QC results are removed. This cannot be undone.`)) return
  try {
    await api(`/datasets/${d.value.id}`, { method: 'DELETE' })
    toast.add({ severity: 'success', summary: 'Dataset deleted', life: 2500 })
    visible.value = false
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 4000 }) }
}

const codeOpen = ref(false)
const codeKey = ref<string | null>(null)
function viewCode(key: string) { codeKey.value = key; codeOpen.value = true }

// --- Step output viewer ---------------------------------------------------------------
const outOpen = ref(false)
const outStep = ref<any>(null)
const outFocus = ref<string | null>(null)
function viewOutput(s: any, focus: string | null = null) { outStep.value = s; outFocus.value = focus; outOpen.value = true }

// --- Details tab: edit descriptive fields + study metadata -------------------------------
const tab = ref('qc')
const editing = ref(false)
const form = reactive<any>({})
// While a run waits on AWS Batch, the step about to run carries a note (the API's jobs.batch_note); the pill is its gist.
const notePill = (note: string) => note.startsWith('Re-queued') ? 'Re-queued' : note.startsWith('Attempt') ? 'Retrying'
  : note.startsWith('Starting') ? 'Starting' : 'Waiting for a machine' 
// The edit form's phenotype decides which counts are asked for: cases and controls, or the total (quantitative traits).
const formQuantitative = computed(() => props.project.phenotype_types?.[form.phenotype] === 'quantitative')
const metaForm = ref<Record<string, any>>({})
const { schema } = useMetadataSchema()
const { data: cohortList } = useApiGet<any[]>(() => visible.value ? `/projects/${props.project.id}/cohorts` : null)
const cohortNames = computed(() => (cohortList.value ?? []).map((c: any) => c.name))
const cohortSuggestions = ref<string[]>([])
const phenoOptions = computed(() => (props.project.phenotypes ?? []).map((p: string) => ({ value: p, label: props.project.phenotype_descriptions?.[p] ? `${p} · ${props.project.phenotype_descriptions[p]}` : p })))
// QC metrics for the table above the plots: the headline ones, plus LDSC detail when it ran.
const qcRows = computed(() => {
  const q = d.value?.qc ?? {}
  const out: { label: string, value: string, help?: string }[] = []
  for (const c of QC_COLUMNS) if (q[c.key] != null) out.push({ label: c.label, value: fmtNum(q[c.key]), help: c.help })
  if (q.ldsc_intercept_se != null) out.push({ label: 'LDSC int. SE', value: fmtNum(q.ldsc_intercept_se) })
  if (q.ldsc_ratio != null) out.push({ label: 'LDSC ratio', value: fmtNum(q.ldsc_ratio), help: '(intercept − 1) / (mean χ² − 1)' })
  if (q.rows_out != null) out.push({ label: 'Rows out', value: fmtNum(q.rows_out), help: 'Rows in the cleaned file' })
  return out
})
// Every numeric column as uploaded and after QC (the runner profiles both ends of every run).
const PROFILE_STATS = ['n', 'missing', 'mean', 'median', 'sd', 'min', 'max'] as const
const STAT_LABEL: Record<string, string> = { n: 'Values', missing: 'Missing', mean: 'Mean', median: 'Median', sd: 'SD', min: 'Min', max: 'Max' }
const profile = computed(() => d.value?.run?.profile ?? null)
const profileCols = computed(() => {
  const p = profile.value
  if (!p) return []
  const keys = ['beta', 'se', 'p', 'eaf', 'n', 'info'].filter(c => p.input?.columns?.[c] || p.output?.columns?.[c])
  return keys.map(c => ({ key: c, rows: [{ label: 'uploaded', s: p.input?.columns?.[c] }, { label: 'after QC', s: p.output?.columns?.[c] }] }))
})
const removed = (s: any) => s.rows_before != null && s.rows_after != null ? s.rows_before - s.rows_after : 0
const metaLabels = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const sec of schema.value ?? []) {
    for (const f of sec.fields ?? []) out[f.key] = f.label
    if (sec.grid) for (const r of sec.grid.rows) for (const c of sec.grid.cols) out[`${r.key}_${c.key}`] = `${r.label}, ${c.label.toLowerCase()}`
  }
  return out
})
const openEdits = ref(new Set<number>())
function toggleEdit(i: number) { const n = new Set(openEdits.value); n.has(i) ? n.delete(i) : n.add(i); openEdits.value = n }
const changesOf = (e: any, i: number) => { const all = Object.entries(e.changes); return openEdits.value.has(i) ? all : all.slice(0, 3) }
// Remapping columns: the file's own column names (its header line), read when editing starts.
const fileColumns = ref<string[]>([])
const columnOptions = computed(() => [{ label: 'Not mapped', value: null }, ...fileColumns.value.map(c => ({ label: c, value: c }))])
const mappingGaps = computed(() => form.column_mapping ? unmappedRequired(form.column_mapping) : [])
async function startEdit() {
  Object.assign(form, { cohort: d.value.cohort, phenotype: d.value.phenotype, ancestry: d.value.ancestry, sex: d.value.sex,
    n_cases: d.value.n_cases, n_controls: d.value.n_controls, n_total: d.value.n_total, build: d.value.build,
    effect_type: d.value.effect_type, column_mapping: Object.fromEntries(STANDARD_FIELDS.map(f => [f.key, d.value.column_mapping?.[f.key] ?? null])) })
  metaForm.value = { ...d.value.study_meta }
  editing.value = true
  try { fileColumns.value = (await api<{ columns: string[] }>(`/datasets/${d.value.id}/columns`)).columns }
  catch { fileColumns.value = Object.values(d.value.column_mapping ?? {}) as string[] }  // the mapped ones at least
}
const saving = ref(false)
async function saveEdit() {
  saving.value = true
  try {
    const counts = formQuantitative.value ? { n_cases: null, n_controls: null } : { n_total: null }  // the server works out the rest
    const r = await api(`/datasets/${d.value.id}`, { method: 'PATCH', body: { ...form, ...counts, study_meta: metaForm.value } })
    editing.value = false
    toast.add({ severity: 'success', summary: r.changed.length ? `Saved ${r.changed.length} change${r.changed.length > 1 ? 's' : ''}` : 'No changes', life: 2500 })
    if (r.rerun_started) {
      toast.add({ severity: 'info', summary: 'QC re-run started', detail: 'The genome build changed, so QC runs again with the new build.', life: 5000 })
      clearInterval(timer)
      timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
    }
    if (r.rerun_advised) toast.add({ severity: 'warn', summary: 'Re-run QC?', detail: 'Details QC uses changed (ancestry, sample sizes, column mapping or effect type); the last run used the old values.', life: 6000 })
    await load()
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 6000 }) }
  finally { saving.value = false }
}
watch(() => props.datasetId, () => { editing.value = false; tab.value = 'qc' })
const fieldName = (k: string) => ({ cohort: 'Cohort', phenotype: 'Phenotype', ancestry: 'Ancestry', sex: 'Sex', n_cases: 'Cases', n_controls: 'Controls', n_total: 'Sample size', build: 'Genome build' } as any)[k] ?? metaLabels.value[k] ?? k.replaceAll('_', ' ')
const showVal = (v: any) => v === null || v === undefined || v === '' ? '(empty)' : String(v)
</script>

<template>
  <Drawer v-model:visible="visible" position="right" class="ds-drawer" :block-scroll="true">
    <template #header>
      <div v-if="d" class="dh">
        <div class="row">
          <Tag :value="STATUS_META[d.status as DatasetStatus].label" :severity="STATUS_META[d.status as DatasetStatus].severity" :icon="STATUS_META[d.status as DatasetStatus].icon" />
          <span class="muted small mono">dataset {{ d.id }} · run {{ d.run?.id }} · pipeline v{{ d.run?.pipeline_version }}</span>
        </div>
        <h2 class="title">{{ d.cohort }} · {{ d.phenotype }} · {{ d.ancestry }} · {{ d.sex }}</h2>
        <div class="mono muted small">{{ d.file_name }} ({{ fmtSize(d.file_size) }})<span v-if="d.uploaded_with_token"> · uploaded by script, token {{ d.uploaded_with_token }}</span></div>
      </div>
    </template>

    <div v-if="d" class="body">
      <div class="row actions" data-tour="panel-actions">
        <a :href="fileUrl('raw')" class="p-button p-button-outlined p-button-sm"><i class="pi pi-download" />&nbsp;Raw file</a>
        <a v-if="d.run?.status === 'completed'" :href="fileUrl('qc')" class="p-button p-button-sm"><i class="pi pi-download" />&nbsp;QC'd file</a>
        <a v-if="d.run && d.status !== 'processing'" :href="fileUrl('report')" :download="`qc_report_${d.id}.html`" class="p-button p-button-secondary p-button-text p-button-sm"><i class="pi pi-file" />&nbsp;Download report</a>
        <span class="spacer" />
        <Button v-if="isReviewer && !['processing', 'review'].includes(d.status) && !overriding" label="Override QC outcome" size="small" text severity="secondary" @click="overriding = true; tab = 'qc'" />
        <Button v-if="isReviewer && !['processing', 'pending'].includes(d.status)" label="Re-run QC" icon="pi pi-refresh" size="small" text @click="rerun" />
        <Button v-if="d.can_edit && d.status === 'processing'" label="Stop QC" icon="pi pi-stop-circle" size="small" text severity="danger" @click="stopQc" />
        <Button v-if="d.can_edit" v-tooltip.top="d.status === 'processing' ? 'Wait for QC to finish' : undefined" label="Delete" icon="pi pi-trash" size="small" text severity="danger" :disabled="d.status === 'processing'" @click="remove" />
      </div>

      <div v-if="d.pipeline" class="row pipe small">
        <span class="muted">QC pipeline</span>
        <Select v-if="isReviewer" :model-value="pipelineChoice" :options="pipelineOptions" option-label="label" option-value="value" size="small" :disabled="d.status === 'processing'" @update:model-value="setPipeline" />
        <span v-else>{{ d.pipeline.pipeline_name }}</span>
        <span class="muted">current v{{ d.pipeline.current_version }}<span v-if="d.pipeline.run_version != null"> · last run used v{{ d.pipeline.run_version }}<span v-if="d.pipeline.run_pipeline && d.pipeline.run_pipeline !== d.pipeline.pipeline_name"> of {{ d.pipeline.run_pipeline }}</span></span></span>
      </div>
      <Message v-if="d.pipeline?.out_of_date && d.status !== 'processing'" severity="warn" size="small" :closable="false" icon="pi pi-history" class="oodmsg">
        <div class="row">
          <span>The last QC run used a different pipeline version than the one this dataset now follows. Re-run QC to bring it up to date.</span>
          <Button v-if="isReviewer" label="Re-run QC" icon="pi pi-refresh" size="small" text severity="warn" class="oodbtn" @click="rerun" />
        </div>
      </Message>

      <Message v-if="d.status === 'pending'" severity="info" size="small" :closable="false" icon="pi pi-pause-circle" class="oodmsg" data-tour="panel-start-qc">
        <div class="row">
          <span><strong>QC hasn't started.</strong> Check the details (build, sample sizes, column mapping, metadata), then start it.</span>
          <span class="spacer" />
          <Button v-if="d.can_edit" label="Start QC" icon="pi pi-play" size="small" @click="rerun" />
        </div>
      </Message>

      <Tabs v-model:value="tab">
        <TabList>
          <Tab value="qc">QC</Tab>
          <Tab value="details" data-tour="panel-details-tab">Details <span class="muted small">· metadata {{ d.metadata_complete.filled }}/{{ d.metadata_complete.total }}</span>
            <i v-if="d.metadata_complete.missing_required?.length" v-tooltip.top="`Required: ${d.metadata_complete.missing_required.join(', ')}`" class="pi pi-exclamation-circle warnic" /></Tab>
        </TabList>
        <TabPanels>
          <TabPanel value="qc">
            <div class="stack">
              <Message v-if="d.run?.stop_reason" severity="error" :closable="false" icon="pi pi-ban">{{ d.run.stop_reason }}</Message>
              <Message v-if="d.run?.error" :severity="d.run.status === 'stopped' ? 'info' : 'error'" :closable="false"><pre class="err">{{ d.run.error }}</pre></Message>
              <Message v-if="d.review" :severity="d.review.decision === 'approve' ? 'success' : 'warn'" :closable="false" icon="pi pi-user">
                {{ { approve: 'Approved', reject: 'Rejected', request_changes: 'Changes requested' }[d.review.decision as string] }} by {{ d.review.by }}<span v-if="d.review.note">: “{{ d.review.note }}”</span>
              </Message>

              <div v-if="d.status === 'changes_requested' && d.can_edit" class="review attention">
                <div class="row"><i class="pi pi-reply" /><strong>Changes requested</strong>
                  <span class="muted small">Upload a corrected file in place of this one, or fix the details (Details tab) and re-run QC.</span></div>
                <div class="row">
                  <input ref="fileInput" type="file" accept=".tsv,.txt,.csv,.gz" class="hidden-input" @change="replaceFile">
                  <Button label="Upload a corrected file" icon="pi pi-upload" size="small" :loading="replacing" @click="fileInput?.click()" />
                  <Button v-if="!isReviewer" label="Re-run QC" icon="pi pi-refresh" size="small" outlined :disabled="replacing" @click="rerun" />
                </div>
                <ProgressBar v-if="replacing" :value="replaceProgress" class="replace-bar" />
                <small class="muted">The corrected file must have the same column names. It replaces the current file, which is deleted: this can't be undone.</small>
              </div>

              <div v-if="isReviewer && (d.status === 'review' || overriding)" :class="['review', { attention: d.status === 'review' }]" data-tour="panel-review">
                <div class="row"><i class="pi pi-eye" /><strong>{{ d.status === 'review' ? 'Awaiting review' : 'Override QC outcome' }}</strong>
                  <span class="muted small">{{ d.status === 'review' ? 'QC passed with warnings. Approve it for meta-analysis, or send it back.' : 'Your decision replaces the QC verdict.' }}</span></div>
                <Textarea v-model="note" rows="2" placeholder="Note to the uploader (optional)" fluid />
                <div class="row">
                  <Button label="Approve" icon="pi pi-check" size="small" severity="success" :loading="busy" @click="decide('approve')" />
                  <Button label="Request changes" icon="pi pi-reply" size="small" severity="warn" outlined :disabled="busy" @click="decide('request_changes')" />
                  <Button label="Reject" icon="pi pi-times" size="small" severity="danger" text :disabled="busy" @click="decide('reject')" />
                  <Button v-if="overriding" label="Cancel" size="small" text severity="secondary" @click="overriding = false" />
                </div>
              </div>

              <section data-tour="panel-steps">
                <h3>Processing steps <span v-if="d.run?.rows_out != null" class="muted">· {{ d.run.rows_out.toLocaleString() }} rows out</span></h3>
                <ol class="steps">
                  <li v-for="(s, i) in steps" :key="i" :class="['step', s.state]">
                    <div class="rail"><span class="dot"><i :class="STEP_META[s.state as StepState]?.icon" /></span><span v-if="i < steps.length - 1" class="line" /></div>
                    <div class="content">
                      <button class="row shead" :aria-expanded="openSteps.has(i)" @click="toggleStep(i)">
                        <strong>{{ s.title }}</strong>
                        <span class="spacer" />
                        <Tag v-if="s.note" :value="notePill(s.note)" severity="warn" class="note-pill" />
                        <Tag :value="s.state" :severity="STEP_META[s.state as StepState]?.severity" />
                        <i :class="['pi pi-angle-down chev', { rot: openSteps.has(i) }]" />
                      </button>
                      <div v-if="s.note" class="small batch-note"><i class="pi pi-info-circle" /> {{ s.note }}</div>
                      <div v-if="s.state === 'running' && s.tail?.lines?.length" class="tail" aria-live="polite">
                        <pre>{{ s.tail.lines.join('\n') }}</pre>
                        <span class="muted small">The step's last {{ s.tail.lines.length }} output lines · updated {{ ago(s.tail.at) }}</span>
                      </div>
                      <table v-if="openSteps.has(i)" class="ct slog">
                        <tbody>
                          <tr>
                            <td class="mono arg">step</td>
                            <td>
                              <div class="row wrap">
                                <span v-tooltip.top="'The step: its script and settings (step.yaml)'" class="ident"><i class="pi pi-code" /><span class="mono">{{ s.key }}</span></span>
                                <span v-if="s.duration != null" class="muted small">{{ dur(s.duration) }}</span>
                                <span class="spacer" />
                                <Button label="Code" icon="pi pi-code" text size="small" class="tbtn" @click="viewCode(s.key)" />
                                <Button v-if="s.dir && !['queued', 'skipped', 'running'].includes(s.state)" label="All files" icon="pi pi-folder-open" text size="small" class="tbtn" @click="viewOutput(s)" />
                              </div>
                              <div v-if="s.source && s.source.repo !== 'gwashub'" class="src mono muted"><i class="pi pi-github" /> {{ srcLabel(s) }}</div>
                            </td>
                          </tr>
                          <tr v-if="s.image">
                            <td class="mono arg">image</td>
                            <td><span v-tooltip.top="'The Docker image (software environment) the step ran in'" class="ident"><i class="pi pi-box" /><span class="mono">{{ s.image }}</span></span>
                              <span class="muted small"> Docker image</span></td>
                          </tr>
                          <tr v-if="ranStep(s)">
                            <td class="mono arg">parameters</td>
                            <td>
                              <span v-if="!s.params" class="muted small">Not recorded for this run (runs record their parameters from 12 October 2026)</span>
                              <span v-else-if="!s.params.length" class="muted small">None</span>
                              <ul v-else class="params">
                                <li v-for="p in s.params" :key="p.key" :class="{ changed: p.changed }">
                                  <span class="pk mono">{{ p.key }}</span>
                                  <span v-tooltip.top="p.help || undefined" class="pd muted">{{ p.label }}</span>
                                  <span v-tooltip.top="p.changed ? `Set by the pipeline; the default is ${fmtParam({ ...p, value: p.default })}` : 'The default'"
                                        class="pv">{{ fmtParam(p) }}</span>
                                </li>
                              </ul>
                            </td>
                          </tr>
                          <tr>
                            <td class="mono arg">verdict</td>
                            <td><Tag :value="s.verdict ?? s.state" :severity="STEP_META[(s.verdict ?? s.state) as StepState]?.severity" />
                              <span v-if="s.on_fail" class="muted small"> on fail: {{ s.on_fail }}</span>
                              <span v-if="s.exit_code != null && s.exit_code !== 0" class="muted small"> · exit code {{ s.exit_code }}</span></td>
                          </tr>
                          <tr v-if="rowsLine(s)">
                            <td class="mono arg">rows</td>
                            <td><span :class="{ removed: removed(s) > 0 }">{{ rowsLine(s) }}</span></td>
                          </tr>
                          <tr v-if="s.messages?.length">
                            <td class="mono arg">messages</td>
                            <td><ul class="msgs"><li v-for="(m, k) in s.messages" :key="k" :class="['msg', { pre: m.includes('\n') }]">{{ m }}</li></ul></td>
                          </tr>
                          <tr v-if="Object.keys(s.metrics ?? {}).length">
                            <td class="mono arg">metrics</td>
                            <td>
                              <div class="mgrid">
                                <div v-for="(v, k) in s.metrics" :key="k" class="m"><span class="muted">{{ metricLabel(String(k)) }}</span><span class="mono">{{ metricValue(String(k), v) }}</span></div>
                              </div>
                            </td>
                          </tr>
                          <tr v-if="s.artifacts?.length">
                            <td class="mono arg">artifacts</td>
                            <td>
                              <div v-for="a in s.artifacts" :key="a.path" class="art">
                                <button class="thumb" :aria-label="`Open ${a.label || a.path}`" @click="viewOutput(s, a.path)">
                                  <img v-if="a.type === 'image'" :src="artUrl(s, a)" :alt="a.label">
                                  <i v-else :class="ART_ICON[a.type] ?? 'pi pi-file'" />
                                </button>
                                <div class="ainfo">
                                  <button class="alabel" @click="viewOutput(s, a.path)">{{ a.label || a.path }}</button>
                                  <div class="muted small"><span class="mono">{{ a.path }}</span><span v-if="fileSize(s, a.path) != null"> · {{ fmtSize(fileSize(s, a.path)) }}</span><span v-if="a.rows != null"> · {{ a.rows.toLocaleString() }} rows</span></div>
                                  <div v-if="a.description" class="muted small">{{ a.description }}</div>
                                </div>
                                <Button v-tooltip.top="'Open'" icon="pi pi-window-maximize" text rounded size="small" class="iconbtn" :aria-label="`Open ${a.label || a.path}`" @click="viewOutput(s, a.path)" />
                                <a v-tooltip.top="'Download'" :href="artUrl(s, a, true)" class="p-button p-button-text p-button-rounded p-button-sm p-button-icon-only iconbtn" :aria-label="`Download ${a.label || a.path}`"><i class="pi pi-download" /></a>
                              </div>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </li>
                </ol>
              </section>

              <section v-if="qcRows.length" data-tour="panel-metrics">
                <h3>QC metrics</h3>
                <table class="qct">
                  <tbody>
                    <tr v-for="r in qcRows" :key="r.label"><th v-tooltip.left="r.help">{{ r.label }}</th><td>{{ r.value }}</td></tr>
                  </tbody>
                </table>
              </section>

              <section v-if="profileCols.length">
                <h3>Columns before and after QC
                  <span class="muted">· {{ fmtNum(profile.input?.rows) }} rows uploaded<span v-if="profile.output">, {{ fmtNum(profile.output.rows) }} after QC</span></span>
                </h3>
                <div class="pwrap">
                  <table class="pt">
                    <thead><tr><th class="l">Column</th><th /><th v-for="k in PROFILE_STATS" :key="k">{{ STAT_LABEL[k] }}</th></tr></thead>
                    <tbody>
                      <template v-for="c in profileCols" :key="c.key">
                        <tr v-for="(r, j) in c.rows" :key="r.label" :class="{ first: j === 0 }">
                          <th>{{ j === 0 ? c.key.toUpperCase() : '' }}</th>
                          <td class="muted lbl">{{ r.label }}</td>
                          <template v-if="r.s"><td v-for="k in PROFILE_STATS" :key="k">{{ fmtNum(r.s[k]) }}</td></template>
                          <td v-else :colspan="PROFILE_STATS.length" class="muted lbl">{{ r.label === 'after QC' && !profile.output ? 'QC did not finish' : 'not in file' }}</td>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
                <small class="muted">Uploaded values are as received, so odds ratios appear before conversion to BETA.</small>
              </section>

              <section v-if="plotData" data-tour="panel-plots">
                <h3>Plots</h3>
                <div class="plots">
                  <div class="plot-card"><ManhattanPlot :data="plotData.manhattan" :gws="plotData.gws" :height="260" /></div>
                  <div class="plot-card"><QQPlot :data="plotData.qq" :size="260" /></div>
                </div>
              </section>

              <section v-if="leads.length">
                <h3>Lead loci (P &lt; 5×10⁻⁸)</h3>
                <DataTable :value="leads" size="small" class="leads">
                  <Column header="Variant"><template #body="{ data }"><span class="mono">{{ data.rsid || '–' }}</span></template></Column>
                  <Column header="Position"><template #body="{ data }"><span class="mono">{{ data.chr }}:{{ Number(data.pos).toLocaleString() }}</span></template></Column>
                  <Column header="EA/OA"><template #body="{ data }"><span class="mono">{{ data.ea }}/{{ data.oa }}</span></template></Column>
                  <Column field="beta" header="BETA" />
                  <Column field="se" header="SE" />
                  <Column field="p" header="P" />
                </DataTable>
              </section>
            </div>
          </TabPanel>

          <TabPanel value="details">
            <div class="stack">
              <div class="row">
                <h3 class="m0">Dataset</h3><span class="spacer" />
                <template v-if="editing">
                  <Button label="Cancel" text severity="secondary" size="small" :disabled="saving" @click="editing = false" />
                  <Button label="Save changes" icon="pi pi-check" size="small" :loading="saving" :disabled="mappingGaps.length > 0" @click="saveEdit" />
                </template>
                <Button v-else-if="d.can_edit" label="Edit" icon="pi pi-pencil" size="small" outlined @click="startEdit" />
              </div>

              <div v-if="editing" class="core-edit">
                <div class="field"><label for="e-cohort">Cohort</label>
                  <AutoComplete v-model="form.cohort" input-id="e-cohort" :suggestions="cohortSuggestions" dropdown fluid
                                placeholder="Choose or type a new cohort" @complete="e => cohortSuggestions = cohortNames.filter(n => n.toLowerCase().includes((e.query ?? '').toLowerCase()))" />
                  <small class="muted">Moving it to another cohort changes the metadata it inherits.</small></div>
                <div class="field"><label>Phenotype</label><Select v-model="form.phenotype" :options="phenoOptions" option-label="label" option-value="value" /></div>
                <div class="field"><label>Ancestry</label><Select v-model="form.ancestry" :options="project.ancestries" /></div>
                <div class="field"><label>Sex</label><SelectButton v-model="form.sex" :options="project.sexes" :allow-empty="false" /></div>
                <div v-if="formQuantitative" class="field"><label>Total sample size</label><InputNumber v-model="form.n_total" :min="1" /></div>
                <template v-else>
                  <div class="field"><label>Cases</label><InputNumber v-model="form.n_cases" :min="1" /></div>
                  <div class="field"><label>Controls</label><InputNumber v-model="form.n_controls" :min="1" /></div>
                </template>
                <div class="field">
                  <label>Genome build</label><SelectButton v-model="form.build" :options="['GRCh37', 'GRCh38']" :allow-empty="false" :disabled="d.status === 'processing'" />
                  <small v-if="form.build !== d.build" class="muted">Saving re-runs QC with the new build.</small>
                </div>
              </div>
              <dl v-else class="kv">
                <dt>Cohort</dt><dd>{{ d.cohort }}</dd>
                <dt>Phenotype</dt><dd>{{ d.phenotype }}</dd>
                <dt>Ancestry</dt><dd>{{ d.ancestry }}</dd>
                <dt>Sex</dt><dd>{{ d.sex }}</dd>
                <template v-if="d.trait_type === 'quantitative'"><dt>Sample size</dt><dd>{{ d.n_total?.toLocaleString() ?? '–' }} <span class="muted">(quantitative trait)</span></dd></template>
                <template v-else><dt>Cases / controls</dt><dd>{{ d.n_cases?.toLocaleString() ?? '–' }} / {{ d.n_controls?.toLocaleString() ?? '–' }}</dd></template>
                <dt>Submitted build</dt><dd>{{ d.build }}</dd>
                <dt>Effect</dt><dd>{{ d.effect_type === 'or' ? 'Odds ratio' : 'BETA' }}</dd>
                <dt>Uploaded</dt><dd>{{ fmtDate(d.created_at) }} by {{ d.uploader }}</dd>
              </dl>

              <div class="row"><h3 class="m0">Study metadata</h3><span class="muted small">{{ d.metadata_complete.filled }} of {{ d.metadata_complete.total }} fields</span>
                <NuxtLink :to="`/projects/${project.id}/cohorts?open=${d.cohort_id}`" class="small">Edit {{ d.cohort }}'s cohort metadata →</NuxtLink></div>
              <Message v-if="d.metadata_complete.missing_required?.length" severity="warn" :closable="false" size="small">
                Metadata incomplete: this project requires {{ d.metadata_complete.missing_required.join(', ') }}.
              </Message>
              <p class="muted small m0">Study, genotyping, imputation and QC details come from the cohort. Change one here only if this dataset differs (e.g. a different imputation panel); it then overrides the cohort's value for this dataset.</p>
              <MetadataForm v-if="editing" v-model="metaForm" :config="d.metadata_config" :inherited="d.cohort_meta" />
              <MetadataForm v-else :model-value="d.study_meta" :config="d.metadata_config" :inherited="d.cohort_meta" readonly />

              <div class="cols2">
                <div>
                  <h3>Column mapping</h3>
                  <template v-if="editing && form.column_mapping">
                    <p class="muted small m0">Which column of the uploaded file holds each value. Changing it doesn't touch the file; re-run QC afterwards.</p>
                    <div class="remap">
                      <template v-for="f in STANDARD_FIELDS" :key="f.key">
                        <label :for="`map-${f.key}`" class="small">{{ f.label }}<span v-if="f.required" class="req" title="Required"> *</span></label>
                        <Select v-model="form.column_mapping[f.key]" :input-id="`map-${f.key}`" :options="columnOptions" option-label="label" option-value="value"
                                size="small" filter placeholder="Not mapped" />
                      </template>
                      <label class="small">Effect type</label>
                      <SelectButton v-model="form.effect_type" :options="[{ l: 'BETA (log-odds or linear)', v: 'beta' }, { l: 'Odds ratio', v: 'or' }]" option-label="l" option-value="v"
                                    :allow-empty="false" size="small" />
                    </div>
                    <small v-if="mappingGaps.length" class="err">Map: {{ mappingGaps.join(', ') }}</small>
                  </template>
                  <dl v-else class="kv mono small">
                    <template v-for="(col, std) in d.column_mapping" :key="std"><dt>{{ std }}</dt><dd>{{ col }}</dd></template>
                    <dt>effect type</dt><dd>{{ d.effect_type === 'or' ? 'odds ratio' : 'BETA' }}</dd>
                  </dl>
                </div>
                <div>
                  <h3>Run history</h3>
                  <ul class="hist">
                    <li v-for="r in [...d.runs].reverse()" :key="r.id" class="small">
                      Run {{ r.id }} · pipeline v{{ r.pipeline_version }} · {{ r.status }}<span v-if="r.overall_verdict"> ({{ r.overall_verdict }})</span> · {{ fmtDate(r.finished_at) }}
                    </li>
                  </ul>
                  <template v-if="d.edits.length">
                    <h3>Edit history</h3>
                    <ul class="hist">
                      <li v-for="(e, i) in [...d.edits].reverse()" :key="i" class="small">
                        <strong>{{ e.by }}</strong> · {{ fmtDate(e.at) }} · {{ Object.keys(e.changes).length }} field{{ Object.keys(e.changes).length === 1 ? '' : 's' }}
                        <div v-for="[k, c] in changesOf(e, i)" :key="k" class="muted chg">{{ fieldName(String(k)) }}: {{ showVal((c as any)[0]) }} → {{ showVal((c as any)[1]) }}</div>
                        <button v-if="Object.keys(e.changes).length > 3" class="more" @click="toggleEdit(i)">{{ openEdits.has(i) ? 'less' : `+${Object.keys(e.changes).length - 3} more` }}</button>
                      </li>
                    </ul>
                  </template>
                </div>
              </div>
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
      <SupportRef :parts="[project.id, `dataset ${d.id}`, d.run && `run ${d.run.id}`]" :location="d.run?.location || d.upload_location" />
    </div>
    <div v-else class="loading"><ProgressSpinner /></div>
  </Drawer>
  <CodeViewer v-model:visible="codeOpen" :project-id="project.id" :step-key="codeKey" />
  <StepOutputViewer v-if="d" v-model:visible="outOpen" :dataset-id="d.id" :run-id="d.run?.id" :step="outStep" :focus="outFocus" />
</template>

<style scoped>
.dh { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.title { margin: 0; font-size: 1.2rem; }
.small { font-size: 0.8rem; }
.body { display: flex; flex-direction: column; gap: 18px; }
.actions { gap: 8px; }
.pipe { gap: 8px; margin: 4px 0 8px; }
.pipe :deep(.p-select-label) { font-size: inherit; }
.oodmsg { margin-bottom: 8px; }
.oodmsg .row { gap: 10px; }
.oodbtn { padding-top: 0; padding-bottom: 0; }
.actions a { text-decoration: none; }
.err { white-space: pre-wrap; font-size: 0.75rem; margin: 0; max-height: 160px; overflow: auto; }
.review { border: 1px solid var(--gh-border); background: var(--gh-bg); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.review.attention { border-color: #f3dfb3; background: #fffaf0; }
.review.attention i { color: #b45309; }
h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; color: var(--gh-muted); margin: 0 0 10px; }
.stack { display: flex; flex-direction: column; gap: 18px; padding-top: 6px; }
.m0 { margin: 0; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 1100px) { .cols2 { grid-template-columns: 1fr; } }
.core-edit { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0 16px; }
.chg { margin-left: 10px; word-break: break-word; }
.steps { list-style: none; margin: 0; padding: 0; }
.step { display: flex; gap: 12px; }
.rail { display: flex; flex-direction: column; align-items: center; width: 26px; flex-shrink: 0; }
.dot { width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; background: #e2e8f0; color: #475569; }
.line { flex: 1; width: 2px; background: var(--gh-border); min-height: 12px; }
.pass .dot { background: #dcfce7; color: #15803d; }
.warn .dot { background: #fef3c7; color: #b45309; }
.fail .dot, .error .dot { background: #fee2e2; color: #b91c1c; }
.running .dot { background: #e0f2fe; color: #0369a1; }
.queued, .skipped { opacity: 0.55; }
.content { flex: 1; min-width: 0; padding-bottom: 16px; }
.iconbtn { width: 1.8rem; height: 1.8rem; }
.src { font-size: 0.72rem; margin-top: 2px; }
.src i { font-size: 0.7rem; margin: 0 2px 0 6px; }
.src i:first-child { margin-left: 0; }
.shead { width: 100%; background: none; border: 0; padding: 2px 0; font: inherit; color: inherit; cursor: pointer; text-align: left; min-height: 1.8rem; }
.shead:hover strong { color: var(--gh-accent); }
.chev { color: var(--gh-muted); font-size: 0.8rem; transition: transform .15s; }
.chev.rot { transform: rotate(180deg); }
.slog { margin-top: 6px; font-size: 0.85rem; }
.tbtn { padding-top: 0; padding-bottom: 0; }
.wrap { flex-wrap: wrap; gap: 4px 10px; }
.msgs { margin: 0; padding-left: 16px; }
.hidden-input { display: none; }
.replace-bar { height: 22px; }
.replace-bar :deep(.p-progressbar-label) { font-size: 0.75rem; line-height: 1; }
.ident { display: inline-flex; align-items: center; gap: 6px; }
.tail { margin: 6px 0 2px; }
.tail pre { margin: 0 0 2px; padding: 6px 8px; font-size: 0.72rem; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--p-content-hover-background); border: 1px solid var(--p-content-border-color); border-radius: 4px; }
.remap { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 10px; align-items: center; margin-top: 6px; }
.params { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: max-content max-content minmax(0, 1fr);
  gap: 1px 16px; font-size: 0.8rem; }
.params li { display: contents; }
.params .pk, .params .pd, .params .pv { padding: 1px 0; border-bottom: 1px dotted var(--p-content-border-color); }
.params .pk { color: var(--gh-muted); }
.params .pv { font-variant-numeric: tabular-nums; }
.params .changed .pk, .params .changed .pd, .params .changed .pv { font-weight: 600; }
.ident .pi { color: var(--gh-muted); font-size: 0.85rem; }
.msg { margin: 0 0 2px; }
.mgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 0 18px; }
.mgrid .m { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px dotted var(--gh-border); padding: 2px 0; }
.mgrid .mono { font-size: 0.8rem; }
.more { background: none; border: 0; color: var(--gh-accent); cursor: pointer; font: inherit; padding: 0; }
.art { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
.art + .art { border-top: 1px solid var(--gh-border); }
.thumb { width: 44px; height: 44px; flex-shrink: 0; border: 1px solid var(--gh-border); border-radius: 6px; background: var(--gh-bg); display: flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; overflow: hidden; color: var(--gh-muted); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.ainfo { flex: 1; min-width: 0; }
.alabel { background: none; border: 0; padding: 0; font: inherit; color: var(--gh-accent); cursor: pointer; text-align: left; }
.alabel:hover { text-decoration: underline; }
.msg.pre { white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace; font-size: 0.75rem; background: #0f1720; color: #fca5a5; padding: 8px; border-radius: 6px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0 0 20px; font-size: 0.88rem; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; word-break: break-all; }
.hist { margin: 0; padding-left: 18px; }
.plots { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: stretch; }
@media (max-width: 900px) { .plots { grid-template-columns: 1fr; } }
.plot-card { border: 1px solid var(--gh-border); border-radius: 8px; padding: 12px; }
.leads :deep(td) { font-size: 0.85rem; }
.qct { border-collapse: collapse; font-size: 0.85rem; columns: 2; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.qct tbody { display: contents; }
.qct tr { display: flex; justify-content: space-between; border-bottom: 1px solid var(--gh-border); padding: 5px 12px 5px 0; margin-right: 16px; }
.qct th { font-weight: 500; color: var(--gh-muted); text-align: left; }
.qct td { font-family: ui-monospace, Menlo, monospace; }
.removed { color: #b45309; font-variant-numeric: tabular-nums; }
.pwrap { overflow-x: auto; }
.pt { border-collapse: collapse; font-size: 0.82rem; width: 100%; }
.pt th, .pt td { padding: 4px 10px; text-align: right; white-space: nowrap; }
.pt thead th { color: var(--gh-muted); font-weight: 500; border-bottom: 1px solid var(--gh-border); }
.pt tbody th, .pt th.l { text-align: left; font-weight: 600; }
.pt td { font-family: ui-monospace, Menlo, monospace; }
.pt td.lbl { text-align: left; font-family: inherit; }
.pt tr.first td, .pt tr.first th { border-top: 1px solid var(--gh-border); }
.warnic { color: #b45309; font-size: 0.8rem; margin-left: 4px; }
.loading { display: flex; justify-content: center; padding: 60px; }
.batch-note { color: #b45309; margin-top: 4px; }
.note-pill { white-space: nowrap; }
</style>
