<script setup lang="ts">
// Pipeline builder: a project's named pipelines, each an ordered list of steps (step from the catalog, on_fail, params;
// each step runs in the image its step.yaml names). Edits are local until "Save", which creates a new version of the
// selected pipeline. Datasets follow the default pipeline unless a reviewer pins them to another.
const toast = useToast()
const route = useRoute()
const router = useRouter()
const { pid, project, error: projectError, isReviewer, refresh: refreshProject } = useProject()
// The pipeline shown: ?pipeline=<id>, else the project default.
const selectedId = computed(() => (route.query.pipeline ? Number(route.query.pipeline) : null))
const { data: pl, refresh } = useApiGet<any>(() => `/projects/${pid.value}/pipeline${selectedId.value ? `?pipeline_id=${selectedId.value}` : ''}`, { keep: true })
function selectPipeline(id: number) { router.replace({ query: { ...route.query, pipeline: String(id) } }) }

interface Entry { uid: string, step: string | null, on_fail: string, params: Record<string, any> }
let uidN = 0
const uid = () => `e${++uidN}`
// Settings a step no longer has (its step.yaml changed since the pipeline was saved) are dropped, so saving works.
const knownParams = (step: string, params: Record<string, any> = {}) => {
  const keys = new Set((stepOf(step)?.param_schema ?? []).map((f: any) => f.key))
  return Object.fromEntries(Object.entries(params).filter(([k]) => keys.has(k)))
}
const toEntries = (steps: any[]): Entry[] => steps.map(s => ({ uid: uid(), step: s.step, on_fail: s.on_fail ?? stepOf(s.step)?.on_fail ?? 'flag', params: knownParams(s.step, s.params) }))
const imageOf = (e: Entry) => stepOf(e.step)?.image ?? ''
const fromEntries = (es: Entry[]) => es.map(e => {
  const s = stepOf(e.step)!
  const out: any = { step: e.step }
  if (e.on_fail !== s.on_fail) out.on_fail = e.on_fail
  const p = Object.fromEntries(Object.entries(e.params).filter(([k, v]) => v !== '' && v != null && JSON.stringify(v) !== JSON.stringify(s.params?.[k])))
  if (Object.keys(p).length) out.params = p
  return out
})

const entries = ref<Entry[]>([])
const saved = ref('')
const note = ref('')
// "show the format" / "show an example" in the step contract: one pop-out panel, filled with whichever was asked for
const contractPop = ref()
const popWhat = ref<'input' | 'log'>('input')
function showContract(event: Event, what: 'input' | 'log') { popWhat.value = what; contractPop.value?.toggle(event) }
const expanded = ref<Set<string>>(new Set())
watch(pl, (v) => {
  if (!v) return
  entries.value = toEntries(v.steps)
  saved.value = JSON.stringify(fromEntries(entries.value))
}, { immediate: true })

const catalog = computed<any[]>(() => pl.value?.catalog ?? [])
const stepOf = (key: string | null) => catalog.value.find(s => s.key === key)
// Steps grouped by owner: GWASHub's defaults first, then each consortium's (pipeline/qc_steps/<owner>).
const owners = computed(() => [...new Set(catalog.value.map(s => s.owner))].sort((a, b) => a === 'gwashub' ? -1 : b === 'gwashub' ? 1 : a.localeCompare(b)))
const stepOptions = computed(() => owners.value.map(o => ({
  label: o === 'gwashub' ? 'GWASHub default steps' : `${o} steps`, owner: o,
  items: catalog.value.filter(s => s.owner === o).map(s => ({ label: s.title, value: s.key, desc: s.description })),
})))
const images = computed<any[]>(() => pl.value?.images ?? [])
// On AWS, consecutive steps with the same image run as one Batch job; a new job starts where the image changes.
const jobOf = computed(() => {
  let job = 0
  return entries.value.map((e, i) => (i > 0 && imageOf(e) !== imageOf(entries.value[i - 1]!) ? ++job : job))
})
const onFailOptions = ['block', 'flag', 'continue']
const dirty = computed(() => entries.value.some(e => !e.step) || JSON.stringify(fromEntries(entries.value.filter(e => e.step))) !== saved.value)

// --- Editing -------------------------------------------------------------
function chooseStep(e: Entry, key: string) {
  const s = stepOf(key)
  if (s) { e.on_fail = s.on_fail; e.params = {} }
}
function move(i: number, d: number) {
  const a = entries.value, j = i + d
  if (j < 0 || j >= a.length) return
  ;[a[i], a[j]] = [a[j]!, a[i]!]
}
function remove(i: number) { entries.value.splice(i, 1) }
function insertBeforeReport(e: Entry) {
  const r = entries.value.findIndex(x => x.step === 'gwashub/qc_report')
  entries.value.splice(r === -1 ? entries.value.length : r, 0, e)
}
function addStep() { insertBeforeReport({ uid: uid(), step: null, on_fail: 'flag', params: {} }) }
function toggle(id: string) {
  const s = new Set(expanded.value)
  s.has(id) ? s.delete(id) : s.add(id)
  expanded.value = s
}
const { data: defaults } = useApiGet<{ template: string[] }>('/steps/default')
function resetTemplate() {
  if (defaults.value) entries.value = toEntries(defaults.value.template.map(k => ({ step: k })))
}
function discard() { if (pl.value) entries.value = toEntries(pl.value.steps) }

const contributeOpen = ref(false)

// --- Validation --------------------------------------------------------------
const resourceKeys = computed(() => new Set((pl.value?.resources ?? []).map((r: any) => r.key)))
const warnings = computed(() => {
  const w: string[] = []
  const keys = entries.value.map(e => e.step)
  if (keys.some(k => !k)) w.push('Some rows have no step chosen.')
  if (!keys.length) w.push('The pipeline has no steps.')
  if (keys[0] && keys[0] !== 'gwashub/parse') w.push('Parse should be the first step: it reads the upload into the GWASHub format.')
  if (keys.length && keys.at(-1) !== 'gwashub/qc_report') w.push('QC report should be the last step.')
  ;[...new Set(keys.filter((k, i) => k && keys.indexOf(k) !== i))].forEach(k => w.push(`${stepOf(k)?.title} appears more than once.`))
  // Everything except Parse, Filter and the report works on GRCh38 positions, which Harmonise and liftover produces.
  const h = keys.indexOf('gwashub/harmonise_liftover')
  if (h !== -1) keys.forEach((k, i) => { if (k && i < h && !['gwashub/parse', 'gwashub/filter', 'gwashub/qc_report'].includes(k)) w.push(`${stepOf(k)?.title} needs GRCh38 positions: put it after Harmonise and liftover.`) })
  entries.value.forEach(e => stepOf(e.step)?.resources?.filter((k: string) => !resourceKeys.value.has(k)).forEach((k: string) => w.push(`${stepOf(e.step)?.title} needs the resource ${k}, which isn't available. Add it under Resources.`)))
  return [...new Set(w)]
})
const blocking = computed(() => entries.value.some(e => !e.step) || !entries.value.length)
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const r = await api(`/projects/${pid.value}/pipeline`, { method: 'PUT', body: { steps: fromEntries(entries.value), note: note.value, pipeline_id: pl.value.pipeline.id } })
    toast.add({ severity: 'success', summary: `${pl.value.pipeline.name}: saved as version ${r.version}`,
      detail: 'New uploads and re-runs of its datasets use it; its existing datasets are now out of date (re-run advised).', life: 5000 })
    note.value = ''
    await refresh()
    refreshProject()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Save failed', detail: e.message, life: 5000 }) }
  finally { saving.value = false }
}

// --- Pipelines: add, rename, make default; re-run out-of-date datasets ---------------------------------
const pipeline = computed(() => pl.value?.pipeline)
const pipelines = computed<any[]>(() => pl.value?.pipelines ?? [])
const current = computed(() => pipelines.value.find(p => p.id === pipeline.value?.id))
const newOpen = ref(false)
const newForm = reactive({ name: '', description: '', copy_from: null as number | null })
function openNew() { Object.assign(newForm, { name: '', description: '', copy_from: pipeline.value?.id ?? null }); newOpen.value = true }
async function createPipeline() {
  try {
    const r = await api(`/projects/${pid.value}/pipelines`, { method: 'POST', body: { ...newForm } })
    newOpen.value = false
    toast.add({ severity: 'success', summary: `Pipeline ${r.name} added`, detail: 'Pin datasets to it from the datasets table or a dataset\'s panel.', life: 4000 })
    selectPipeline(r.id)
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not add the pipeline', detail: e.message, life: 5000 }) }
}
const renaming = ref(false)
const renameForm = reactive({ name: '', description: '' })
function startRename() { Object.assign(renameForm, { name: pipeline.value.name, description: pipeline.value.description }); renaming.value = true }
async function editPipeline(body: Record<string, any>, done: string) {
  try {
    await api(`/pipelines/${pipeline.value.id}`, { method: 'PATCH', body })
    renaming.value = false
    toast.add({ severity: 'success', summary: done, life: 3000 })
    await refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not change the pipeline', detail: e.message, life: 5000 }) }
}
const rerunning = ref(false)
async function rerunOutOfDate() {
  rerunning.value = true
  try {
    const r = await api(`/projects/${pid.value}/datasets/rerun`, { method: 'POST', body: { ids: current.value.out_of_date_ids } })
    toast.add({ severity: 'success', summary: `QC re-run started for ${r.started} dataset${r.started === 1 ? '' : 's'}`, life: 4000 })
    await refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not re-run', detail: e.message, life: 5000 }) }
  finally { rerunning.value = false }
}

// --- Resources & code ----------------------------------------------------------
const resourceFilter = ref<'all' | 'used' | 'project'>('all')
const usedBy = (key: string) => entries.value.map(e => stepOf(e.step)).filter(s => s?.resources?.includes(key) || s?.optional_resources?.includes(key))
const resourceSearch = ref('')
const shownResources = computed(() => {
  const q = resourceSearch.value.trim().toLowerCase()
  return (pl.value?.resources ?? []).filter((r: any) =>
    (resourceFilter.value === 'all' || (resourceFilter.value === 'used' ? usedBy(r.key).length > 0 : r.scope === 'project'))
    && (!q || [r.key, r.title, r.description, r.source, r.build, r.scope_label, ...usedBy(r.key).map((s: any) => s.title)]
      .some(v => String(v ?? '').toLowerCase().includes(q))))
})
const hostOf = (u: string) => u.replace(/^https?:\/\//, '').split('/')[0]
const uploadOpen = ref(false)
async function onResourceSaved(r: any) {
  await refresh()
  toast.add({ severity: 'success', summary: r.version > 1 ? `New version v${r.version}` : 'Resource added',
    detail: `${r.key} is now available to steps ${r.scope === 'shared' ? 'in every project' : 'in this project'}.`, life: 3500 })
}
const codeOpen = ref(false)
const codeKey = ref<string | null>(null)
function viewCode(key: string | null) { if (key) { codeKey.value = key; codeOpen.value = true } }
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div v-if="pl" class="container page">
      <Message v-if="!isReviewer" severity="info" :closable="false" icon="pi pi-lock" class="mb">
        Only project reviewers can change these settings. You're viewing them read-only.
      </Message>
      <UploadOptions :project="project" class="mb" data-tour="setup-upload-options" @saved="refreshProject" />
      <MetadataConfig :project="project" class="mb" data-tour="setup-metadata" @saved="refreshProject" />

      <div class="layout">
        <div class="main">
          <Card class="mb pipes" data-tour="setup-pipelines">
            <template #title><div class="row"><i class="pi pi-sitemap" /> QC pipelines<span class="spacer" />
              <Button v-if="isReviewer" label="New pipeline" icon="pi pi-plus" size="small" outlined @click="openNew" /></div></template>
            <template #subtitle>Datasets run against the default pipeline unless a reviewer pins them to another (in the datasets table or a dataset's panel). A dataset is out of date when its latest QC used another pipeline or an older version.</template>
            <template #content>
              <div class="plist">
                <button v-for="p in pipelines" :key="p.id" :class="['pcard', { on: p.id === pipeline?.id }]" @click="selectPipeline(p.id)">
                  <div class="row"><strong>{{ p.name }}</strong><Tag v-if="p.is_default" value="default" severity="info" class="mini" /><span class="spacer" /><span class="muted small">v{{ p.version }}</span></div>
                  <div class="small muted">{{ p.datasets }} dataset{{ p.datasets === 1 ? '' : 's' }}<span v-if="p.out_of_date" class="ood"> · {{ p.out_of_date }} out of date</span></div>
                </button>
              </div>
              <Message v-if="current?.out_of_date" severity="warn" size="small" :closable="false" class="oodmsg">
                <div class="row">
                  <span><strong>{{ current.out_of_date }} dataset{{ current.out_of_date === 1 ? '' : 's' }}</strong> on {{ pipeline.name }} {{ current.out_of_date === 1 ? 'was' : 'were' }} last QC'd with another pipeline or an older version. Re-run advised.</span>
                  <span class="spacer" />
                  <Button v-if="isReviewer" label="Re-run all out of date" icon="pi pi-refresh" size="small" text severity="warn" class="oodbtn" :loading="rerunning" @click="rerunOutOfDate" />
                </div>
              </Message>
            </template>
          </Card>
          <Card data-tour="setup-steps">
            <template #title>
              <div class="row">
                {{ pipeline?.name }} <Tag v-if="pipeline?.is_default" value="default" severity="info" /> <Tag :value="`v${pl.version}`" severity="secondary" />
                <Tag v-if="dirty" value="Unsaved changes" severity="warn" />
                <span class="spacer" />
                <template v-if="isReviewer">
                  <Button label="Rename" icon="pi pi-pencil" size="small" text severity="secondary" @click="startRename" />
                  <Button v-if="!pipeline?.is_default" label="Make default" icon="pi pi-star" size="small" text severity="secondary"
                          @click="editPipeline({ is_default: true }, `${pipeline.name} is now the default pipeline`)" />
                  <Button label="Reset to template" icon="pi pi-replay" size="small" text severity="secondary" @click="resetTemplate" />
                </template>
              </div>
              <div v-if="pipeline?.description && !renaming" class="pdesc muted">{{ pipeline.description }}</div>
              <form v-if="renaming" class="row rename" @submit.prevent="editPipeline({ ...renameForm }, 'Pipeline updated')">
                <InputText v-model="renameForm.name" size="small" placeholder="Name" />
                <InputText v-model="renameForm.description" size="small" placeholder="Description (optional)" class="grow" />
                <Button type="submit" label="Save" size="small" :disabled="!renameForm.name.trim()" />
                <Button label="Cancel" size="small" text severity="secondary" @click="renaming = false" />
              </form>
            </template>
            <template #subtitle>Steps run top to bottom on each uploaded file, each in the image shown. Consecutive steps with the same image run together as one job; a new job starts where the image changes.</template>
            <template #content>
              <div class="head-row muted"><span /><span>Step</span><span>Image</span><span>If it fails</span><span /></div>
              <TransitionGroup name="list" tag="ol" class="rows compact-ui">
                <li v-for="(e, i) in entries" :key="e.uid" :class="['entry', { custom: stepOf(e.step)?.source === 'consortium' }]">
                  <div v-if="i > 0 && jobOf[i] !== jobOf[i - 1]" class="jobsplit muted small"><i class="pi pi-arrow-right-arrow-left" /> New job: image changes to <span class="mono">{{ imageOf(e) }}</span></div>
                  <div class="entry-row">
                    <span class="idx">{{ i + 1 }}</span>
                    <Select v-model="e.step" :options="stepOptions" option-label="label" option-value="value"
                            option-group-label="label" option-group-children="items" placeholder="Choose a step…"
                            :disabled="!isReviewer" filter class="w-full" overlay-class="compact-overlay" @change="ev => chooseStep(e, ev.value)">
                      <template #optiongroup="{ option }">
                        <div class="og"><i :class="option.owner === 'gwashub' ? 'pi pi-box' : 'pi pi-users'" /> {{ option.label }}</div>
                      </template>
                      <template #option="{ option }">
                        <div class="opt"><div>{{ option.label }}</div><div class="muted small">{{ option.desc }}</div></div>
                      </template>
                      <template #value="{ value, placeholder }">
                        <span v-if="value" class="row nowrap"><Tag v-if="stepOf(value)?.source === 'consortium'" :value="stepOf(value)?.owner" severity="secondary" class="otag" />{{ stepOf(value)?.title }}</span>
                        <span v-else class="muted">{{ placeholder }}</span>
                      </template>
                    </Select>
                    <span v-tooltip.top="images.find(x => x.ref === imageOf(e))?.title ?? 'Set in the step\'s step.yaml'" class="mono img">{{ imageOf(e) || '–' }}</span>
                    <Select v-model="e.on_fail" :options="onFailOptions" :disabled="!isReviewer" class="w-full" overlay-class="compact-overlay" />
                    <div class="acts">
                      <Button v-tooltip.top="'View code'" icon="pi pi-code" text rounded size="small" :disabled="!e.step" aria-label="View code" @click="viewCode(e.step)" />
                      <Button v-tooltip.top="'Details & parameters'" icon="pi pi-angle-down" text rounded size="small" :class="{ rot: expanded.has(e.uid) }" aria-label="Details" @click="toggle(e.uid)" />
                      <template v-if="isReviewer">
                        <Button icon="pi pi-arrow-up" text rounded size="small" :disabled="i === 0" aria-label="Move up" @click="move(i, -1)" />
                        <Button icon="pi pi-arrow-down" text rounded size="small" :disabled="i === entries.length - 1" aria-label="Move down" @click="move(i, 1)" />
                        <Button icon="pi pi-times" text rounded size="small" severity="danger" aria-label="Remove" @click="remove(i)" />
                      </template>
                    </div>
                  </div>
                  <div v-if="expanded.has(e.uid) && stepOf(e.step)" class="details">
                    <dl class="kv">
                      <dt>Owner</dt><dd>{{ stepOf(e.step).owner === 'gwashub' ? 'GWASHub (default step)' : stepOf(e.step).owner }}</dd>
                      <dt>Script path</dt><dd class="mono">{{ stepOf(e.step).script }}</dd>
                      <dt>Image</dt><dd class="mono">{{ imageOf(e) }} <span class="muted">(set in step.yaml)</span></dd>
                    </dl>
                    <div class="contract">
                      <div class="ph muted">The step contract <span class="small">(what the step is given and must write; parameter changes are saved with the pipeline)</span></div>
                      <table class="ct">
                        <tbody>
                          <tr><td class="mono arg">{{ stepOf(e.step).interpreter }}</td><td>{{ stepOf(e.step).language }}</td></tr>
                          <tr><td class="mono arg in1">{{ stepOf(e.step).script_name }}</td><td class="narr">{{ stepOf(e.step).narrative }}</td></tr>
                          <tr>
                            <td class="mono arg in2">--input</td>
                            <td>
                              The dataset in the standard GWASHub format: tab-separated, one variant per row.
                              <button class="more" @click="e2 => showContract(e2, 'input')"><i class="pi pi-info-circle" /> the format</button>
                            </td>
                          </tr>
                          <tr>
                            <td class="mono arg in2">--resources</td>
                            <td>
                              <span v-if="!stepOf(e.step).resources?.length && !stepOf(e.step).optional_resources?.length" class="muted">None</span>
                              <template v-else>
                                <span class="small muted">A folder with only the reference files this step declares:</span>
                                <div class="rlist">
                                  <Tag v-for="k in stepOf(e.step).resources" :key="k" v-tooltip.top="resourceKeys.has(k) ? 'Required' : 'Required, but not uploaded to this project yet'"
                                       :value="k" :severity="resourceKeys.has(k) ? 'secondary' : 'danger'" class="mono rtag" />
                                  <Tag v-for="k in stepOf(e.step).optional_resources" :key="k" v-tooltip.top="'Optional: used when available'" :value="`${k} (optional)`" severity="secondary" class="mono rtag opt" />
                                </div>
                              </template>
                            </td>
                          </tr>
                          <tr>
                            <td class="mono arg in2">--params</td>
                            <td>
                              <span v-if="!stepOf(e.step).param_schema?.length" class="muted">None</span>
                              <ParamForm v-else :schema="stepOf(e.step).param_schema" :model-value="e.params" :disabled="!isReviewer" :id-prefix="e.uid" compact
                                         @update:model-value="v => e.params = v" />
                            </td>
                          </tr>
                          <tr>
                            <td class="mono arg in2">--log</td>
                            <td>
                              A JSON step log. Exit code 0 means the step ran (its verdict says how it went).
                              <button class="more" @click="e2 => showContract(e2, 'log')"><i class="pi pi-info-circle" /> an example</button>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </li>
              </TransitionGroup>
              <div v-if="isReviewer" class="row add">
                <Button label="Add step" icon="pi pi-plus" size="small" outlined @click="addStep" />
                <Button label="Contribute a custom step" icon="pi pi-github" size="small" text severity="secondary" @click="contributeOpen = true" />
              </div>
            </template>
          </Card>

          <Message v-for="w in warnings" :key="w" severity="warn" :closable="false" size="small" icon="pi pi-exclamation-triangle">{{ w }}</Message>

          <Card v-if="isReviewer" data-tour="setup-save">
            <template #content>
              <div class="save-row compact-ui">
                <InputText v-model="note" placeholder="What changed? (shown in version history)" fluid :disabled="!dirty" />
                <Button label="Discard" severity="secondary" text :disabled="!dirty" @click="discard" />
                <Button :label="`Save as v${pl.version + 1}`" icon="pi pi-check" :loading="saving" :disabled="!dirty || blocking" @click="save" />
              </div>
              <small class="muted">Saving pins every resource to its current version; each run also records the exact image builds it used. Datasets already processed keep the version they ran with; re-run them to use the new one.</small>
            </template>
          </Card>

          <Card data-tour="setup-resources">
            <template #title>
              <div class="row"><i class="pi pi-database" /> Resources<span class="spacer" />
                <IconField class="compact-ui rsearch">
                  <InputIcon class="pi pi-search" />
                  <InputText v-model="resourceSearch" placeholder="Search files, titles, sources" fluid />
                </IconField>
                <SelectButton class="compact-ui" v-model="resourceFilter" :options="[{ l: 'All', v: 'all' }, { l: 'Used by this pipeline', v: 'used' }, { l: 'Project only', v: 'project' }]"
                              option-label="l" option-value="v" :allow-empty="false" size="small" />
              </div>
            </template>
            <template #subtitle>Reference files staged read-only into each step's <code>--resources</code> directory. Only the files a step needs are staged for it.</template>
            <template #content>
              <DataTable :value="shownResources" data-key="key" size="small" class="rtable">
                <Column header="File">
                  <template #body="{ data }">
                    <div class="row nowrap"><i :class="data.scope === 'shared' ? 'pi pi-globe' : 'pi pi-folder'" class="muted" /><span class="mono strong">{{ data.key }}</span></div>
                    <div class="muted small rdesc">{{ data.title }} · {{ data.build === 'Any' ? 'any build' : data.build }} · {{ fmtSize(data.size) }}</div>
                  </template>
                </Column>
                <Column header="Scope"><template #body="{ data }"><Tag :value="data.scope_label" :severity="data.scope === 'shared' ? 'secondary' : 'info'" class="scope" /></template></Column>
                <Column header="Version"><template #body="{ data }"><span v-tooltip.top="`sha256 ${data.sha256}`">v{{ data.version }}</span></template></Column>
                <Column header="Used by">
                  <template #body="{ data }">
                    <span v-if="!usedBy(data.key).length" class="muted">–</span>
                    <span v-for="st in usedBy(data.key)" :key="st.key" class="used">{{ st.title }}</span>
                  </template>
                </Column>
                <Column header="Source">
                  <template #body="{ data }">
                    <a v-if="data.source.startsWith('http')" :href="data.source" target="_blank" rel="noopener" class="small">{{ hostOf(data.source) }}</a>
                    <span v-else class="small">{{ data.source === 'Uploaded' ? `Uploaded by ${data.added_by}` : data.source }}</span>
                    <div class="muted tiny">{{ fmtDate(data.added_at) }}</div>
                  </template>
                </Column>
              </DataTable>
              <div class="row add">
                <template v-if="project.is_admin">
                  <Button label="Add resource" icon="pi pi-upload" size="small" outlined @click="uploadOpen = true" />
                  <span class="muted small">For {{ project.short }} only, or shared with every project.</span>
                </template>
                <span v-else class="muted small">Resources are added by platform admins, usually with the step that needs them (see Contribute a custom step).</span>
              </div>
            </template>
          </Card>
        </div>

        <aside class="side">
          <Card>
            <template #title>Version history</template>
            <template #content>
              <ul class="hist">
                <li v-for="h in pl.history" :key="h.version">
                  <div class="row nowrap"><Tag :value="`v${h.version}`" severity="secondary" /><span>{{ h.note }}</span></div>
                  <div class="muted small">{{ fmtDate(h.at) }} · {{ h.by }}</div>
                </li>
              </ul>
            </template>
          </Card>
          <Card>
            <template #title>Custom steps</template>
            <template #content>
              <p class="m0 small">Consortium steps and images live in the GWASHub repository, so every step is reviewed and tested before it runs on anyone's data. To add one, open a pull request.</p>
              <ul v-if="owners.some(o => o !== 'gwashub')" class="cust">
                <li v-for="s in catalog.filter(c => c.owner !== 'gwashub')" :key="s.key">
                  <div class="row nowrap"><Tag :value="s.owner" severity="secondary" class="otag" /><strong>{{ s.title }}</strong><span class="spacer" />
                    <Button icon="pi pi-code" text rounded size="small" aria-label="View code" @click="viewCode(s.key)" />
                  </div>
                  <div class="mono muted small path" :title="s.script">{{ s.script.split('/').pop() }} · {{ s.image }}</div>
                </li>
              </ul>
              <Button label="How to contribute a step" icon="pi pi-github" size="small" outlined class="mt" @click="contributeOpen = true" />
            </template>
          </Card>
        </aside>
      </div>
    </div>
    <ContributeDialog v-model:visible="contributeOpen" :images="images" :contributing-url="pl?.contributing_url" />
    <ResourceUploadDialog v-model:visible="uploadOpen" :project-id="pid" :project-short="project.short" :existing="pl?.resources ?? []" @saved="onResourceSaved" />
    <CodeViewer v-model:visible="codeOpen" :project-id="pid" :step-key="codeKey" />
    <Dialog v-model:visible="newOpen" header="New pipeline" modal :style="{ width: '30rem' }">
      <form class="stack" @submit.prevent="createPipeline">
        <div class="field"><label for="np-name">Name</label><InputText id="np-name" v-model="newForm.name" fluid placeholder="e.g. Custom QC for imputed arrays" /></div>
        <div class="field"><label for="np-desc">Description (optional)</label><InputText id="np-desc" v-model="newForm.description" fluid /></div>
        <div class="field"><label for="np-copy">Start from</label>
          <Select input-id="np-copy" v-model="newForm.copy_from" :options="[{ id: null, name: 'The default template' }, ...pipelines]" option-label="name" option-value="id" fluid />
          <small class="muted">Its current steps and settings are copied; edit and save them afterwards.</small></div>
        <div class="row end"><Button label="Cancel" text severity="secondary" @click="newOpen = false" /><Button type="submit" label="Add pipeline" :disabled="!newForm.name.trim()" /></div>
      </form>
    </Dialog>
    <Popover ref="contractPop" class="contract-pop">
      <div v-if="pl && popWhat === 'input'" class="pop">
        <strong>The GWASHub format</strong>
        <div class="small muted">Tab-separated, one variant per row. Always: {{ pl.contract.required_columns.join(', ') }}; when the upload or study details give them: {{ pl.contract.standard_columns.filter((c: string) => !pl.contract.required_columns.includes(c)).join(', ') }}.</div>
        <pre class="mono">{{ [pl.contract.standard_columns, ...pl.contract.input_sample].map((r: string[]) => r.join('\t')).join('\n') }}</pre>
      </div>
      <div v-else-if="pl" class="pop">
        <strong>A step log (example)</strong>
        <ul class="small">
          <li v-for="f in pl.contract.log_fields" :key="f.key"><span class="mono">{{ f.key }}</span>: {{ f.about }}</li>
        </ul>
        <pre class="mono">{{ JSON.stringify(pl.contract.log_example, null, 2) }}</pre>
      </div>
    </Popover>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
.mb { margin-bottom: 16px; }
.m0 { margin: 0; }
.small { font-size: 0.78rem; }
.tiny { font-size: 0.7rem; }
.rtable .scope { font-size: 0.75rem; padding: 0.15rem 0.45rem; }
.strong { font-weight: 600; }
.jobsplit { display: flex; align-items: center; gap: 6px; padding: 2px 0 6px 34px; }
.otag { font-size: 0.7rem; padding: 1px 6px; }
.mt { margin-top: 12px; }
.path { overflow-wrap: anywhere; }
.nowrap { flex-wrap: nowrap; gap: 8px; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
@media (max-width: 1060px) { .layout { grid-template-columns: 1fr; } }
.main, .side { display: flex; flex-direction: column; gap: 12px; }
.head-row, .entry-row { display: grid; grid-template-columns: 28px minmax(0, 2fr) minmax(0, 1.5fr) 136px 182px; gap: 10px; align-items: center; }
.head-row { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .04em; padding: 0 10px 6px; }
.rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.entry { border: 1px solid var(--gh-border); border-radius: 8px; padding: 8px 10px; background: var(--gh-surface); }
.entry.custom { border-left: 3px solid #6366f1; }
.idx { width: 24px; height: 24px; border-radius: 50%; background: var(--gh-bg); display: inline-flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 600; }
.acts { display: flex; justify-content: flex-end; }
.rot :deep(.pi) { transform: rotate(180deg); }
.w-full { width: 100%; }
.mono-input :deep(input), .mono-input :deep(.p-select-label) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }
.og { font-weight: 600; display: flex; gap: 6px; align-items: center; }
.opt { display: flex; flex-direction: column; max-width: 420px; white-space: normal; }
.details { margin: 10px 0 2px 38px; font-size: 0.8rem; }
.details .mono { font-size: 0.95em; }
.rsearch { width: 240px; font-weight: 400; }
.compact-ui.p-selectbutton :deep(.p-togglebutton) { font-size: 0.8rem; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 8px 0; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; word-break: break-all; }
.rtag { font-size: 0.7rem; margin: 0 4px 4px 0; }
.pipes { margin-bottom: 16px; }
.plist { display: flex; flex-wrap: wrap; gap: 10px; }
.pcard { text-align: left; border: 1px solid var(--gh-border); border-radius: 8px; background: var(--p-content-background, #fff); padding: 8px 12px; min-width: 200px; cursor: pointer; font: inherit; color: inherit; display: flex; flex-direction: column; gap: 4px; }
.pcard.on { border-color: var(--gh-accent); box-shadow: 0 0 0 1px var(--gh-accent); }
.pcard .row { gap: 6px; }
.ood { color: #b45309; font-weight: 600; }
.oodmsg { margin-top: 12px; }
.oodmsg .row { gap: 10px; width: 100%; }
.oodbtn { padding-top: 0; padding-bottom: 0; }
.pcard .mini { font-size: 0.7rem; padding: 0 6px; }
.pdesc { font-size: 0.85rem; font-weight: 400; margin-top: 2px; }
.rename { gap: 6px; margin-top: 6px; font-weight: 400; }
.img { font-size: 0.8rem; padding: 0 4px; color: var(--gh-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stack { display: flex; flex-direction: column; gap: 12px; }
.contract { border-top: 1px dashed var(--gh-border); padding-top: 8px; margin-top: 4px; display: flex; flex-direction: column; gap: 6px; }
.pop { display: flex; flex-direction: column; gap: 6px; max-width: min(760px, 90vw); }
.pop pre { background: var(--gh-bg); border: 1px solid var(--gh-border); border-radius: 6px; padding: 8px; font-size: 0.75rem; overflow-x: auto; margin: 0; }
.pop ul { margin: 0; padding-left: 18px; }
.rlist { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.rtag.opt { opacity: 0.75; }
.more { background: none; border: 0; color: var(--gh-accent); cursor: pointer; font: inherit; padding: 0; margin-left: 4px; }
.add { margin-top: 12px; gap: 8px; }
.save-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.save-row :deep(.p-button) { white-space: nowrap; flex-shrink: 0; }
.hist, .cust { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 12px; }
.snippet { background: var(--gh-bg); border: 1px solid var(--gh-border); border-radius: 6px; padding: 8px 10px; font-size: 0.72rem; overflow-x: auto; margin: 8px 0; }
.rdesc { margin-left: 22px; }
.used { display: inline-block; font-size: 0.75rem; background: var(--gh-bg); border: 1px solid var(--gh-border); border-radius: 10px; padding: 1px 8px; margin: 0 4px 4px 0; white-space: nowrap; }
.rtable :deep(td) { vertical-align: top; }
.list-move { transition: transform .2s; }
@media (max-width: 760px) {
  .head-row { display: none; }
  .entry-row { grid-template-columns: 28px 1fr; }
  .entry-row > :nth-child(n+3) { grid-column: 2; }
}
</style>
