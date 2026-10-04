<script setup lang="ts">
// The cohorts contributing to this project. Each holds the cohort-level metadata (study, genotyping,
// imputation, QC) that its datasets inherit; a dataset can override a value for itself.
const route = useRoute()
const toast = useToast()
const { pid, project, error: projectError } = useProject()
const { data: cohorts, refresh } = useApiGet<any[]>(() => `/projects/${pid.value}/cohorts`)
const search = ref('')
const rows = computed(() => (cohorts.value ?? []).filter(c => !search.value || c.name.toLowerCase().includes(search.value.toLowerCase())))

const open = ref<any>(null)
const editing = ref(false)
const form = reactive({ name: '', meta: {} as Record<string, any> })
watch([cohorts, () => route.query.open], () => {
  const id = Number(route.query.open)
  if (id && cohorts.value && !open.value) open.value = cohorts.value.find(c => c.id === id) ?? null
}, { immediate: true })
function show(c: any) { open.value = c; editing.value = false }
function startEdit() { Object.assign(form, { name: open.value.name, meta: { ...open.value.meta } }); editing.value = true }
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const r = await api<any>(`/cohorts/${open.value.id}`, { method: 'PATCH', body: { name: form.name, meta: form.meta } })
    toast.add({ severity: 'success', summary: `${r.name} saved`, detail: r.datasets ? `Its ${r.datasets} dataset${r.datasets === 1 ? '' : 's'} now use these details (except fields they override).` : undefined, life: 4000 })
    open.value = r
    editing.value = false
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 6000 }) }
  finally { saving.value = false }
}

// Adding a cohort here (uploaders can also add one while uploading).
const adding = ref(false)
const newName = ref('')
const similar = ref<any[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
watch(newName, (name) => {
  clearTimeout(timer)
  similar.value = []
  if (!name.trim()) return
  timer = setTimeout(async () => { similar.value = await api<any[]>(`/projects/${pid.value}/cohorts/similar`, { query: { name } }).catch(() => []) }, 300)
})
async function add() {
  try {
    const r = await api<any>(`/projects/${pid.value}/cohorts`, { method: 'POST', body: { name: newName.value } })
    toast.add({ severity: 'success', summary: `${r.name} added`, life: 3000 })
    adding.value = false
    newName.value = ''
    await refresh()
    show(r)
    startEdit()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not add the cohort', detail: e.message, life: 5000 }) }
}
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <div class="layout">
        <Card class="list">
          <template #title>
            <div class="row">Cohorts <span class="muted small">{{ cohorts?.length ?? 0 }}</span><span class="spacer" />
              <Button label="Add cohort" icon="pi pi-plus" size="small" outlined @click="adding = true" /></div>
          </template>
          <template #content>
            <IconField class="mb">
              <InputIcon class="pi pi-search" />
              <InputText v-model="search" placeholder="Search cohorts" fluid />
            </IconField>
            <p v-if="!rows.length" class="muted">{{ cohorts?.length ? 'No cohorts match.' : 'No cohorts yet. They are added when datasets are uploaded, or here.' }}</p>
            <ul class="cl">
              <li v-for="c in rows" :key="c.id" :class="{ on: open?.id === c.id }" @click="show(c)">
                <div class="strong">{{ c.name }}</div>
                <div class="muted small">{{ c.datasets }} dataset{{ c.datasets === 1 ? '' : 's' }} · metadata {{ c.metadata_complete.filled }}/{{ c.metadata_complete.total }}
                  <i v-if="c.metadata_complete.missing_required.length" v-tooltip.top="`Required: ${c.metadata_complete.missing_required.join(', ')}`" class="pi pi-exclamation-circle warnic" /></div>
              </li>
            </ul>
          </template>
        </Card>

        <Card v-if="open" class="detail">
          <template #title>
            <div class="row">
              <InputText v-if="editing" v-model="form.name" class="name" />
              <span v-else>{{ open.name }}</span>
              <span class="spacer" />
              <template v-if="editing">
                <Button label="Cancel" text severity="secondary" size="small" :disabled="saving" @click="editing = false" />
                <Button label="Save" icon="pi pi-check" size="small" :loading="saving" @click="save" />
              </template>
              <Button v-else-if="open.can_edit" label="Edit" icon="pi pi-pencil" size="small" outlined @click="startEdit" />
            </div>
          </template>
          <template #subtitle>
            {{ open.datasets }} dataset{{ open.datasets === 1 ? '' : 's' }}<span v-if="open.created_by"> · added by {{ open.created_by }} on {{ fmtDate(open.created_at) }}</span>
            · <NuxtLink :to="{ path: `/projects/${pid}/datasets`, query: { cohort: open.name } }">view its datasets</NuxtLink>
          </template>
          <template #content>
            <Message v-if="open.metadata_complete.missing_required.length" severity="warn" size="small" :closable="false" class="mb">
              Metadata incomplete: this project requires {{ open.metadata_complete.missing_required.join(', ') }}.
            </Message>
            <p class="muted small">These details apply to every dataset from {{ open.name }}, unless a dataset overrides a value for itself
              (for example a different imputation panel for one phenotype).<span v-if="!open.can_edit"> Only the person who added the cohort or a reviewer can change them.</span></p>
            <MetadataForm v-if="editing" v-model="form.meta" :config="project.metadata_config" level="cohort" />
            <MetadataForm v-else :model-value="open.meta" :config="project.metadata_config" level="cohort" readonly />
          </template>
        </Card>
        <Card v-else class="detail"><template #content><p class="muted m0">Choose a cohort to see its details.</p></template></Card>
      </div>
    </div>

    <Dialog v-model:visible="adding" modal header="Add a cohort" class="csd" :draggable="false">
      <div class="field"><label for="nc">Cohort name</label><InputText id="nc" v-model="newName" placeholder="e.g. UK Biobank" fluid autofocus @keydown.enter="newName.trim() && add()" /></div>
      <Message v-if="similar.length" severity="warn" size="small" :closable="false">
        Already in this project: {{ similar.map(s => s.name).join(', ') }}. Make sure it isn't one of these before adding a new cohort.
      </Message>
      <div class="row end">
        <Button label="Cancel" text severity="secondary" @click="adding = false" />
        <Button label="Add cohort" icon="pi pi-check" :disabled="!newName.trim() || similar.some(s => s.exact)" @click="add" />
      </div>
    </Dialog>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.small { font-size: 0.8rem; }
.strong { font-weight: 600; }
.mb { margin-bottom: 12px; }
.m0 { margin: 0; }
.spacer { flex: 1; }
.end { justify-content: flex-end; margin-top: 12px; }
.cl { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 2px; max-height: 65vh; overflow: auto; }
.cl li { padding: 8px 10px; border-radius: 6px; cursor: pointer; }
.cl li:hover { background: var(--gh-accent-soft); }
.cl li.on { background: var(--gh-accent-soft); box-shadow: inset 3px 0 0 var(--gh-accent); }
.name { min-width: 320px; }
.warnic { color: #b45309; font-size: 0.8rem; margin-left: 4px; }
</style>
