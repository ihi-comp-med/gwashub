<script setup lang="ts">
// Your account: personal API tokens for the upload script (one project, uploading only, 30 days; shown once).
const { me } = useAuth()
const toast = useToast()
const { data: tokens, refresh } = useApiGet<any[]>('/tokens')
const { data: projects } = useApiGet<any[]>('/projects')
const projectOptions = computed(() => (projects.value ?? []).map((p: any) => ({ value: p.id, label: p.name })))
const projectName = (id: string) => (projects.value ?? []).find((p: any) => p.id === id)?.name ?? id
const draft = reactive({ project_id: null as string | null, name: '' })
const made = ref<any>(null)  // the new token, shown once
const busy = ref(false)
async function make() {
  busy.value = true
  try {
    made.value = await api('/tokens', { method: 'POST', body: draft })
    draft.name = ''
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not make the token', detail: e.message, life: 5000 }) }
  finally { busy.value = false }
}
async function copy() {
  try { await navigator.clipboard.writeText(made.value.token); toast.add({ severity: 'success', summary: 'Copied', life: 2000 }) }
  catch { toast.add({ severity: 'warn', summary: 'Select the token and copy it', life: 3000 }) }
}
async function revoke(t: any) {
  if (!window.confirm(`Revoke "${t.name}"? Scripts using it stop working at once.`)) return
  try {
    await api(`/tokens/${t.id}`, { method: 'DELETE' })
    toast.add({ severity: 'success', summary: 'Token revoked', life: 2500 })
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not revoke', detail: e.message, life: 5000 }) }
}
const STATE: Record<string, string> = { active: 'success', expired: 'secondary', revoked: 'danger' }
</script>

<template>
  <div class="container page">
    <h1 class="title">Account</h1>
    <p v-if="me" class="muted">{{ me.name }} · {{ me.email }}<span v-if="me.institution"> · {{ me.institution }}</span></p>

    <Card class="mb" data-tour="account-bulk">
      <template #title>Bulk upload</template>
      <template #subtitle>Upload many files at once, with their metadata, from an Excel manifest (one row per file).</template>
      <template #content>
        <ol class="steps">
          <li><a href="/api/bulk/script" download data-tour="account-script">Download the upload script</a> (Python 3.9 or later, run with <code>python3</code>; nothing to install). It already knows this site's address.</li>
          <li>Make an API token below for the project, and give it to the script: <code>export GWASHUB_TOKEN=gwh_…</code></li>
          <li><code>python3 gwashub_upload.py template manifest.xlsx</code> writes an empty manifest workbook: a README sheet
            explaining every column, and a Manifest sheet with drop-down lists. Fill one row per file. Name every file column
            exactly (<code>col_chr</code>, <code>col_effect</code>…); nothing is guessed. Repeat a cohort's details on each of its rows.</li>
          <li><code>python3 gwashub_upload.py check manifest.xlsx</code> lists every problem; fix them until it says OK.</li>
          <li><code>python3 gwashub_upload.py upload manifest.xlsx</code> uploads the files (run it again to resume after an
            interruption). They wait on the site as <strong>Pending QC</strong>: check them, then click Start QC.</li>
        </ol>
      </template>
    </Card>

    <Card>
      <template #title>API tokens</template>
      <template #subtitle>For the bulk upload script. A token acts as you, in one project, and can only upload: it can't start QC,
        delete, review or read other people's datasets. It lasts 30 days and is shown once.</template>
      <template #content>
        <form class="row add" data-tour="account-token-form" @submit.prevent="make">
          <Select v-model="draft.project_id" :options="projectOptions" option-label="label" option-value="value" placeholder="Project" size="small" />
          <InputText v-model="draft.name" placeholder="Name, e.g. UKB files, October 2026" size="small" class="grow" />
          <Button type="submit" label="Make token" icon="pi pi-key" size="small" :loading="busy" :disabled="!draft.project_id || !draft.name.trim()" />
        </form>

        <Message v-if="made" severity="success" size="small" :closable="false" class="made">
          <div class="stack">
            <span><strong>Copy this token now.</strong> It won't be shown again; if you lose it, revoke it and make another.</span>
            <div class="row"><code class="tok">{{ made.token }}</code><Button icon="pi pi-copy" text size="small" aria-label="Copy the token" @click="copy" />
              <span class="spacer" /><Button label="Done" text size="small" @click="made = null" /></div>
          </div>
        </Message>

        <DataTable v-if="tokens?.length" :value="tokens" size="small" class="toks">
          <Column header="Name"><template #body="{ data }"><span class="strong">{{ data.name }}</span> <span class="mono muted small">{{ data.prefix }}…</span></template></Column>
          <Column header="Project"><template #body="{ data }">{{ projectName(data.project_id) }}</template></Column>
          <Column header="Made"><template #body="{ data }">{{ fmtDate(data.created_at) }}</template></Column>
          <Column header="Expires"><template #body="{ data }">{{ fmtDate(data.expires_at) }}</template></Column>
          <Column header="Last used"><template #body="{ data }">{{ data.last_used_at ? fmtDate(data.last_used_at) : 'never' }}</template></Column>
          <Column header="State"><template #body="{ data }"><Tag :value="data.state" :severity="STATE[data.state]" class="st" /></template></Column>
          <Column><template #body="{ data }"><Button v-if="data.state === 'active'" label="Revoke" text size="small" severity="danger" @click="revoke(data)" /></template></Column>
        </DataTable>
        <p v-else class="muted small">No tokens yet.</p>
      </template>
    </Card>
  </div>
</template>

<style scoped>
.title { margin: 0 0 4px; }
.mb { margin-bottom: 16px; }
.steps { margin: 0; padding-left: 1.2rem; display: flex; flex-direction: column; gap: 6px; font-size: 0.9rem; }
.steps code { font-size: 0.8rem; }
.add { gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.grow { flex: 1; min-width: 16rem; }
.made { margin-bottom: 12px; }
.stack { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.tok { font-size: 0.8rem; word-break: break-all; background: var(--gh-bg); padding: 2px 6px; border-radius: 4px; }
.toks { font-size: 0.85rem; }
.st { font-size: 0.7rem; padding: 0 6px; }
</style>
