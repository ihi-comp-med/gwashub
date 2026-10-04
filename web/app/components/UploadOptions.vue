<script setup lang="ts">
// Project upload options: phenotypes (code + description) offered in the upload form, allowed ancestries,
// expected sex strata.
const props = defineProps<{ project: any }>()
const emit = defineEmits<{ saved: [] }>()
const toast = useToast()
const isReviewer = computed(() => props.project.role === 'reviewer')
const ALL_ANC = [
  { v: 'EUR', l: 'European' }, { v: 'AFR', l: 'African' }, { v: 'EAS', l: 'East Asian' },
  { v: 'SAS', l: 'South Asian' }, { v: 'AMR', l: 'Admixed American' }, { v: 'Multi', l: 'Multi-ancestry' },
]
const ALL_SEX = ['Combined', 'Female', 'Male']

const form = reactive({ phenotypes: [] as string[], phenotype_descriptions: {} as Record<string, string>, ancestries: [] as string[], sexes: [] as string[] })
const original = ref('')
function reset() {
  Object.assign(form, { phenotypes: [...props.project.phenotypes], phenotype_descriptions: { ...(props.project.phenotype_descriptions ?? {}) },
    ancestries: [...props.project.ancestries], sexes: [...props.project.sexes] })
  original.value = JSON.stringify(form)
}
watch(() => props.project, reset, { immediate: true })
const dirty = computed(() => JSON.stringify(form) !== original.value)
const newPheno = ref('')
const newDesc = ref('')
function addPheno() {
  const p = newPheno.value.trim()
  if (p && !form.phenotypes.includes(p)) form.phenotypes.push(p)
  if (p && newDesc.value.trim()) form.phenotype_descriptions[p] = newDesc.value.trim()
  newPheno.value = ''
  newDesc.value = ''
}
function removePheno(i: number) {
  const [p] = form.phenotypes.splice(i, 1)
  if (p) delete form.phenotype_descriptions[p]
}
// CSV / TSV import: one phenotype per line, "code,description" (a header line starting with "code" is skipped).
const csvInput = ref<HTMLInputElement | null>(null)
async function importCsv(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  const lines = (await f.text()).split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  let added = 0
  for (const line of lines) {
    const [code, ...rest] = line.split(line.includes('\t') ? '\t' : ',').map(x => x.trim().replace(/^"|"$/g, ''))
    if (!code || /^code$/i.test(code)) continue
    if (!form.phenotypes.includes(code)) { form.phenotypes.push(code); added++ }
    if (rest.join(',').trim()) form.phenotype_descriptions[code] = rest.join(',').trim()
  }
  toast.add({ severity: 'info', summary: `Imported ${lines.length} line${lines.length === 1 ? '' : 's'}`, detail: `${added} new phenotype${added === 1 ? '' : 's'}; save to keep them.`, life: 4000 })
  if (csvInput.value) csvInput.value.value = ''
}
function toggle(list: string[], v: string) {
  const i = list.indexOf(v)
  i >= 0 ? list.splice(i, 1) : list.push(v)
}
const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const r = await api(`/projects/${props.project.id}/upload-options`, { method: 'PUT', body: form })
    toast.add({ severity: 'success', summary: 'Upload options saved', life: 2500 })
    if (r.removed_but_in_use.length)
      toast.add({ severity: 'warn', summary: 'Removed phenotypes still in use', detail: `${r.removed_but_in_use.join(', ')}: existing datasets keep it, but new uploads can't choose it.`, life: 7000 })
    emit('saved')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not save', detail: e.message, life: 5000 }) }
  finally { saving.value = false }
}
</script>

<template>
  <Card>
    <template #title><div class="row"><i class="pi pi-list-check" /> Upload options<span class="spacer" /><Tag v-if="dirty" value="Unsaved changes" severity="warn" /></div></template>
    <template #subtitle>What uploaders can choose when submitting a dataset. Uploads with anything else are rejected.</template>
    <template #content>
      <div class="opts">
        <div>
          <h4>Phenotypes</h4>
          <table class="ph">
            <thead><tr><th>Code</th><th>Description</th><th /></tr></thead>
            <tbody>
              <tr v-for="(p, i) in form.phenotypes" :key="p">
                <td class="mono">{{ p }}</td>
                <td><InputText v-if="isReviewer" v-model="form.phenotype_descriptions[p]" size="small" fluid placeholder="Description" />
                  <span v-else>{{ form.phenotype_descriptions[p] || '–' }}</span></td>
                <td><Button v-if="isReviewer" icon="pi pi-times" text rounded size="small" severity="secondary" :aria-label="`Remove ${p}`" @click="removePheno(i)" /></td>
              </tr>
              <tr v-if="!form.phenotypes.length"><td colspan="3" class="muted small">None: add at least one.</td></tr>
            </tbody>
          </table>
          <form v-if="isReviewer" class="row add" @submit.prevent="addPheno">
            <InputText v-model="newPheno" placeholder="Code, e.g. HFmrEF" size="small" class="code" />
            <InputText v-model="newDesc" placeholder="Description" size="small" class="grow" />
            <Button type="submit" icon="pi pi-plus" size="small" outlined :disabled="!newPheno.trim()" aria-label="Add phenotype" />
          </form>
          <div v-if="isReviewer" class="row imp">
            <input ref="csvInput" type="file" accept=".csv,.tsv,.txt" hidden @change="importCsv">
            <Button label="Import CSV" icon="pi pi-upload" size="small" text @click="csvInput?.click()" />
            <span class="muted small">one per line: code,description</span>
          </div>
        </div>
        <div>
          <h4>Allowed ancestries</h4>
          <div class="checks">
            <label v-for="a in ALL_ANC" :key="a.v" class="row check">
              <Checkbox :model-value="form.ancestries.includes(a.v)" binary :disabled="!isReviewer" @update:model-value="toggle(form.ancestries, a.v)" />
              <span><span class="mono">{{ a.v }}</span> <span class="muted small">{{ a.l }}</span></span>
            </label>
          </div>
        </div>
        <div>
          <h4>Expected sex strata</h4>
          <div class="checks">
            <label v-for="s in ALL_SEX" :key="s" class="row check">
              <Checkbox :model-value="form.sexes.includes(s)" binary :disabled="!isReviewer" @update:model-value="toggle(form.sexes, s)" />
              <span>{{ s }}</span>
            </label>
          </div>
          <p class="muted small">e.g. tick only Female and Male for sex-stratified analyses.</p>
        </div>
      </div>
      <div v-if="isReviewer" class="row end">
        <Button label="Discard" text severity="secondary" size="small" :disabled="!dirty" @click="reset" />
        <Button label="Save upload options" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty || !form.phenotypes.length || !form.ancestries.length || !form.sexes.length" @click="save" />
      </div>
    </template>
  </Card>
</template>

<style scoped>
.opts { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 24px; }
@media (max-width: 900px) { .opts { grid-template-columns: 1fr; } }
h4 { margin: 0 0 10px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .04em; color: var(--gh-muted); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.add { flex-wrap: nowrap; gap: 6px; margin-top: 8px; }
.add .code { width: 9rem; }
.grow { flex: 1; }
.imp { gap: 6px; margin-top: 4px; }
.ph { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.ph th { text-align: left; font-size: 0.72rem; color: var(--gh-muted); font-weight: 600; padding: 4px 6px; }
.ph td { padding: 3px 6px; border-top: 1px solid var(--gh-border); }
.ph td:first-child { white-space: nowrap; }
.checks { display: flex; flex-direction: column; gap: 8px; }
.check { flex-wrap: nowrap; gap: 8px; cursor: pointer; font-size: 0.9rem; }
.small { font-size: 0.78rem; }
.end { justify-content: flex-end; margin-top: 12px; gap: 6px; }
</style>
