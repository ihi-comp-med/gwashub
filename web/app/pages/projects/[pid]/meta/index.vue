<script setup lang="ts">
// Meta-analysis tab: list of analyses + (reviewers) a builder to choose datasets and settings.
const toast = useToast()
const { pid, project, error: projectError, isReviewer } = useProject()
const { data: analyses, refresh } = useApiGet<any[]>(() => `/projects/${pid.value}/meta`)
const { data: cands, refresh: refreshCands } = useApiGet<any[]>(() => `/projects/${pid.value}/meta/candidates`)
const { data: settingsResp, refresh: refreshSettings } = useApiGet<any>(() => `/projects/${pid.value}/meta/settings`)
const { data: catalogue } = useApiGet<any>(() => '/meta/methods')
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => analyses.value?.some(a => ['queued', 'running'].includes(a.status)) && refresh(), 2500) })
onBeforeUnmount(() => clearInterval(timer))

// --- Builder ----------------------------------------------------------------------------
const building = ref(false)
const name = ref('')
const s = reactive<any>({})
watch(settingsResp, (v) => { if (v) Object.assign(s, JSON.parse(JSON.stringify(v.settings))) }, { immediate: true })
// Methods (pipeline/analyses/meta_analysis/<owner>/<method>), grouped by owner for the picker.
const methodList = computed<any[]>(() => catalogue.value?.methods ?? [])
const method = computed(() => methodList.value.find(m => m.key === s.method))
const ownerLabel = (o: string) => o === 'gwashub' ? 'GWASHub' : o
const methodGroups = computed(() => [...new Set(methodList.value.map(m => m.owner))]
  .map(o => ({ label: ownerLabel(o), items: methodList.value.filter(m => m.owner === o) })))
const methodParams = computed({
  get: () => s.params?.[s.method] ?? {},
  set: (v) => { s.params = { ...(s.params ?? {}), [s.method]: v } },
})
const common = computed({ get: () => s.common ?? {}, set: (v) => { s.common = v } })
const hasGc = computed(() => method.value?.params?.some((f: any) => f.key === 'genomic_control'))
const filters = reactive({ phenotype: null as string | null, ancestries: [] as string[], sex: null as string | null })
watch(cands, (c) => { if (c?.length && !filters.phenotype) filters.phenotype = c[0].phenotype }, { immediate: true })
const phenotypes = computed(() => [...new Set((cands.value ?? []).map(c => c.phenotype))])
const shown = computed(() => (cands.value ?? []).filter(c => (!filters.phenotype || c.phenotype === filters.phenotype)
  && (!filters.ancestries.length || filters.ancestries.includes(c.ancestry)) && (!filters.sex || c.sex === filters.sex)))
const reason = (c: any) => !c.eligible ? 'No completed QC run' : !s.include_statuses?.includes(c.status) ? `Status "${STATUS_META[c.status as DatasetStatus].label}" excluded by settings` : null
const selectable = computed(() => shown.value.filter(c => !reason(c)))
const selected = ref<any[]>([])
watch(() => [shown.value, s.include_statuses], () => { selected.value = selectable.value.slice() }, { deep: true })
const selectedIds = computed(() => selected.value.filter(c => !reason(c)).map(c => c.id))
const nCases = computed(() => selected.value.reduce((a, c) => a + (c.n_cases ?? 0), 0))
const nControls = computed(() => selected.value.reduce((a, c) => a + (c.n_controls ?? 0), 0))
const ancestriesSel = computed(() => [...new Set(selected.value.map(c => c.ancestry))])

function openBuilder() {
  building.value = true
  name.value = `${filters.phenotype ?? 'Meta-analysis'}${filters.ancestries.length === 1 ? `, ${filters.ancestries[0]}` : ', all ancestries'}`
  refreshCands()
}
watch(() => [filters.phenotype, filters.ancestries.join()], () => {
  if (building.value) name.value = `${filters.phenotype ?? 'Meta-analysis'}${filters.ancestries.length === 1 ? `, ${filters.ancestries[0]}` : ', all ancestries'}`
})
const running = ref(false)
async function run() {
  running.value = true
  try {
    const settings = { method: s.method, params: { [s.method]: methodParams.value }, common: s.common, include_statuses: s.include_statuses }
    const ma = await api(`/projects/${pid.value}/meta`, { method: 'POST', body: { name: name.value, dataset_ids: selectedIds.value, settings } })
    toast.add({ severity: 'success', summary: 'Meta-analysis started', detail: `${ma.n_datasets} datasets`, life: 3000 })
    building.value = false
    navigateTo(`/projects/${pid.value}/meta/${ma.id}`)
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not start', detail: e.message, life: 6000 }) }
  finally { running.value = false }
}
async function saveDefaults() {
  try {
    await api(`/projects/${pid.value}/meta/settings`, { method: 'PUT', body: s })
    await refreshSettings()
    toast.add({ severity: 'success', summary: 'Saved as project defaults', life: 2500 })
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 5000 }) }
}
function resetDefaults() { if (settingsResp.value) Object.assign(s, JSON.parse(JSON.stringify(settingsResp.value.settings))) }
const methodSummary = (a: any) => [a.method?.title ?? a.settings.method, a.settings.params?.model ?? a.settings.params?.scheme].filter(Boolean).join(', ')
const inflated = computed(() => selected.value.filter(c => (c.lambda_gc ?? 0) > 1.1))
const statusChoices = [{ l: 'Passed QC', v: 'passed' }, { l: 'For review', v: 'review' }]
const statusSev = (st: string) => ({ completed: 'success', running: 'info', queued: 'secondary', failed: 'danger' } as any)[st]
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <div class="head">
        <h2 class="h2">Meta-analysis</h2>
        <Button v-if="isReviewer && !building" label="New meta-analysis" icon="pi pi-plus" class="new" @click="openBuilder" />
      </div>
      <Message v-if="!isReviewer" severity="info" :closable="false" icon="pi pi-lock" class="mb">
        You can view results and plots. Only reviewers can run meta-analyses or download full results.
      </Message>

      <Card v-if="building" class="mb builder">
        <template #title><div class="row">New meta-analysis<span class="spacer" /><Button icon="pi pi-times" text rounded aria-label="Close" @click="building = false" /></div></template>
        <template #content>
          <div class="field"><label for="ma-name">Name</label><InputText id="ma-name" v-model="name" /></div>

          <h3 class="h3">1. Choose datasets</h3>
          <div class="filters compact-ui">
            <Select overlay-class="compact-overlay" v-model="filters.phenotype" :options="phenotypes" placeholder="Phenotype" />
            <MultiSelect overlay-class="compact-overlay" v-model="filters.ancestries" :options="[...ANCESTRIES]" placeholder="All ancestries" display="chip" />
            <Select overlay-class="compact-overlay" v-model="filters.sex" :options="[...SEXES]" placeholder="Any sex" show-clear />
            <span class="spacer" />
            <span class="muted small">{{ selectedIds.length }} selected · {{ nCases.toLocaleString() }} cases / {{ nControls.toLocaleString() }} controls · {{ ancestriesSel.join(', ') || '–' }}</span>
          </div>
          <DataTable v-model:selection="selected" :value="shown" data-key="id" class="dt" :row-class="(c: any) => reason(c) ? 'disabled' : ''"
                     :is-data-selectable="(e: any) => !reason(e.data)">
            <Column selection-mode="multiple" header-style="width: 3rem" />
            <Column field="cohort" header="Cohort" />
            <Column field="ancestry" header="Ancestry" />
            <Column field="sex" header="Sex" />
            <Column header="Cases / controls"><template #body="{ data }">{{ data.n_cases?.toLocaleString() }} / {{ data.n_controls?.toLocaleString() }}</template></Column>
            <Column header="Variants"><template #body="{ data }">{{ data.rows?.toLocaleString() ?? '–' }}</template></Column>
            <Column header="λGC"><template #body="{ data }"><span :class="{ warnv: data.lambda_gc > 1.1 }">{{ data.lambda_gc?.toFixed(3) ?? '–' }}</span></template></Column>
            <Column header="QC">
              <template #body="{ data }">
                <Tag :value="STATUS_META[data.status as DatasetStatus].label" :severity="STATUS_META[data.status as DatasetStatus].severity" />
                <div v-if="reason(data)" class="muted tiny">{{ reason(data) }}</div>
              </template>
            </Column>
          </DataTable>

          <h3 class="h3 step">2. Meta-analysis settings <span class="muted small">(project defaults; change for this run or save as new defaults)</span></h3>
          <div class="grid settings">
            <div class="field method">
              <label for="ma-method">Method <i v-if="method" v-tooltip.right="{ value: `${method.description}\n\n${method.reference}`, class: 'method-tip' }" class="pi pi-info-circle info" aria-label="About this method" /></label>
              <Select v-model="s.method" input-id="ma-method" :options="methodGroups" option-group-label="label" option-group-children="items"
                      option-label="title" option-value="key" :loading="!catalogue" />
              <small v-if="method" class="muted"><span class="mono">{{ method.script }}</span> · image <span class="mono">{{ method.image }}</span></small>
            </div>
            <div class="field"><label>Eligible QC statuses</label><SelectButton v-model="s.include_statuses" :options="statusChoices" option-label="l" option-value="v" multiple /></div>
          </div>
          <h4 v-if="method?.params?.length" class="h4">{{ method.title }} parameters</h4>
          <ParamForm v-if="method" v-model="methodParams" :schema="method.params" id-prefix="mp" />
          <Message v-if="inflated.length && hasGc && !methodParams.genomic_control" severity="warn" :closable="false" size="small" class="gcwarn">
            {{ inflated.map(c => `${c.cohort} (λGC ${c.lambda_gc.toFixed(2)})`).join(', ') }} {{ inflated.length > 1 ? 'are' : 'is' }} inflated: consider genomic control, or excluding {{ inflated.length > 1 ? 'them' : 'it' }}.
          </Message>

          <h3 class="h3 step">3. Post-processing <span class="muted small">(lead loci and heterogeneity flags; the same whichever method runs)</span></h3>
          <ParamForm v-if="catalogue" v-model="common" :schema="catalogue.common" id-prefix="cp" />
          <div class="row end">
            <Button label="Reset to project defaults" text severity="secondary" size="small" @click="resetDefaults" />
            <Button label="Save as project defaults" icon="pi pi-bookmark" text size="small" @click="saveDefaults" />
            <span class="spacer" />
            <Button label="Run meta-analysis" icon="pi pi-play" :loading="running" :disabled="selectedIds.length < 2 || !name" @click="run" />
          </div>
          <small v-if="selectedIds.length < 2" class="muted">Select at least two datasets.</small>
        </template>
      </Card>

      <Card>
        <template #title>Analyses</template>
        <template #content>
          <DataTable :value="analyses ?? []" data-key="id" row-hover class="dt clickable" @row-click="e => navigateTo(`/projects/${pid}/meta/${e.data.id}`)">
            <template #empty><div class="muted empty">No meta-analyses yet.{{ isReviewer ? ' Start one with “New meta-analysis”.' : '' }}</div></template>
            <Column field="name" header="Name"><template #body="{ data }"><strong>{{ data.name }}</strong></template></Column>
            <Column header="Datasets"><template #body="{ data }">{{ data.n_datasets }}</template></Column>
            <Column header="Method"><template #body="{ data }">{{ methodSummary(data) }}</template></Column>
            <Column header="Variants"><template #body="{ data }">{{ data.summary?.n_variants?.toLocaleString() ?? '–' }}</template></Column>
            <Column header="Loci"><template #body="{ data }">{{ data.summary?.n_loci ?? '–' }}</template></Column>
            <Column header="λGC"><template #body="{ data }">{{ data.lambda_gc?.toFixed(3) ?? '–' }}</template></Column>
            <Column header="Run by"><template #body="{ data }">{{ data.created_by }} · {{ fmtDate(data.created_at) }}</template></Column>
            <Column header="Status">
              <template #body="{ data }"><Tag :value="data.status" :severity="statusSev(data.status)" :icon="['queued', 'running'].includes(data.status) ? 'pi pi-spin pi-spinner' : undefined" /></template>
            </Column>
          </DataTable>
        </template>
      </Card>
    </div>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
/* Same text size as the Datasets tables. */
.dt :deep(td), .dt :deep(th) { font-size: 0.78rem; padding: 0.45rem 0.6rem; }
.dt :deep(.p-tag) { font-size: 0.7rem; padding: 0.1rem 0.4rem; }
.head { display: flex; align-items: center; gap: 20px; margin-bottom: 16px; }
.head .h2 { margin: 0; }
.head .new { flex: 0 0 auto; margin-left: auto; }
.info { color: var(--p-text-muted-color); cursor: help; font-size: 0.85em; margin-left: 4px; }
.h2 { font-size: 1.25rem; margin: 0 0 4px; }
.h3 { font-size: 0.95rem; margin: 18px 0 10px; }
/* The builder's numbered steps: a rule and space above each one after the first. */
.h3.step { margin-top: 28px; padding-top: 20px; border-top: 1px solid var(--gh-border); }
.mb { margin-bottom: 16px; }
.small { font-size: 0.8rem; }
.tiny { font-size: 0.72rem; }
.builder { border: 1px solid #b5bce0; }
.filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.settings { grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr); gap: 4px 20px; }
@media (max-width: 800px) { .settings { grid-template-columns: 1fr; } }
.method small { line-height: 1.5; }
.h4 { font-size: 0.85rem; margin: 14px 0 8px; color: var(--gh-muted); font-weight: 600; }
.gcwarn { margin-top: 10px; }
.end { margin-top: 8px; gap: 8px; }
.warnv { color: #b45309; font-weight: 600; }
.empty { padding: 20px; text-align: center; }
:deep(tr.disabled td) { color: var(--gh-muted); }
.clickable :deep(tbody tr) { cursor: pointer; }
:global(.method-tip) { max-width: 420px; }
:global(.method-tip .p-tooltip-text) { white-space: pre-line; }
</style>
