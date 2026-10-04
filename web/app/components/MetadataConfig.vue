<script setup lang="ts">
// Which study-metadata fields this project collects, and which are required at upload. All fields are
// defined in the repo (api/gwashub_api/metadata_schema.py); a platform admin sets this up for a new project
// and the project's reviewers can change it. Datasets missing a newly required field show as
// "metadata incomplete"; nothing is blocked.
const props = defineProps<{ project: any }>()
const emit = defineEmits<{ saved: [] }>()
const toast = useToast()
const { schema, units, defaultConfig } = useMetadataSchema()
const canEdit = computed(() => props.project.role === 'reviewer' || props.project.is_admin)

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
</style>
