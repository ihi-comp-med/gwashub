<script setup lang="ts">
// Platform admins only: add a reference file, for this project or shared with every project. Uploaded in parts
// (straight to S3 on AWS), so multi-GB files work. Files are immutable; re-using a name creates a new version.
const props = defineProps<{ projectId: string, projectShort: string, existing: any[] }>()
const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ saved: [r: any] }>()
const toast = useToast()

const form = reactive({ scope: 'project', key: '', title: '', description: '', build: 'GRCh38', attest: false })
const file = ref<File | null>(null)
const saving = ref(false)
const progress = ref(0)

watch(visible, (open) => {
  if (!open) return
  Object.assign(form, { scope: 'project', key: '', title: '', description: '', build: 'GRCh38', attest: false })
  file.value = null
  progress.value = 0
})
function onSelect(e: { files: File[] }) {
  file.value = e.files[0] ?? null
  if (file.value && !form.key) form.key = file.value.name
}
const existingVersion = computed(() => props.existing.find(r => r.key === form.key && (form.scope === 'shared' ? r.scope === 'shared' : true)))
const sharedClash = computed(() => form.scope === 'project' && existingVersion.value?.scope === 'shared')
const keyOk = computed(() => /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(form.key))
const ready = computed(() => !!file.value && keyOk.value && !!form.title && form.attest && !sharedClash.value)

async function save() {
  saving.value = true
  progress.value = 0
  try {
    const f = file.value!
    const start = await api<{ ticket: string, part_size: number, parts: number }>('/resources/uploads', { method: 'POST',
      body: { scope: form.scope === 'shared' ? 'shared' : props.projectId, key: form.key, title: form.title, description: form.description,
              build: form.build, file_size: f.size, attest: form.attest } })
    const parts = await uploadInParts(f, {
      partSize: start.part_size, parts: start.parts, onProgress: (x) => { progress.value = Math.round(100 * x) },
      urlFor: async n => (await api<{ urls: Record<string, string> }>('/resources/uploads/urls', { method: 'POST', body: { ticket: start.ticket, parts: [n] } })).urls[String(n)]!,
    })
    const r = await api('/resources/uploads/complete', { method: 'POST', body: { ticket: start.ticket, parts } })
    emit('saved', r)
    visible.value = false
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Upload failed', detail: e.message, life: 5000 }) }
  finally { saving.value = false }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Add a resource" class="csd" :draggable="false">
    <p class="muted intro">Reference files are staged read-only into a step's <code>--resources</code> directory. Only platform admins add them
      (alongside the step that needs them, see Contribute a custom step). They're never overwritten: re-using a file name creates a new version,
      and saved pipelines keep the version they were saved with.</p>
    <div class="field">
      <label>Available to</label>
      <SelectButton v-model="form.scope" :options="[{ l: `${projectShort} only`, v: 'project' }, { l: 'Every project (shared)', v: 'shared' }]"
                    option-label="l" option-value="v" :allow-empty="false" />
    </div>
    <div class="mb">
      <FileUpload mode="advanced" :auto="false" :show-upload-button="false" :show-cancel-button="false" choose-label="Choose file" @select="onSelect">
        <template #empty><div class="drop"><i class="pi pi-cloud-upload" /><p class="m0">Drag a file here.</p></div></template>
      </FileUpload>
    </div>
    <div class="grid grid-2">
      <div class="field">
        <label for="rs-key">File name seen by steps</label>
        <InputText id="rs-key" v-model="form.key" class="mono" />
        <small v-if="form.key && !keyOk" class="err">Letters, digits, dots, hyphens and underscores only.</small>
        <small v-else-if="sharedClash" class="err">That name is a shared GWASHub resource. Choose a different name.</small>
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
    <ProgressBar v-if="saving" :value="progress" class="mb" />
    <template #footer>
      <Button label="Cancel" severity="secondary" text :disabled="saving" @click="visible = false" />
      <Button :label="saving ? `Uploading ${progress}%` : 'Add resource'" icon="pi pi-check" :loading="saving" :disabled="!ready" @click="save" />
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
