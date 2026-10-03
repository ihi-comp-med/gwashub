<script setup lang="ts">
// Reviewer-only: add a project resource. Files are checksummed and immutable; re-using a name creates a new version.
const props = defineProps<{ projectId: string, existing: any[] }>()
const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ saved: [r: any] }>()
const toast = useToast()

const form = reactive({ key: '', title: '', description: '', build: 'GRCh38', attest: false })
const file = ref<File | null>(null)
const saving = ref(false)

watch(visible, (open) => {
  if (!open) return
  Object.assign(form, { key: '', title: '', description: '', build: 'GRCh38', attest: false })
  file.value = null
})
function onSelect(e: { files: File[] }) {
  file.value = e.files[0] ?? null
  if (file.value && !form.key) form.key = file.value.name
}
const existingVersion = computed(() => props.existing.find(r => r.key === form.key))
const sharedClash = computed(() => existingVersion.value?.scope === 'shared')
const ready = computed(() => !!file.value && !!form.key && !!form.title && form.attest && !sharedClash.value)

async function save() {
  saving.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value!)
    fd.append('meta', JSON.stringify(form))
    const r = await api(`/projects/${props.projectId}/resources`, { method: 'POST', body: fd })
    emit('saved', r)
    visible.value = false
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Upload failed', detail: e.message, life: 5000 }) }
  finally { saving.value = false }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Add a project resource" class="csd" :draggable="false">
    <p class="muted intro">Reference files are staged read-only into a step's <code>--resources</code> directory. Only reviewers can add them. They're checksummed and never overwritten: re-using a file name creates a new version, and saved pipelines keep the version they were saved with.</p>
    <div class="mb">
      <FileUpload mode="advanced" :auto="false" :show-upload-button="false" :show-cancel-button="false" choose-label="Choose file" @select="onSelect">
        <template #empty><div class="drop"><i class="pi pi-cloud-upload" /><p class="m0">Drag a file here.</p></div></template>
      </FileUpload>
    </div>
    <div class="grid grid-2">
      <div class="field">
        <label for="rs-key">File name seen by steps</label>
        <InputText id="rs-key" v-model="form.key" class="mono" />
        <small v-if="sharedClash" class="err">That name is a shared GWASHub resource. Choose a different name.</small>
        <small v-else-if="existingVersion" class="warn">Exists already: this will become version {{ existingVersion.version + 1 }}.</small>
        <small v-else class="muted">Steps read it as <span class="mono">&lt;resources&gt;/{{ form.key || '…' }}</span></small>
      </div>
      <div class="field">
        <label for="rs-build">Genome build</label>
        <Select input-id="rs-build" v-model="form.build" :options="['GRCh37', 'GRCh38', 'Any']" />
      </div>
    </div>
    <div class="field">
      <label for="rs-title">Title</label>
      <InputText id="rs-title" v-model="form.title" placeholder="e.g. Long-range LD exclusion regions" />
    </div>
    <div class="field">
      <label for="rs-desc">Description <span class="muted">(optional)</span></label>
      <Textarea id="rs-desc" v-model="form.description" rows="2" auto-resize />
    </div>
    <div class="row attest">
      <Checkbox v-model="form.attest" input-id="rs-attest" binary />
      <label for="rs-attest">This file contains <strong>no individual-level data</strong>, and the project is allowed to use it.</label>
    </div>
    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="visible = false" />
      <Button label="Add resource" icon="pi pi-check" :loading="saving" :disabled="!ready" @click="save" />
    </template>
  </Dialog>
</template>

<style scoped>
.intro { margin: 0 0 16px; font-size: 0.9rem; }
.mb { margin-bottom: 16px; }
.m0 { margin: 0; }
.drop { text-align: center; padding: 16px; }
.drop i { font-size: 1.6rem; color: var(--gh-accent); }
.attest { flex-wrap: nowrap; align-items: flex-start; font-size: 0.9rem; }
.err { color: #b91c1c; }
.warn { color: #b45309; }
</style>
