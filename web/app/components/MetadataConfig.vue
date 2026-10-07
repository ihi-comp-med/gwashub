<script setup lang="ts">
// Which study-metadata fields this project collects, and which are required at upload. The built-in fields are
// defined in the repo (api/gwashub_api/metadata_schema.py); reviewers can add the project's own fields too (per cohort
// or per dataset; renamed freely, hidden instead of deleted so saved values are kept). Datasets missing a newly
// required field show as "metadata incomplete"; nothing is blocked.
const props = defineProps<{ project: any }>()
const emit = defineEmits<{ saved: [] }>()
const toast = useToast()
const { schema, units, defaultConfig } = useMetadataSchema()
const canEdit = computed(() => props.project.role === 'reviewer')

const shown = ref<Set<string>>(new Set())
const required = ref<Set<string>>(new Set())
const original = ref('')
function reset() {
  shown.value = new Set(props.project.metadata_config?.shown ?? [])
  required.value = new Set(props.project.metadata_config?.required ?? [])
  original.value = state()
}
const state = () => JSON.stringify([[...shown.value].sort(), [...required.value].sort()])
watch(() => props.project, reset, { immediate: true })
const dirty = computed(() => state() !== original.value)

const sections = computed(() => (schema.value ?? []).map((sec: any) => ({
  ...sec, units: (units.value ?? []).filter((u: any) => u.section === sec.key),
})))
function setShown(k: string, v: boolean) {
  const s = new Set(shown.value)
  v ? s.add(k) : s.delete(k)
  shown.value = s
  if (!v) setRequired(k, false)
}
function setRequired(k: string, v: boolean) {
  const r = new Set(required.value)
  v ? r.add(k) : r.delete(k)
  required.value = r
}
function useDefaults() { shown.value = new Set(defaultConfig.value?.shown ?? []); required.value = new Set() }
function showAll() { shown.value = new Set((units.value ?? []).map((u: any) => u.key)) }
// ---- the project's own fields ----
const FIELD_TYPES = [{ v: 'text', l: 'Text' }, { v: 'number', l: 'Number' }, { v: 'integer', l: 'Whole number' },
  { v: 'yesno', l: 'Yes / no' }, { v: 'select', l: 'Choice from a list' }, { v: 'date', l: 'Date' }]
const LEVELS = [{ v: 'cohort', l: 'Per cohort' }, { v: 'dataset', l: 'Per dataset' }]
const typeLabel = (t: string) => FIELD_TYPES.find(x => x.v === t)?.l ?? t
const custom = ref<any[]>([])
const customOriginal = ref('')
function resetCustom() {
  custom.value = (props.project.metadata_custom ?? []).map((c: any) => ({ ...c, options: [...(c.options ?? [])], newChoice: '' }))
  customOriginal.value = customState()
}
const customState = () => JSON.stringify(custom.value.map(({ newChoice, ...c }) => c))
watch(() => props.project, resetCustom, { immediate: true })
const customDirty = computed(() => customState() !== customOriginal.value)
const blank = () => ({ label: '', level: 'cohort', type: 'text', choices: '' })
const draft = ref(blank())
function addField() {
  const label = draft.value.label.trim()
  if (!label) return
  const options = draft.value.type === 'select' ? draft.value.choices.split(',').map(o => o.trim()).filter(Boolean) : []
  custom.value.push({ label, level: draft.value.level, type: draft.value.type, options, hidden: false, newChoice: '' })
  draft.value = blank()
}
function addChoice(c: any) {
  const o = c.newChoice.trim()
  if (o && !c.options.includes(o)) c.options.push(o)
  c.newChoice = ''
}
const savingCustom = ref(false)
async function saveCustom() {
  savingCustom.value = true
  try {
    await api(`/projects/${props.project.id}/metadata-fields`, { method: 'PUT', body: { fields: custom.value.map(({ newChoice, ...c }) => c) } })
    toast.add({ severity: 'success', summary: 'Project fields saved', detail: 'New fields are shown; tick Required in the table if needed.', life: 3500 })
    emit('saved')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save the project fields', detail: e.message, life: 6000 }) }
  finally { savingCustom.value = false }
}

const saving = ref(false)
async function save() {
  saving.value = true
  try {
    await api(`/projects/${props.project.id}/metadata-config`, { method: 'PUT', body: { shown: [...shown.value], required: [...required.value] } })
    toast.add({ severity: 'success', summary: 'Metadata fields saved', detail: 'The upload form, dataset and cohort pages now use these fields.', life: 3500 })
    emit('saved')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 5000 }) }
  finally { saving.value = false }
}
</script>

<template>
  <Card>
    <template #title><div class="row"><i class="pi pi-clipboard" /> Metadata fields<span class="spacer" /><Tag v-if="dirty" value="Unsaved changes" severity="warn" /></div></template>
    <template #subtitle>Which study details this project collects. Cohort fields are entered once per cohort; dataset fields per upload. Required fields must be filled in to upload; existing datasets missing one are marked "metadata incomplete".</template>
    <template #content>
      <div v-if="canEdit" class="row quick">
        <Button label="HERMES default set" size="small" text @click="useDefaults" />
        <Button label="Show every field" size="small" text @click="showAll" />
        <span class="muted small">{{ shown.size }} shown · {{ required.size }} required</span>
      </div>
      <div class="secs">
        <section v-for="sec in sections" :key="sec.key">
          <h4>{{ sec.title }} <Tag :value="sec.level === 'cohort' ? 'per cohort' : 'per dataset'" :severity="sec.level === 'cohort' ? 'info' : 'secondary'" class="lvl" /></h4>
          <table>
            <thead><tr><th /><th>Show</th><th>Required</th></tr></thead>
            <tbody>
              <tr v-for="u in sec.units" :key="u.key">
                <td :class="{ muted: !shown.has(u.key) }">{{ u.label }}</td>
                <td><Checkbox :model-value="shown.has(u.key)" binary :disabled="!canEdit" :aria-label="`Show ${u.label}`" @update:model-value="v => setShown(u.key, v)" /></td>
                <td><Checkbox :model-value="required.has(u.key)" binary :disabled="!canEdit || !shown.has(u.key)" :aria-label="`Require ${u.label}`" @update:model-value="v => setRequired(u.key, v)" /></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
      <div v-if="canEdit" class="row end">
        <Button label="Discard" text severity="secondary" size="small" :disabled="!dirty" @click="reset" />
        <Button label="Save metadata fields" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty" @click="save" />
      </div>
      <p v-else class="muted small">Only reviewers can change which fields are collected.</p>

      <div class="custom">
        <h4>Project fields <span class="muted small cap">fields this project adds to the built-in ones</span><span class="spacer" /><Tag v-if="customDirty" value="Unsaved changes" severity="warn" class="lvl" /></h4>
        <table v-if="custom.length" class="ctab">
          <thead><tr><th class="l">Name</th><th class="l">Level</th><th class="l">Type</th><th class="l">Choices</th><th /></tr></thead>
          <tbody>
            <tr v-for="(c, i) in custom" :key="c.key ?? `new-${i}`" :class="{ hidden: c.hidden }">
              <td class="l"><InputText v-if="canEdit" v-model="c.label" size="small" fluid :aria-label="`Name of ${c.label}`" /><span v-else>{{ c.label }}</span>
                <small v-if="c.hidden" class="muted">Hidden: not on the forms; saved values are kept</small></td>
              <td class="l">{{ LEVELS.find(x => x.v === c.level)?.l }}</td>
              <td class="l">{{ typeLabel(c.type) }}<span v-if="!c.key" class="muted small"> (new)</span></td>
              <td class="l">
                <template v-if="c.type === 'select'">
                  <Tag v-for="o in c.options" :key="o" :value="o" severity="secondary" class="opt" />
                  <InputText v-if="canEdit" v-model="c.newChoice" size="small" placeholder="Add a choice" class="addc" @keydown.enter.prevent="addChoice(c)" @blur="addChoice(c)" />
                </template>
                <span v-else class="muted">–</span>
              </td>
              <td>
                <template v-if="canEdit">
                  <Button v-if="!c.key" icon="pi pi-times" text rounded size="small" severity="secondary" aria-label="Remove this new field" @click="custom.splice(i, 1)" />
                  <Button v-else :label="c.hidden ? 'Unhide' : 'Hide'" :icon="c.hidden ? 'pi pi-eye' : 'pi pi-eye-slash'" text size="small" severity="secondary"
                          v-tooltip.top="c.hidden ? undefined : 'Takes the field off the forms; its saved values are kept'" @click="c.hidden = !c.hidden" />
                </template>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted small">None yet.</p>
        <form v-if="canEdit" class="row addf" @submit.prevent="addField">
          <InputText v-model="draft.label" size="small" placeholder="Field name, e.g. Hospital data collected from" class="grow" />
          <Select v-model="draft.level" :options="LEVELS" option-label="l" option-value="v" size="small" aria-label="Level" />
          <Select v-model="draft.type" :options="FIELD_TYPES" option-label="l" option-value="v" size="small" aria-label="Type" />
          <InputText v-if="draft.type === 'select'" v-model="draft.choices" size="small" placeholder="Choices, comma-separated" />
          <Button type="submit" icon="pi pi-plus" size="small" outlined :disabled="!draft.label.trim() || (draft.type === 'select' && !draft.choices.trim())" aria-label="Add field" />
        </form>
        <div v-if="canEdit" class="row end">
          <Button label="Discard" text severity="secondary" size="small" :disabled="!customDirty" @click="resetCustom" />
          <Button label="Save project fields" icon="pi pi-check" size="small" :loading="savingCustom" :disabled="!customDirty" @click="saveCustom" />
        </div>
      </div>
    </template>
  </Card>
</template>

<style scoped>
.quick { gap: 6px; margin-bottom: 8px; }
.secs { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px 28px; }
h4 { margin: 0 0 6px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .04em; color: var(--gh-muted); display: flex; align-items: center; gap: 6px; }
.lvl { font-size: 0.65rem; text-transform: none; letter-spacing: 0; padding: 0 6px; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { font-size: 0.7rem; color: var(--gh-muted); font-weight: 600; text-align: center; padding: 2px 6px; }
td { padding: 4px 6px; border-top: 1px solid var(--gh-border); }
td:not(:first-child) { text-align: center; width: 4.5rem; }
.small { font-size: 0.78rem; }
.end { justify-content: flex-end; margin-top: 12px; gap: 6px; }
.custom { border-top: 1px dashed var(--gh-border); margin-top: 16px; padding-top: 12px; }
.custom h4 .cap { text-transform: none; letter-spacing: 0; font-weight: 400; }
.ctab td.l, .ctab th.l { text-align: left; width: auto; }
.ctab tr.hidden td { opacity: 0.6; }
.ctab small { display: block; }
.opt { margin: 0 4px 4px 0; font-size: 0.75rem; padding: 0 6px; }
.ctab :deep(.p-inputtext) { font-size: inherit; }
.addc { width: 9rem; }
.addf { gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.grow { flex: 1; min-width: 14rem; }
</style>
