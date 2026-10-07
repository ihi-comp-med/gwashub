<script setup lang="ts">
// Platform admins delete a project and everything in it. Two safeguards before the request: the project's name typed
// out (pasting and dropping text are blocked), then a second, explicit confirmation. The server checks the name too.
const props = defineProps<{ project: any, datasets: number }>()
const visible = defineModel<boolean>('visible', { default: false })
const toast = useToast()
const typed = ref('')
const step = ref<1 | 2>(1)
const busy = ref(false)
const matches = computed(() => typed.value === props.project.name)
watch(visible, (v) => { if (v) { typed.value = ''; step.value = 1 } })
function blocked(e: Event) {
  e.preventDefault()
  toast.add({ severity: 'warn', summary: 'Type the name', detail: 'Pasting is turned off here: type the project name yourself.', life: 3000 })
}
async function remove() {
  busy.value = true
  try {
    await api(`/projects/${props.project.id}`, { method: 'DELETE', body: { confirm: typed.value } })
    toast.add({ severity: 'success', summary: `${props.project.short} deleted`, life: 4000 })
    visible.value = false
    useState('current-project').value = { pid: null, data: null, error: null }
    await navigateTo('/projects')
  }
  catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not delete the project', detail: e.message, life: 6000 })
    step.value = 1
  }
  finally { busy.value = false }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Delete project" :style="{ width: '34rem' }" :closable="!busy">
    <div class="stack">
      <p class="m0">This permanently deletes <strong>{{ project.name }}</strong> and everything in it:</p>
      <ul class="m0 what">
        <li>{{ datasets }} dataset{{ datasets === 1 ? '' : 's' }}: the uploaded files, every QC run and its results</li>
        <li>all meta-analyses and their results</li>
        <li>cohorts, study metadata, QC pipelines and the project's own resources</li>
        <li>the members' access to it (their accounts are kept)</li>
      </ul>
      <template v-if="step === 1">
        <label for="del-confirm" class="small">Type the project name, <strong class="mono">{{ project.name }}</strong>, to continue</label>
        <InputText id="del-confirm" v-model="typed" autocomplete="off" spellcheck="false" autocapitalize="off" fluid
                   @paste="blocked" @drop="blocked" @keydown.enter.prevent="matches && (step = 2)" />
        <div class="row end">
          <Button label="Cancel" text severity="secondary" size="small" @click="visible = false" />
          <Button label="Continue" severity="danger" size="small" outlined :disabled="!matches" @click="step = 2" />
        </div>
      </template>
      <template v-else>
        <Message severity="error" size="small" :closable="false" icon="pi pi-exclamation-triangle">
          Are you sure? This can't be undone from the website. Files in storage go too; on AWS an administrator can recover
          them from the bucket's version history for a limited time only.
        </Message>
        <div class="row end">
          <Button label="Back" text severity="secondary" size="small" :disabled="busy" @click="step = 1" />
          <Button :label="`Yes, delete ${project.short} permanently`" icon="pi pi-trash" severity="danger" size="small" :loading="busy" @click="remove" />
        </div>
      </template>
    </div>
  </Dialog>
</template>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 10px; font-size: 0.9rem; }
.what { padding-left: 1.2rem; color: var(--gh-muted); }
.end { justify-content: flex-end; gap: 6px; }
.m0 { margin: 0; }
</style>
