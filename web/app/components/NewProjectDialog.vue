<script setup lang="ts">
// Platform admins create projects. The creator becomes the first reviewer and can then invite people.
const visible = defineModel<boolean>('visible', { default: false })
const toast = useToast()
const form = reactive({ id: '', name: '', short: '', description: '', phenotypes: [] as string[] })
const idTouched = ref(false)
const busy = ref(false)
watch(visible, (v) => { if (v) { Object.assign(form, { id: '', name: '', short: '', description: '', phenotypes: [] }); idTouched.value = false } })
watch(() => form.short, (s) => { if (!idTouched.value) form.id = s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) })
const idOk = computed(() => /^[a-z][a-z0-9-]{1,39}$/.test(form.id))
const newPheno = ref('')
function addPheno() {
  const p = newPheno.value.trim()
  if (p && !form.phenotypes.includes(p)) form.phenotypes.push(p)
  newPheno.value = ''
}
async function submit() {
  busy.value = true
  try {
    const r = await api<{ id: string }>('/projects', { method: 'POST', body: form })
    toast.add({ severity: 'success', summary: `${form.short} created`, detail: "You're its first reviewer. Invite members from the project page.", life: 5000 })
    visible.value = false
    navigateTo(`/projects/${r.id}`)
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not create the project', detail: e.message, life: 6000 }) }
  finally { busy.value = false }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="New project" class="csd" :draggable="false">
    <form class="form" @submit.prevent="submit">
      <div class="field"><label for="np-name">Name</label>
        <InputText id="np-name" v-model="form.name" placeholder="e.g. HERMES 3 heart failure GWAS meta-analysis" fluid />
      </div>
      <div class="grid grid-2">
        <div class="field"><label for="np-short">Short name</label><InputText id="np-short" v-model="form.short" placeholder="e.g. HERMES 3" fluid /></div>
        <div class="field"><label for="np-id">Project ID (used in links)</label>
          <InputText id="np-id" v-model="form.id" class="mono" :invalid="!!form.id && !idOk" fluid @input="idTouched = true" />
          <small class="muted">Lower-case letters, digits and hyphens. Can't be changed later.</small>
        </div>
      </div>
      <div class="field"><label for="np-desc">Description</label><Textarea id="np-desc" v-model="form.description" rows="3" fluid /></div>
      <div class="field"><label>Phenotypes</label>
        <div class="row chips">
          <Chip v-for="(p, i) in form.phenotypes" :key="p" :label="p" removable @remove="form.phenotypes.splice(i, 1)" />
        </div>
        <div class="row add">
          <InputText v-model="newPheno" placeholder="e.g. All-cause HF" class="grow" @keydown.enter.prevent="addPheno" />
          <Button label="Add" icon="pi pi-plus" size="small" outlined :disabled="!newPheno.trim()" @click="addPheno" />
        </div>
        <small class="muted">What uploaders choose from. Reviewers can change these later under Upload options.</small>
      </div>
      <div class="row end">
        <Button label="Cancel" text severity="secondary" :disabled="busy" @click="visible = false" />
        <Button type="submit" label="Create project" icon="pi pi-check" :loading="busy" :disabled="!form.name || !form.short || !idOk || !form.phenotypes.length" />
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 12px; }
.end { justify-content: flex-end; }
.chips { gap: 6px; flex-wrap: wrap; }
.add { flex-wrap: nowrap; gap: 8px; }
.grow { flex: 1; }
</style>
