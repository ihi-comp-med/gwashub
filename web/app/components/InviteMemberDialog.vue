<script setup lang="ts">
// Reviewers add people to their project. Someone new gets an account and an invitation email.
const props = defineProps<{ projectId: string, projectName: string }>()
const emit = defineEmits<{ added: [] }>()
const visible = defineModel<boolean>('visible', { default: false })
const toast = useToast()
const form = reactive({ email: '', name: '', institution: '', role: 'uploader' })
const busy = ref(false)
const roles = [
  { label: 'Uploader', value: 'uploader', help: 'Uploads datasets and sees only their own.' },
  { label: 'Reviewer', value: 'reviewer', help: 'Sees every dataset, reviews QC, runs meta-analyses, manages the pipeline and members.' },
]
watch(visible, (v) => { if (v) Object.assign(form, { email: '', name: '', institution: '', role: 'uploader' }) })
async function submit() {
  busy.value = true
  try {
    const r = await api<any>(`/projects/${props.projectId}/members`, { method: 'POST', body: form })
    const detail = r.account === 'invited' ? `An invitation email is on its way to ${r.email}.`
      : r.account === 'existing' ? `${r.name} already had an account and can now open ${props.projectName}.`
        : `${r.name} can now sign in.`
    toast.add({ severity: 'success', summary: `${r.name} added as ${r.role}`, detail, life: 5000 })
    visible.value = false
    emit('added')
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not add member', detail: e.message, life: 6000 }) }
  finally { busy.value = false }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Invite a member" class="csd" :draggable="false">
    <form class="form" @submit.prevent="submit">
      <div class="field"><label for="inv-email">Email</label>
        <InputText id="inv-email" v-model="form.email" type="email" placeholder="name@university.ac.uk" autocomplete="off" fluid />
      </div>
      <div class="grid grid-2">
        <div class="field"><label for="inv-name">Name</label><InputText id="inv-name" v-model="form.name" placeholder="Full name" fluid /></div>
        <div class="field"><label for="inv-inst">Institution</label><InputText id="inv-inst" v-model="form.institution" placeholder="Optional" fluid /></div>
      </div>
      <div class="field"><label>Role</label>
        <SelectButton v-model="form.role" :options="roles" option-label="label" option-value="value" :allow-empty="false" />
        <small class="muted">{{ roles.find(r => r.value === form.role)?.help }}</small>
      </div>
      <small class="muted">If they don't have a GWASHub account yet, they'll get an email with a temporary password. They set their
        own password and an authenticator app the first time they sign in. Name and institution are only used for new accounts.</small>
      <div class="row end">
        <Button label="Cancel" text severity="secondary" :disabled="busy" @click="visible = false" />
        <Button type="submit" label="Invite" icon="pi pi-send" :loading="busy" :disabled="!form.email" />
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 12px; }
.end { justify-content: flex-end; }
</style>
