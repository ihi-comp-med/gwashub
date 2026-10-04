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
const showAll = ref(new Set<number>())
const MAX_METRICS = 4
const HIDDEN_METRICS = new Set(['rows_out'])  // already shown as the step's row count
const metricEntries = (s: any) => Object.entries(s.metrics ?? {}).filter(([k]) => !HIDDEN_METRICS.has(k))
const metricsOf = (s: any, i: number) => { const e = metricEntries(s); return showAll.value.has(i) ? e : e.slice(0, MAX_METRICS) }
function toggleMetrics(i: number) { const n = new Set(showAll.value); n.has(i) ? n.delete(i) : n.add(i); showAll.value = n }

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
  try {
    await api(`/datasets/${d.value.id}/rerun`, { method: 'POST' })
    toast.add({ severity: 'info', summary: 'QC re-run started', detail: 'Using the current pipeline version.', life: 2500 })
    await load()
    clearInterval(timer)
    timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Re-run failed', detail: e.message, life: 4000 }) }
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
function viewOutput(s: any) { outStep.value = s; outOpen.value = true }

// --- Details tab: edit descriptive fields + study metadata -------------------------------
const tab = ref('qc')
const editing = ref(false)
const form = reactive<any>({})
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
function startEdit() {
  Object.assign(form, { cohort: d.value.cohort, phenotype: d.value.phenotype, ancestry: d.value.ancestry, sex: d.value.sex,
    n_cases: d.value.n_cases, n_controls: d.value.n_controls, build: d.value.build })
  metaForm.value = { ...d.value.study_meta }
  editing.value = true
}
const saving = ref(false)
async function saveEdit() {
  saving.value = true
  try {
    const r = await api(`/datasets/${d.value.id}`, { method: 'PATCH', body: { ...form, study_meta: metaForm.value } })
    editing.value = false
    toast.add({ severity: 'success', summary: r.changed.length ? `Saved ${r.changed.length} change${r.changed.length > 1 ? 's' : ''}` : 'No changes', life: 2500 })
    if (r.rerun_started) {
      toast.add({ severity: 'info', summary: 'QC re-run started', detail: 'The genome build changed, so QC runs again with the new build.', life: 5000 })
      clearInterval(timer)
      timer = setInterval(() => d.value?.status === 'processing' && load(), 2000)
    }
    if (r.rerun_advised) toast.add({ severity: 'warn', summary: 'Re-run QC?', detail: 'Ancestry or sample sizes changed; QC used the old values.', life: 6000 })
    await load()
    emit('changed')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 6000 }) }
  finally { saving.value = false }
}
watch(() => props.datasetId, () => { editing.value = false; tab.value = 'qc' })
const fieldName = (k: string) => ({ cohort: 'Cohort', phenotype: 'Phenotype', ancestry: 'Ancestry', sex: 'Sex', n_cases: 'Cases', n_controls: 'Controls', build: 'Genome build' } as any)[k] ?? metaLabels.value[k] ?? k.replaceAll('_', ' ')
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
        <div class="mono muted small">{{ d.file_name }} ({{ fmtSize(d.file_size) }})</div>
      </div>
    </template>

    <div v-if="d" class="body">
      <div class="row actions">
        <a :href="fileUrl('raw')" class="p-button p-button-outlined p-button-sm"><i class="pi pi-download" />&nbsp;Raw file</a>
        <a v-if="d.run?.status === 'completed'" :href="fileUrl('qc')" class="p-button p-button-sm"><i class="pi pi-download" />&nbsp;QC'd file</a>
        <a v-if="d.run && d.status !== 'processing'" :href="fileUrl('report')" :download="`qc_report_${d.id}.html`" class="p-button p-button-secondary p-button-text p-button-sm"><i class="pi pi-file" />&nbsp;Download report</a>
        <span class="spacer" />
        <Button v-if="isReviewer && !['processing', 'review'].includes(d.status) && !overriding" label="Override QC outcome" size="small" text severity="secondary" @click="overriding = true; tab = 'qc'" />
        <Button v-if="isReviewer && d.status !== 'processing'" label="Re-run QC" icon="pi pi-refresh" size="small" text @click="rerun" />
        <Button v-if="d.can_edit" v-tooltip.top="d.status === 'processing' ? 'Wait for QC to finish' : undefined" label="Delete" icon="pi pi-trash" size="small" text severity="danger" :disabled="d.status === 'processing'" @click="remove" />
      </div>

      <Tabs v-model:value="tab">
        <TabList>
          <Tab value="qc">QC</Tab>
          <Tab value="details">Details <span class="muted small">· metadata {{ d.metadata_complete.filled }}/{{ d.metadata_complete.total }}</span>
            <i v-if="d.metadata_complete.missing_required?.length" v-tooltip.top="`Required: ${d.metadata_complete.missing_required.join(', ')}`" class="pi pi-exclamation-circle warnic" /></Tab>
        </TabList>
        <TabPanels>
          <TabPanel value="qc">
            <div class="stack">
              <Message v-if="d.run?.stop_reason" severity="error" :closable="false" icon="pi pi-ban">{{ d.run.stop_reason }}</Message>
              <Message v-if="d.run?.error" severity="error" :closable="false"><pre class="err">{{ d.run.error }}</pre></Message>
              <Message v-if="d.review" :severity="d.review.decision === 'approve' ? 'success' : 'warn'" :closable="false" icon="pi pi-user">
                {{ { approve: 'Approved', reject: 'Rejected', request_changes: 'Changes requested' }[d.review.decision as string] }} by {{ d.review.by }}<span v-if="d.review.note">: “{{ d.review.note }}”</span>
              </Message>

              <div v-if="isReviewer && (d.status === 'review' || overriding)" :class="['review', { attention: d.status === 'review' }]">
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

              <section>
                <h3>Processing steps <span v-if="d.run?.rows_out != null" class="muted">· {{ d.run.rows_out.toLocaleString() }} rows out</span></h3>
                <ol class="steps">
                  <li v-for="(s, i) in steps" :key="i" :class="['step', s.state]">
                    <div class="rail"><span class="dot"><i :class="STEP_META[s.state as StepState]?.icon" /></span><span v-if="i < steps.length - 1" class="line" /></div>
                    <div class="content">
                      <div class="row">
                        <strong>{{ s.title }}</strong>
                        <Button v-tooltip.top="'View code'" icon="pi pi-code" text rounded size="small" class="iconbtn" aria-label="View code" @click="viewCode(s.key)" />
                        <Button v-if="s.dir && !['queued', 'skipped', 'running'].includes(s.state)" v-tooltip.top="'View output'" icon="pi pi-desktop" text rounded size="small" class="iconbtn" aria-label="View output" @click="viewOutput(s)" />
                        <span class="spacer" />
                        <span v-if="removed(s) > 0" class="small removed">−{{ removed(s).toLocaleString() }}</span>
                        <span v-if="s.rows_after != null" class="muted small">{{ s.rows_after.toLocaleString() }} rows</span>
                        <span class="muted small">{{ dur(s.duration) }}</span>
                        <Tag :value="s.state" :severity="STEP_META[s.state as StepState]?.severity" />
                      </div>
                      <div v-if="s.source && s.source.repo !== 'gwashub'" class="src mono muted"><i class="pi pi-github" /> {{ srcLabel(s) }} <i class="pi pi-box" /> {{ s.image }}</div>
                      <p v-for="(m, k) in s.messages" :key="k" :class="['msg', { pre: m.includes('\n') }]">{{ m }}</p>
                      <div v-if="metricEntries(s).length" class="metrics">
                        <span v-for="[k, v] in metricsOf(s, i)" :key="k"><span class="muted">{{ String(k).replaceAll('_', ' ') }}</span> {{ fmtNum(v) }}</span>
                        <button v-if="metricEntries(s).length > MAX_METRICS" class="more" @click="toggleMetrics(i)">{{ showAll.has(i) ? 'less' : `+${metricEntries(s).length - MAX_METRICS} more` }}</button>
                      </div>
                    </div>
                  </li>
                </ol>
              </section>

              <section v-if="qcRows.length">
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

              <section v-if="plotData">
                <h3>Plots</h3>
                <div class="plots">
                  <div class="plot-card"><ManhattanPlot :data="plotData.manhattan" :gws="plotData.gws" /></div>
                  <div class="plot-card"><QQPlot :data="plotData.qq" /></div>
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
                  <Button label="Save changes" icon="pi pi-check" size="small" :loading="saving" @click="saveEdit" />
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
                <div class="field"><label>Cases</label><InputNumber v-model="form.n_cases" :min="1" /></div>
                <div class="field"><label>Controls</label><InputNumber v-model="form.n_controls" :min="1" /></div>
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
                <dt>Cases / controls</dt><dd>{{ d.n_cases?.toLocaleString() ?? '–' }} / {{ d.n_controls?.toLocaleString() ?? '–' }}</dd>
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
                  <dl class="kv mono small">
                    <template v-for="(col, std) in d.column_mapping" :key="std"><dt>{{ std }}</dt><dd>{{ col }}</dd></template>
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
    </div>
    <div v-else class="loading"><ProgressSpinner /></div>
  </Drawer>
  <CodeViewer v-model:visible="codeOpen" :project-id="project.id" :step-key="codeKey" />
  <StepOutputViewer v-if="d" v-model:visible="outOpen" :dataset-id="d.id" :run-id="d.run?.id" :step="outStep" />
</template>

<style scoped>
.dh { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.title { margin: 0; font-size: 1.2rem; }
.small { font-size: 0.8rem; }
.body { display: flex; flex-direction: column; gap: 18px; }
.actions { gap: 8px; }
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
.msg { margin: 6px 0 0; }
.msg.pre { white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace; font-size: 0.75rem; background: #0f1720; color: #fca5a5; padding: 8px; border-radius: 6px; }
.metrics { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 6px; font-size: 0.85rem; }
.metrics .muted { margin-right: 4px; }
.more { background: none; border: 0; color: var(--gh-accent); cursor: pointer; font: inherit; padding: 0; }
.arts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 6px; font-size: 0.8rem; }
.art { text-decoration: none; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0 0 20px; font-size: 0.88rem; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; word-break: break-all; }
.hist { margin: 0; padding-left: 18px; }
.plots { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }
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
</style>
