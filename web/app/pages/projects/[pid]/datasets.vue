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
  search: q('search') ?? '', ancestry: q('ancestry'), sex: q('sex'), status: q('status') as DatasetStatus | null,
  phenotype: q('phenotype'), mine: route.query.mine === '1',
})
watch(filters, (f) => {
  const query: Record<string, string> = {}
  for (const [k, v] of Object.entries(f)) if (v) query[k] = v === true ? '1' : String(v)
  if (route.query.open) query.open = String(route.query.open)
  router.replace({ query })
})

const statusOptions = (Object.keys(STATUS_META) as DatasetStatus[]).map(s => ({ label: STATUS_META[s].label, value: s }))
const phenotypes = computed(() => [...new Set((datasets.value ?? []).map(d => d.phenotype))])
const rows = computed(() => (datasets.value ?? []).filter((d) => {
  const s = filters.search.toLowerCase()
  return (!s || `${d.cohort} ${d.phenotype} ${d.file_name} ${d.uploader}`.toLowerCase().includes(s))
    && (!filters.ancestry || d.ancestry === filters.ancestry) && (!filters.sex || d.sex === filters.sex)
    && (!filters.status || d.status === filters.status) && (!filters.phenotype || d.phenotype === filters.phenotype)
    && (!filters.mine || d.uploaded_by === me.value?.id)
}))
const anyFilter = computed(() => !!(filters.search || filters.ancestry || filters.sex || filters.status || filters.phenotype || filters.mine))
function clear() { Object.assign(filters, { search: '', ancestry: null, sex: null, status: null, phenotype: null, mine: false }) }

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
            <Select v-model="filters.phenotype" :options="phenotypes" placeholder="Phenotype" show-clear />
            <Select v-model="filters.ancestry" :options="[...ANCESTRIES]" placeholder="Ancestry" show-clear />
            <Select v-model="filters.sex" :options="[...SEXES]" placeholder="Sex" show-clear />
            <Select v-model="filters.status" :options="statusOptions" option-label="label" option-value="value" placeholder="QC status" show-clear />
            <div class="row mine"><ToggleSwitch v-model="filters.mine" input-id="mine" /><label for="mine">My uploads</label></div>
            <Button v-if="anyFilter" label="Clear" text size="small" @click="clear" />
          </div>

          <DataTable :value="rows" data-key="id" row-hover paginator :rows="15" :row-class="(d: any) => d.can_open ? 'openable' : 'locked'"
                     sort-field="created_at" :sort-order="-1" @row-click="e => open(e.data)">
            <template #empty><div class="muted empty">{{ datasets?.length ? 'No datasets match these filters.' : 'No datasets yet.' }}</div></template>
            <Column header="" style="width: 2rem">
              <template #body="{ data }"><i :class="data.can_open ? 'pi pi-chevron-right' : 'pi pi-lock'" class="muted" /></template>
            </Column>
            <Column field="cohort" header="Dataset" sortable>
              <template #body="{ data }">
                <div class="strong">{{ data.cohort }}</div>
                <div class="mono muted small fname" :title="data.file_name">{{ data.file_name }}</div>
              </template>
            </Column>
            <Column field="phenotype" header="Phenotype" sortable />
            <Column field="ancestry" header="Ancestry" sortable />
            <Column field="sex" header="Sex" sortable />
            <Column field="n_cases" header="Cases" sortable class="num">
              <template #body="{ data }">{{ data.n_cases?.toLocaleString() ?? '–' }}</template>
            </Column>
            <Column field="uploader" header="Uploaded by" sortable />
            <Column field="created_at" header="Uploaded" sortable>
              <template #body="{ data }">{{ fmtDate(data.created_at) }}</template>
            </Column>
            <Column field="status" header="QC status" sortable>
              <template #body="{ data }">
                <Tag :value="STATUS_META[data.status as DatasetStatus].label" :severity="STATUS_META[data.status as DatasetStatus].severity" :icon="STATUS_META[data.status as DatasetStatus].icon" />
                <i v-if="data.review" v-tooltip.top="`Reviewed by ${data.review.by}`" class="pi pi-user muted reviewed" />
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
.fname { max-width: 240px; overflow: hidden; text-overflow: ellipsis; }
.empty { padding: 24px; text-align: center; }
.reviewed { margin-left: 6px; font-size: 0.8rem; }
:deep(tr.openable) { cursor: pointer; }
:deep(tr.locked) { cursor: not-allowed; }
:deep(tr.locked td) { color: var(--gh-muted); }
:deep(td.num), :deep(th.num) { text-align: right; }
:deep(td), :deep(th) { white-space: nowrap; }
</style>
