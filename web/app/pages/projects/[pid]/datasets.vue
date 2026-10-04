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

// Reviewers can select datasets and re-run their QC with the current pipeline version.
const currentVersion = computed(() => project.value?.pipeline?.version ?? null)
const picked = ref<any[]>([])
const rerunnable = (d: any) => !['processing', 'uploading'].includes(d.status)
function pickOutdated() { picked.value = rows.value.filter(d => rerunnable(d) && d.pipeline_version !== currentVersion.value) }
const rerunning = ref(false)
async function rerunPicked() {
  const ids = picked.value.filter(rerunnable).map(d => d.id)
  if (!ids.length) return
  rerunning.value = true
  try {
    const r = await api<{ started: number, skipped: any[] }>(`/projects/${pid.value}/datasets/rerun`, { method: 'POST', body: { ids } })
    toast.add({ severity: 'info', summary: `QC re-run started for ${r.started} dataset${r.started === 1 ? '' : 's'}`,
      detail: `Using pipeline v${currentVersion.value}.${r.skipped.length ? ` ${r.skipped.length} skipped.` : ''}`, life: 4000 })
    picked.value = []
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Re-run failed', detail: e.message, life: 5000 }) }
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
          <div class="filters">
            <IconField class="search">
              <InputIcon class="pi pi-search" />
              <InputText v-model="filters.search" placeholder="Search cohort, phenotype, file, uploader" fluid />
            </IconField>
            <Select v-model="filters.cohort" :options="cohorts" placeholder="Cohort" show-clear filter />
            <Select v-model="filters.phenotype" :options="phenotypes" placeholder="Phenotype" show-clear />
            <Select v-model="filters.ancestry" :options="[...ANCESTRIES]" placeholder="Ancestry" show-clear />
            <Select v-model="filters.sex" :options="[...SEXES]" placeholder="Sex" show-clear />
            <Select v-model="filters.status" :options="statusOptions" option-label="label" option-value="value" placeholder="QC status" show-clear />
            <div class="row mine"><ToggleSwitch v-model="filters.mine" input-id="mine" /><label for="mine">My uploads</label></div>
            <Button v-if="anyFilter" label="Clear" text size="small" @click="clear" />
            <span class="spacer" />
            <SelectButton v-model="view" :options="[{ l: 'Overview', v: 'overview' }, { l: 'Details', v: 'details' }]" option-label="l" option-value="v" :allow-empty="false" size="small" />
          </div>
          <div v-if="isReviewer" class="bulk row">
            <span class="muted small">{{ picked.length ? `${picked.length} selected` : 'Select datasets to re-run their QC' }}</span>
            <Button v-if="currentVersion" :label="`Select those not on v${currentVersion}`" text size="small" @click="pickOutdated" />
            <Button v-if="picked.length" label="Clear selection" text size="small" severity="secondary" @click="picked = []" />
            <span class="spacer" />
            <Button :label="`Re-run QC${picked.length ? ` (${picked.length})` : ''}`" icon="pi pi-refresh" size="small" outlined
                    :disabled="!picked.length" :loading="rerunning" @click="rerunPicked" />
          </div>

          <DataTable v-model:selection="picked" :value="rows" data-key="id" row-hover paginator :rows="15" :class="['dt', view]"
                     scrollable
                     :row-class="(d: any) => d.can_open ? 'openable' : 'locked'" sort-field="created_at" :sort-order="-1"
                     @row-click="e => open(e.data)">
            <template #empty><div class="muted empty">{{ datasets?.length ? 'No datasets match these filters.' : 'No datasets yet.' }}</div></template>
            <Column v-if="isReviewer" selection-mode="multiple" style="width: 2.5rem" />
            <Column v-else header="" style="width: 2rem">
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
              <Column v-for="c in QC_COLUMNS" :key="c.key" :field="`qc.${c.key}`" sortable class="num">
                <template #header><span v-tooltip.top="c.help" class="nocase">{{ c.label }}</span></template>
                <template #body="{ data }">{{ num(data.qc?.[c.key], c.digits) }}</template>
              </Column>
            </template>
            <Column field="pipeline_version" header="Pipeline" sortable class="num">
              <template #body="{ data }">
                <span v-if="data.pipeline_version" :class="{ outdated: currentVersion && data.pipeline_version !== currentVersion }"
                      v-tooltip.top="currentVersion && data.pipeline_version !== currentVersion ? `Current pipeline is v${currentVersion}` : undefined">v{{ data.pipeline_version }}</span>
                <span v-else class="muted">–</span>
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
.fname { display: inline-block; max-width: 220px; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }
.spacer { flex: 1; }
.bulk { gap: 8px; margin: -4px 0 10px; }
.outdated { color: #b45309; font-weight: 600; }
.dt.details :deep(td), .dt.details :deep(th) { font-size: 0.78rem; padding: 0.45rem 0.5rem; }
.nocase { text-transform: none; }
.empty { padding: 24px; text-align: center; }
.reviewed { margin-left: 6px; font-size: 0.8rem; }
:deep(tr.openable) { cursor: pointer; }
:deep(tr.locked) { cursor: not-allowed; }
:deep(tr.locked td) { color: var(--gh-muted); }
:deep(td.num), :deep(th.num) { text-align: right; }
:deep(td), :deep(th) { white-space: nowrap; }
</style>
