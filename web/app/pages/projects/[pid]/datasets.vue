<script setup lang="ts">
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { pid, project, error: projectError, isReviewer, refresh: refreshProject } = useProject()
const { me } = useAuth()
const { data: datasets, refresh } = useApiGet<any[]>(() => `/projects/${pid.value}/datasets`)
const anyProcessing = computed(() => datasets.value?.some(d => d.status === 'processing'))
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => anyProcessing.value && refresh(), 3000) })
onBeforeUnmount(() => clearInterval(timer))

// Filters are mirrored in the query string so the overview's summary table can deep-link here.
const q = (k: string) => (route.query[k] as string | undefined) ?? null
const filters = reactive({
  search: q('search') ?? '', cohort: q('cohort'), ancestry: q('ancestry'), sex: q('sex'), status: q('status') as DatasetStatus | null,
  phenotype: q('phenotype'), mine: route.query.mine === '1',
})
// Overview: who uploaded what and its QC status. Details: sample sizes and QC metrics (smaller type to fit).
const view = ref<'overview' | 'details'>(route.query.view === 'details' ? 'details' : 'overview')
watch(view, v => router.replace({ query: { ...route.query, view: v === 'details' ? 'details' : undefined } }))
watch(filters, (f) => {
  const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(f)) if (v) query[k] = v === true ? '1' : String(v)
  if (route.query.open) query.open = String(route.query.open)
  if (view.value === 'details') query.view = 'details'
  router.replace({ query })
})

const statusOptions = (Object.keys(STATUS_META) as DatasetStatus[]).map(s => ({ label: STATUS_META[s].label, value: s }))
const phenotypes = computed(() => [...new Set((datasets.value ?? []).map(d => d.phenotype))])
const cohorts = computed(() => [...new Set((datasets.value ?? []).map(d => d.cohort))].sort((a, b) => a.localeCompare(b)))
const phenoDesc = (code: string) => project.value?.phenotype_descriptions?.[code] ?? ''
const rows = computed(() => (datasets.value ?? []).filter((d) => {
  const s = filters.search.toLowerCase()
  return (!s || `${d.cohort} ${d.phenotype} ${phenoDesc(d.phenotype)} ${d.file_name} ${d.uploader}`.toLowerCase().includes(s))
    && (!filters.cohort || d.cohort === filters.cohort)
    && (!filters.ancestry || d.ancestry === filters.ancestry) && (!filters.sex || d.sex === filters.sex)
    && (!filters.status || d.status === filters.status) && (!filters.phenotype || d.phenotype === filters.phenotype)
    && (!filters.mine || d.uploaded_by === me.value?.id)
}))
const anyFilter = computed(() => !!(filters.search || filters.cohort || filters.ancestry || filters.sex || filters.status || filters.phenotype || filters.mine))
function clear() { Object.assign(filters, { search: '', cohort: null, ancestry: null, sex: null, status: null, phenotype: null, mine: false }) }

// Members select datasets to start QC on (pending ones: the uploader's own, or any for reviewers) or delete; reviewers
// also re-run QC (each with its pipeline's current version) and pin datasets to a pipeline.
// A dataset is out of date when its latest run used another pipeline, or an older version of its own.
const picked = ref<any[]>([])
const startable = (d: any) => d.status === 'pending' && (isReviewer.value || d.uploaded_by === me.value?.id)
// Uploaders also re-run their own datasets a reviewer sent back (changes requested).
const rerunnable = (d: any) => isReviewer.value ? !['processing', 'uploading'].includes(d.status)
  : startable(d) || (d.status === 'changes_requested' && d.uploaded_by === me.value?.id)
const deletable = (d: any) => d.status !== 'processing' && (isReviewer.value || d.uploaded_by === me.value?.id)
const waiting = computed(() => (datasets.value ?? []).filter(startable))
const runLabel = computed(() => {
  const n = picked.value.filter(rerunnable).length
  const all = n && picked.value.filter(rerunnable).every((d: any) => d.status === 'pending')
  return `${all || !isReviewer.value ? 'Start QC' : 'Run QC'}${n ? ` (${n})` : ''}`
})
async function startWaiting() { picked.value = waiting.value; await rerunPicked() }
const deleting = ref(false)
async function deletePicked() {
  const ids = picked.value.filter(deletable).map((d: any) => d.id)
  if (!ids.length || !window.confirm(`Delete ${ids.length} dataset${ids.length === 1 ? '' : 's'}? Their uploaded files and any QC results are removed. This cannot be undone.`)) return
  deleting.value = true
  try {
    const r = await api<{ deleted: number, skipped: any[] }>(`/projects/${pid.value}/datasets/delete`, { method: 'POST', body: { ids } })
    toast.add({ severity: 'success', summary: `Deleted ${r.deleted} dataset${r.deleted === 1 ? '' : 's'}`, detail: r.skipped.length ? `${r.skipped.length} skipped.` : undefined, life: 4000 })
    picked.value = []
    changed()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 5000 }) }
  finally { deleting.value = false }
}
const outOfDate = computed(() => (datasets.value ?? []).filter(d => d.pipeline?.out_of_date))
function pickOutdated() { picked.value = rows.value.filter(d => d.pipeline?.out_of_date) }
const pipelineOptions = computed(() => (project.value?.pipelines ?? []).map((p: any) => ({ id: p.id, label: `${p.name}${p.is_default ? ' (default)' : ''}` })))
const pinTo = ref<number | null>(null)
async function pinPicked() {
  if (pinTo.value == null) return
  const target = (project.value?.pipelines ?? []).find((p: any) => p.id === pinTo.value)
  try {
    const r = await api<{ changed: number, out_of_date: number[] }>(`/projects/${pid.value}/datasets/pipeline`, { method: 'POST',
      body: { ids: picked.value.map(d => d.id), pipeline_id: target?.is_default ? 0 : pinTo.value } })
    toast.add({ severity: 'success', summary: `${r.changed} dataset${r.changed === 1 ? '' : 's'} now on ${target?.name}`,
      detail: r.out_of_date.length ? `${r.out_of_date.length} out of date: re-run them to use ${target?.name}.` : undefined, life: 5000 })
    pinTo.value = null
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not change the pipeline', detail: e.message, life: 5000 }) }
}
async function rerunOutOfDate() { picked.value = outOfDate.value.filter(rerunnable); await rerunPicked() }
const rerunning = ref(false)
async function rerunPicked() {
  const ids = picked.value.filter(rerunnable).map(d => d.id)
  if (!ids.length) return
  rerunning.value = true
  try {
    const r = await api<{ started: number, skipped: any[] }>(`/projects/${pid.value}/datasets/rerun`, { method: 'POST', body: { ids } })
    toast.add({ severity: 'info', summary: `QC started for ${r.started} dataset${r.started === 1 ? '' : 's'}`,
      detail: `Each with the current version of its pipeline.${r.skipped.length ? ` ${r.skipped.length} skipped.` : ''}`, life: 4000 })
    picked.value = []
    changed()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not start QC', detail: e.message, life: 5000 }) }
  finally { rerunning.value = false }
}
const num = (v: any, digits = 3) => v == null ? '–' : typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(digits) : Number(v).toLocaleString()

const selected = ref<number | null>(route.query.open ? Number(route.query.open) : null)
const panelOpen = ref(!!selected.value)
function open(d: any) {
  if (!d.can_open) {
    toast.add({ severity: 'secondary', summary: 'Restricted', detail: 'Uploaders can only open datasets they submitted. Ask a project reviewer for access.', life: 3500 })
    return
  }
  selected.value = d.id
  panelOpen.value = true
}
function changed() { refresh(); refreshProject() }
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <Message v-if="!isReviewer" severity="info" :closable="false" class="mb" icon="pi pi-lock">
        You're an uploader on this project: you can open and download only the datasets you submitted.
      </Message>

      <Card>
        <template #content>
          <div class="filters compact-ui" data-tour="datasets-filters">
            <IconField class="search">
              <InputIcon class="pi pi-search" />
              <InputText v-model="filters.search" placeholder="Search cohort, phenotype, file, uploader" fluid />
            </IconField>
            <Select overlay-class="compact-overlay" v-model="filters.cohort" :options="cohorts" placeholder="Cohort" show-clear filter />
            <Select overlay-class="compact-overlay" v-model="filters.phenotype" :options="phenotypes" placeholder="Phenotype" show-clear />
            <Select overlay-class="compact-overlay" v-model="filters.ancestry" :options="[...ANCESTRIES]" placeholder="Ancestry" show-clear />
            <Select overlay-class="compact-overlay" v-model="filters.sex" :options="[...SEXES]" placeholder="Sex" show-clear />
            <Select overlay-class="compact-overlay" v-model="filters.status" :options="statusOptions" option-label="label" option-value="value" placeholder="QC status" show-clear />
            <div class="row mine"><ToggleSwitch v-model="filters.mine" input-id="mine" /><label for="mine">My uploads</label></div>
            <Button v-if="anyFilter" label="Clear" text size="small" @click="clear" />
            <span class="spacer" />
            <SelectButton v-model="view" :options="[{ l: 'Overview', v: 'overview' }, { l: 'Details', v: 'details' }]" option-label="l" option-value="v" :allow-empty="false" size="small" />
          </div>
          <Message v-if="outOfDate.length" severity="warn" size="small" :closable="false" class="oodmsg">
            <div class="row">
              <span><strong>{{ outOfDate.length }} dataset{{ outOfDate.length === 1 ? ' is' : 's are' }} out of date</strong>: last QC'd with another pipeline or an older version of its own. Re-run advised.</span>
              <span class="spacer" />
              <Button v-if="isReviewer" label="Re-run all out of date" icon="pi pi-refresh" size="small" text severity="warn" class="oodbtn" :loading="rerunning" @click="rerunOutOfDate" />
            </div>
          </Message>
          <Message v-if="waiting.length" severity="info" size="small" :closable="false" icon="pi pi-pause-circle" class="oodmsg" data-tour="datasets-waiting">
            <div class="row">
              <span><strong>{{ waiting.length }} dataset{{ waiting.length === 1 ? ' is' : 's are' }} waiting for QC</strong>{{ isReviewer ? '' : ' (yours)' }}. Check their details, then start QC.</span>
              <span class="spacer" />
              <Button :label="`Start QC on ${waiting.length === 1 ? 'it' : `all ${waiting.length}`}`" icon="pi pi-play" size="small" text class="oodbtn" :loading="rerunning" @click="startWaiting" />
            </div>
          </Message>
          <div class="bulk row" data-tour="datasets-bulk">
            <span class="muted small">{{ picked.length ? `${picked.length} selected` : isReviewer ? 'Select datasets to start or re-run QC, change their pipeline or delete them' : 'Select your datasets to start QC on pending ones or delete them' }}</span>
            <Button v-if="isReviewer && outOfDate.length" label="Select the out-of-date ones" text size="small" @click="pickOutdated" />
            <Button v-if="picked.length" label="Clear selection" text size="small" severity="secondary" @click="picked = []" />
            <span class="spacer" />
            <Button v-if="picked.some(deletable)" :label="`Delete (${picked.filter(deletable).length})`" icon="pi pi-trash" size="small" text severity="danger"
                    :loading="deleting" @click="deletePicked" />
            <template v-if="isReviewer && picked.length && pipelineOptions.length > 1">
              <Select v-model="pinTo" :options="pipelineOptions" option-label="label" option-value="id" placeholder="Set pipeline…" size="small" />
              <Button label="Apply" size="small" text :disabled="pinTo == null" @click="pinPicked" />
            </template>
            <Button :label="runLabel" icon="pi pi-play" size="small" outlined
                    :disabled="!picked.some(rerunnable)" :loading="rerunning" @click="rerunPicked" />
          </div>

          <DataTable v-model:selection="picked" :value="rows" data-key="id" row-hover paginator :rows="15" :class="['dt', view]" data-tour="datasets-table"
                     scrollable
                     :row-class="(d: any) => d.can_open ? 'openable' : 'locked'" sort-field="created_at" :sort-order="-1"
                     @row-click="e => open(e.data)">
            <template #empty><div class="muted empty">{{ datasets?.length ? 'No datasets match these filters.' : 'No datasets yet.' }}</div></template>
            <Column selection-mode="multiple" style="width: 2.5rem" />
            <Column v-if="!isReviewer" header="" style="width: 2rem">
              <template #body="{ data }"><i :class="data.can_open ? 'pi pi-chevron-right' : 'pi pi-lock'" class="muted" /></template>
            </Column>
            <Column field="cohort" header="Cohort" sortable>
              <template #body="{ data }"><span class="strong">{{ data.cohort }}</span></template>
            </Column>
            <Column field="file_name" header="Dataset" sortable>
              <template #body="{ data }"><span class="mono muted small fname" :title="data.file_name">{{ data.file_name }}</span></template>
            </Column>
            <Column field="phenotype" header="Phenotype" sortable>
              <template #body="{ data }"><span v-tooltip.top="phenoDesc(data.phenotype) || undefined">{{ data.phenotype }}</span></template>
            </Column>
            <Column field="ancestry" header="Ancestry" sortable />
            <Column field="sex" header="Sex" sortable />
            <template v-if="view === 'overview'">
              <Column field="uploader" header="Uploaded by" sortable />
              <Column field="created_at" header="Uploaded" sortable>
                <template #body="{ data }">{{ fmtDate(data.created_at) }}</template>
              </Column>
            </template>
            <template v-else>
              <Column field="n_cases" header="Cases" sortable class="num"><template #body="{ data }">{{ num(data.n_cases) }}</template></Column>
              <Column field="n_controls" header="Controls" sortable class="num"><template #body="{ data }">{{ num(data.n_controls) }}</template></Column>
              <Column field="n_total" header="N" sortable class="num"><template #body="{ data }">{{ num(data.n_total) }}</template></Column>
              <Column v-for="c in QC_COLUMNS" :key="c.key" :field="`qc.${c.key}`" sortable class="num">
                <template #header><span v-tooltip.top="c.help" class="nocase">{{ c.label }}</span></template>
                <template #body="{ data }">{{ num(data.qc?.[c.key], c.digits) }}</template>
              </Column>
            </template>
            <Column field="status" header="QC status" sortable>
              <template #body="{ data }">
                <div class="status">
                  <Tag :value="STATUS_META[data.status as DatasetStatus].label" :severity="STATUS_META[data.status as DatasetStatus].severity" />
                  <i v-if="data.metadata_complete?.missing_required?.length" v-tooltip.top="`Metadata incomplete. Required: ${data.metadata_complete.missing_required.join(', ')}`"
                     class="pi pi-exclamation-circle warnic" />
                </div>
              </template>
            </Column>
            <Column field="pipeline.pipeline_name" header="Pipeline" sortable>
              <template #body="{ data }">
                <span class="nowrap">
                  {{ data.pipeline?.pipeline_name }}<i v-if="data.pipeline?.pinned" v-tooltip.top="'Pinned to this pipeline by a reviewer'" class="pi pi-thumbtack pin" />
                  <span v-if="data.pipeline_version" class="muted small"> · run v{{ data.pipeline_version }}</span>
                  <Tag v-if="data.pipeline?.out_of_date" value="out of date" severity="warn" class="ood"
                       v-tooltip.top="`Last QC used ${data.pipeline.run_pipeline} v${data.pipeline.run_version}; ${data.pipeline.pipeline_name} is at v${data.pipeline.current_version}`" />
                </span>
              </template>
            </Column>
          </DataTable>
        </template>
      </Card>
    </div>
    <DatasetPanel v-model:visible="panelOpen" :project="project" :dataset-id="selected" @changed="changed" />
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
.mb { margin-bottom: 16px; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }
.search { flex: 1 1 260px; }
.mine { gap: 8px; font-size: 0.9rem; }
.strong { font-weight: 600; }
.small { font-size: 0.78rem; }
.fname { display: inline-block; max-width: 170px; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }
.spacer { flex: 1; }
.bulk { gap: 8px; margin: -4px 0 10px; }
.oodmsg { margin: 0 0 10px; }
.oodmsg .row { gap: 10px; width: 100%; }
.oodbtn { padding-top: 0; padding-bottom: 0; }
.pin { font-size: 0.7rem; margin-left: 4px; color: var(--gh-muted); }
.ood { margin-left: 6px; font-size: 0.7rem; padding: 0 6px; }
.nowrap { white-space: nowrap; }
.outdated { color: #b45309; font-weight: 600; }
.dt.details :deep(td), .dt.details :deep(th) { font-size: 0.78rem; padding: 0.45rem 0.5rem; }
.dt.overview :deep(td), .dt.overview :deep(th) { font-size: 0.78rem; padding: 0.45rem 0.6rem; }
.nocase { text-transform: none; }
.empty { padding: 24px; text-align: center; }
.reviewed { margin-left: 6px; font-size: 0.8rem; }
:deep(tr.openable) { cursor: pointer; }
:deep(tr.locked) { cursor: not-allowed; }
:deep(tr.locked td) { color: var(--gh-muted); }
:deep(td.num), :deep(th.num) { text-align: right; }
:deep(td), :deep(th) { white-space: nowrap; }
.status { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.status :deep(.p-tag) { font-size: 0.7rem; padding: 0.1rem 0.4rem; }
.warnic { color: #b45309; }
</style>
