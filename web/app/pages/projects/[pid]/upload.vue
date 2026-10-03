<script setup lang="ts">
const toast = useToast()
const { pid, project, error: projectError } = useProject()
const { data: pl } = useApiGet<any>(() => `/projects/${pid.value}/pipeline`)
const step = ref('1')

const details = reactive({
  cohort: '', phenotype: '', ancestry: 'EUR', sex: 'Combined', build: 'GRCh37', effect_type: 'beta',
  n_cases: null as number | null, n_controls: null as number | null,
})
watch(project, (p) => {
  if (!p) return
  if (!p.phenotypes.includes(details.phenotype)) details.phenotype = p.phenotypes[0] ?? ''
  if (!p.ancestries.includes(details.ancestry)) details.ancestry = p.ancestries[0] ?? ''
  if (!p.sexes.includes(details.sex)) details.sex = p.sexes[0] ?? ''
}, { immediate: true })
const detailsOk = computed(() => !!(details.cohort && details.phenotype && details.n_cases && details.n_controls))

// Step 2: study metadata, optionally copied from one of your earlier uploads.
const studyMeta = ref<Record<string, any>>({})
const { fieldCount } = useMetadataSchema()
const { data: myDatasets } = useApiGet<any[]>(() => `/projects/${pid.value}/datasets`)
const previous = computed(() => (myDatasets.value ?? []).filter(d => d.can_open && d.metadata_complete?.filled)
  .map(d => ({ id: d.id, label: `${d.cohort} · ${d.phenotype} · ${d.ancestry} · ${d.sex} (${d.metadata_complete.filled} fields)` })))
const copyFrom = ref<number | null>(null)
async function copyMeta() {
  if (!copyFrom.value) return
  const d = await api(`/datasets/${copyFrom.value}`)
  studyMeta.value = { ...d.study_meta }
  toast.add({ severity: 'info', summary: 'Metadata copied', detail: 'Check anything that differs for this dataset.', life: 3000 })
}

// Step 2: choose a file; the API reads its header and suggests a column mapping.
const file = ref<File | null>(null)
const suggestion = ref<any>(null)
const reading = ref(false)
async function onSelect(e: { files: File[] }) {
  file.value = e.files[0] ?? null
  suggestion.value = null
  if (!file.value) return
  reading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value.slice(0, 512 * 1024), file.value.name)
    suggestion.value = await api(`/projects/${pid.value}/column-suggestion`, { method: 'POST', body: fd })
    Object.assign(mapping, Object.fromEntries(standardFields.map(f => [f.key, suggestion.value.mapping[f.key] ?? null])))
    details.effect_type = suggestion.value.effect_type
  }
  catch (err: any) { toast.add({ severity: 'error', summary: 'Could not read the file', detail: err.message, life: 5000 }) }
  finally { reading.value = false }
}

const standardFields = [
  { key: 'rsid', label: 'Variant ID (rsID)', required: false },
  { key: 'chr', label: 'Chromosome', required: true },
  { key: 'pos', label: 'Position', required: true },
  { key: 'ea', label: 'Effect allele', required: true },
  { key: 'oa', label: 'Other allele', required: true },
  { key: 'eaf', label: 'Effect allele frequency', required: false },
  { key: 'beta', label: 'Effect (BETA or OR)', required: true },
  { key: 'se', label: 'Standard error', required: true },
  { key: 'p', label: 'P-value', required: true },
  { key: 'n', label: 'Sample size', required: false },
  { key: 'info', label: 'Imputation INFO', required: false },
]
const mapping = reactive<Record<string, string | null>>({})
const unmappedRequired = computed(() => standardFields.filter(f => f.required && !mapping[f.key]).map(f => f.label))
const unmappedColumns = computed(() => (suggestion.value?.columns ?? []).filter((c: string) => !Object.values(mapping).includes(c)))

// Step 4: upload in parts (straight to S3 on AWS), then QC starts and we go to the dataset.
const progress = ref(0)
const submitting = ref(false)
const PARALLEL = 4
const RETRIES = 3

function putPart(url: string, blob: Blob, onBytes: (n: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    if (url.startsWith('/')) xhr.withCredentials = true
    let sent = 0
    xhr.upload.onprogress = (e) => { onBytes(e.loaded - sent); sent = e.loaded }
    xhr.onload = () => {
      const etag = xhr.getResponseHeader('ETag')
      if (xhr.status >= 200 && xhr.status < 300 && etag) resolve(etag)
      else { onBytes(-sent); reject(new Error(`part upload failed (${xhr.status})`)) }
    }
    xhr.onerror = () => { onBytes(-sent); reject(new Error('network error')) }
    xhr.send(blob)
  })
}

async function submit() {
  const f = file.value!
  submitting.value = true
  progress.value = 0
  let datasetId: number | null = null
  try {
    const meta = { ...details, study_meta: studyMeta.value, column_mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)) }
    const start = await api<{ id: number, part_size: number, parts: number }>(`/projects/${pid.value}/uploads`,
      { method: 'POST', body: { meta, file_name: f.name, file_size: f.size } })
    datasetId = start.id
    let done = 0
    const bump = (n: number) => { done += n; progress.value = Math.min(99, Math.round(100 * done / f.size)) }
    const etags: { n: number, etag: string }[] = []
    const queue = Array.from({ length: start.parts }, (_, i) => i + 1)
    async function worker() {
      while (queue.length) {
        const batch = queue.splice(0, 1)
        const n = batch[0]!
        const blob = f.slice((n - 1) * start.part_size, n * start.part_size)
        for (let attempt = 1; ; attempt++) {
          try {
            // Ask for each URL just before use: presigned URLs expire after 15 minutes.
            const { urls } = await api<{ urls: Record<string, string> }>(`/datasets/${start.id}/upload/urls`, { method: 'POST', body: { parts: [n] } })
            etags.push({ n, etag: await putPart(urls[String(n)]!, blob, bump) })
            break
          }
          catch (e) {
            if (attempt >= RETRIES) throw e
            await new Promise(r => setTimeout(r, 1000 * attempt))
          }
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL, start.parts) }, worker))
    await api(`/datasets/${start.id}/upload/complete`, { method: 'POST', body: { parts: etags } })
    progress.value = 100
    toast.add({ severity: 'success', summary: 'Uploaded: QC started', detail: `${details.cohort} is being processed.`, life: 3500 })
    navigateTo({ path: `/projects/${pid.value}/datasets`, query: { open: String(start.id) } })
  }
  catch (e: any) {
    toast.add({ severity: 'error', summary: 'Upload failed', detail: `${e.message}. Nothing was kept; please try again.`, life: 7000 })
    if (datasetId) await api(`/datasets/${datasetId}`, { method: 'DELETE' }).catch(() => {})
  }
  finally { submitting.value = false }
}
const stepTitle = (key: string) => pl.value?.catalog.find((c: any) => c.key === key)?.title ?? key
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <h2 class="h2">Upload dataset</h2>
      <p class="page-sub">Describe the dataset, upload summary statistics, check the column mapping and run the project's QC pipeline.</p>

      <Card>
        <template #content>
          <Stepper v-model:value="step" linear>
            <StepList>
              <Step value="1">Details</Step>
              <Step value="2">Study metadata</Step>
              <Step value="3">File</Step>
              <Step value="4">Map columns</Step>
              <Step value="5">Run QC</Step>
            </StepList>
            <StepPanels>
              <StepPanel v-slot="{ activateCallback }" value="1">
                <div class="grid grid-2">
                  <div class="field"><label for="u-cohort">Cohort</label><InputText id="u-cohort" v-model="details.cohort" placeholder="e.g. UK Biobank" /></div>
                  <div class="field"><label>Phenotype</label><Select v-model="details.phenotype" :options="project.phenotypes" /></div>
                  <div class="field"><label>Ancestry</label><Select v-model="details.ancestry" :options="project.ancestries" /></div>
                  <div class="field"><label>Sex</label><SelectButton v-model="details.sex" :options="project.sexes" :allow-empty="false" /></div>
                  <div class="field"><label>Genome build</label><Select v-model="details.build" :options="['GRCh37', 'GRCh38']" /></div>
                  <div class="grid grid-2 tight">
                    <div class="field"><label>Cases</label><InputNumber v-model="details.n_cases" fluid :min="1" /></div>
                    <div class="field"><label>Controls</label><InputNumber v-model="details.n_controls" fluid :min="1" /></div>
                  </div>
                </div>
                <div class="row end"><Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="!detailsOk" @click="activateCallback('2')" /></div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="2">
                <div class="row copy">
                  <span class="muted">All optional; you can complete or correct these later from the dataset viewer.</span>
                  <span class="spacer" />
                  <Select v-if="previous.length" v-model="copyFrom" :options="previous" option-label="label" option-value="id" placeholder="Copy from a previous upload" show-clear
                          class="copysel" @change="copyMeta" />
                </div>
                <MetadataForm v-model="studyMeta" />
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('1')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" @click="activateCallback('3')" />
                </div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="3">
                <FileUpload mode="advanced" :auto="false" :show-upload-button="false" :show-cancel-button="false"
                            accept=".tsv,.txt,.csv,.gz" choose-label="Choose file" @select="onSelect">
                  <template #empty>
                    <div class="drop">
                      <i class="pi pi-cloud-upload" />
                      <p>Drag and drop a summary-statistics file here</p>
                      <p class="muted">.tsv, .txt, .csv: tab, comma or space separated, optionally gzipped. Any column names.</p>
                    </div>
                  </template>
                </FileUpload>
                <div v-if="file" class="row status">
                  <i class="pi pi-file" /><span class="mono">{{ file.name }}</span><span class="muted">{{ fmtSize(file.size) }}</span>
                  <span class="spacer" />
                  <span v-if="reading" class="muted"><i class="pi pi-spin pi-spinner" /> Reading header…</span>
                  <span v-else-if="suggestion" class="ok"><i class="pi pi-check" /> {{ suggestion.columns.length }} columns found</span>
                </div>
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('2')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="!suggestion" @click="activateCallback('4')" />
                </div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="4">
                <Message severity="info" :closable="false" class="mb">We suggested a mapping from your file's header. Check it before running QC.</Message>
                <div class="grid grid-2">
                  <div v-for="f in standardFields" :key="f.key" class="field">
                    <label>{{ f.label }} <span v-if="f.required" class="req">*</span></label>
                    <Select v-model="mapping[f.key]" :options="suggestion?.columns ?? []" show-clear placeholder="Not in file" />
                  </div>
                  <div class="field">
                    <label>Effect column contains</label>
                    <SelectButton v-model="details.effect_type" :options="[{ l: 'BETA (log scale)', v: 'beta' }, { l: 'Odds ratios', v: 'or' }]" option-label="l" option-value="v" :allow-empty="false" />
                    <small class="muted">Detected from the values; odds ratios are converted to log-odds.</small>
                  </div>
                </div>
                <p v-if="unmappedColumns.length" class="muted small">Ignored columns: <span class="mono">{{ unmappedColumns.join(', ') }}</span></p>
                <h3 class="h3">File preview</h3>
                <div class="table-wrap">
                  <DataTable :value="(suggestion?.preview ?? []).map((r: any[]) => Object.fromEntries(suggestion.columns.map((c: string, i: number) => [c, r[i]])))" size="small">
                    <Column v-for="c in suggestion?.columns ?? []" :key="c" :field="c" :header="c" />
                  </DataTable>
                </div>
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('3')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="unmappedRequired.length > 0" @click="activateCallback('5')" />
                </div>
                <small v-if="unmappedRequired.length" class="err">Map: {{ unmappedRequired.join(', ') }}</small>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="5">
                <div class="grid grid-2">
                  <Card class="inner">
                    <template #title>Pipeline</template>
                    <template #subtitle>Project pipeline v{{ pl?.version }}</template>
                    <template #content>
                      <ol class="steps"><li v-for="(s, i) in pl?.steps ?? []" :key="i">{{ stepTitle(s.step) }} <span class="mono muted small">{{ s.image ?? pl.catalog.find((c: any) => c.key === s.step)?.image }}</span></li></ol>
                    </template>
                  </Card>
                  <Card class="inner">
                    <template #title>Summary</template>
                    <template #content>
                      <dl class="summary">
                        <dt>Dataset</dt><dd>{{ details.cohort }} · {{ details.phenotype }} · {{ details.ancestry }} · {{ details.sex }}</dd>
                        <dt>Cases / controls</dt><dd>{{ details.n_cases?.toLocaleString() }} / {{ details.n_controls?.toLocaleString() }}</dd>
                        <dt>Build</dt><dd>{{ details.build }}<span v-if="details.build === 'GRCh37'" class="muted"> → lifted to GRCh38</span></dd>
                        <dt>Effect</dt><dd>{{ details.effect_type === 'or' ? 'Odds ratios → log-odds' : 'BETA' }}</dd>
                        <dt>File</dt><dd class="mono">{{ file?.name }} ({{ fmtSize(file?.size) }})</dd>
                        <dt>Columns</dt><dd>{{ Object.values(mapping).filter(Boolean).length }} mapped</dd>
                        <dt>Study metadata</dt><dd>{{ Object.keys(studyMeta).length }} of {{ fieldCount }} fields</dd>
                      </dl>
                    </template>
                  </Card>
                </div>
                <ProgressBar v-if="submitting" :value="progress" class="up" />
                <div class="row end">
                  <Button label="Back" severity="secondary" text :disabled="submitting" @click="activateCallback('4')" />
                  <Button :label="submitting ? `Uploading ${progress}%` : 'Upload & run QC'" icon="pi pi-play" :loading="submitting" @click="submit" />
                </div>
              </StepPanel>
            </StepPanels>
          </Stepper>
        </template>
      </Card>
    </div>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
.h2 { font-size: 1.25rem; margin: 0 0 4px; }
.end { justify-content: flex-end; margin-top: 16px; }
.tight { gap: 12px; }
.drop { text-align: center; padding: 24px; }
.drop i { font-size: 2rem; color: var(--gh-accent); }
.status { margin-top: 12px; gap: 10px; }
.ok { color: #15803d; }
.req, .err { color: #c2410c; }
.mb { margin-bottom: 16px; }
.small { font-size: 0.8rem; }
.h3 { font-size: 1rem; margin: 8px 0; }
.table-wrap { overflow-x: auto; }
.inner { border: 1px solid var(--gh-border); box-shadow: none; }
.steps { margin: 0; padding-left: 20px; line-height: 1.8; }
.summary { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; }
.summary dt { color: var(--gh-muted); }
.summary dd { margin: 0; word-break: break-all; }
.up { margin-top: 16px; height: 8px; }
.copy { margin-bottom: 16px; font-size: 0.88rem; }
.copysel { min-width: 320px; }
</style>
