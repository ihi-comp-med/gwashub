<script setup lang="ts">
const toast = useToast()
const { pid, project, error: projectError } = useProject()
const { data: pl } = useApiGet<any>(() => `/projects/${pid.value}/pipeline`)
const step = ref('1')

const details = reactive({
  cohort: '', phenotype: '', ancestry: 'EUR', sex: 'Combined', build: 'GRCh37', effect_type: 'beta',
  n_cases: null as number | null, n_controls: null as number | null, n_total: null as number | null,
})
watch(project, (p) => {
  if (!p) return
  if (!p.phenotypes.includes(details.phenotype)) details.phenotype = p.phenotypes[0] ?? ''
  if (!p.ancestries.includes(details.ancestry)) details.ancestry = p.ancestries[0] ?? ''
  if (!p.sexes.includes(details.sex)) details.sex = p.sexes[0] ?? ''
}, { immediate: true })
// Case/control phenotypes need cases and controls (the total is their sum); quantitative ones the total sample size.
const quantitative = computed(() => project.value?.phenotype_types?.[details.phenotype] === 'quantitative')
const detailsOk = computed(() => !!(details.cohort.trim() && details.phenotype
  && (quantitative.value ? details.n_total : details.n_cases && details.n_controls)))
watch(quantitative, (q) => { if (q) details.effect_type = 'beta' })
const phenoOptions = computed(() => (project.value?.phenotypes ?? []).map((p: string) => ({ value: p, label: project.value.phenotype_descriptions?.[p] ? `${p} · ${project.value.phenotype_descriptions[p]}` : p })))

// Cohort: pick an existing one or type a new name (warned if it looks like an existing cohort).
const { data: cohorts } = useApiGet<any[]>(() => `/projects/${pid.value}/cohorts`)
const cohortNames = computed(() => (cohorts.value ?? []).map(c => c.name))
const cohortSuggestions = ref<string[]>([])
async function suggestCohorts(q: string) {
  const text = (q ?? '').toLowerCase()
  const matches = cohortNames.value.filter(n => n.toLowerCase().includes(text))
  // Also offer near matches (typos, spacing), so an existing cohort is easy to pick.
  const near = text.trim() ? (await api<any[]>(`/projects/${pid.value}/cohorts/similar`, { query: { name: q } }).catch(() => [])).map(c => c.name) : []
  cohortSuggestions.value = [...new Set([...matches, ...near])]
}
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '')
const chosenCohort = computed(() => (cohorts.value ?? []).find(c => norm(c.name) === norm(details.cohort)) ?? null)
const similar = ref<any[]>([])
let simTimer: ReturnType<typeof setTimeout> | undefined
watch(() => details.cohort, (name) => {
  clearTimeout(simTimer)
  similar.value = []
  if (!name.trim() || chosenCohort.value) return
  simTimer = setTimeout(async () => {
    similar.value = await api<any[]>(`/projects/${pid.value}/cohorts/similar`, { query: { name } }).catch(() => [])
  }, 300)
})

// Step 2: study metadata. Cohort-level fields start from the chosen cohort's values; for a new cohort
// they're saved with it. Optionally copy from one of your earlier uploads.
const studyMeta = ref<Record<string, any>>({})
const { schema: metaSchema, missingRequired } = useMetadataSchema()
const cohortKeys = computed(() => new Set((metaSchema.value ?? []).filter((sec: any) => sec.level === 'cohort').flatMap((sec: any) => sec.fields.map((f: any) => f.key))))
const metaConfig = computed(() => project.value?.metadata_config ?? null)
const missing = computed(() => missingRequired(studyMeta.value, metaConfig.value))
let prefilledFor: number | null | undefined
watch(step, (s) => {
  // Entering the metadata step: start from the cohort's values (once per chosen cohort).
  if (s !== '2' || prefilledFor === (chosenCohort.value?.id ?? null)) return
  prefilledFor = chosenCohort.value?.id ?? null
  const datasetPart = Object.fromEntries(Object.entries(studyMeta.value).filter(([k]) => !cohortKeys.value.has(k)))
  studyMeta.value = { ...(chosenCohort.value?.meta ?? {}), ...datasetPart }
})
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
function onRemove() {
  file.value = null
  suggestion.value = null
}
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
    details.effect_type = quantitative.value ? 'beta' : suggestion.value.effect_type
  }
  catch (err: any) { toast.add({ severity: 'error', summary: 'Could not read the file', detail: err.message, life: 5000 }) }
  finally { reading.value = false }
}

const standardFields = STANDARD_FIELDS
const mapping = reactive<Record<string, string | null>>({})
const missingColumns = computed(() => unmappedRequired(mapping))
const unmappedColumns = computed(() => (suggestion.value?.columns ?? []).filter((c: string) => !Object.values(mapping).includes(c)))

// Step 4: upload in parts (straight to S3 on AWS), then QC starts and we go to the dataset.
const progress = ref(0)
const submitting = ref(false)
async function submit() {
  const f = file.value!
  submitting.value = true
  progress.value = 0
  let datasetId: number | null = null
  try {
    const meta = { ...details, cohort: details.cohort.trim(), cohort_id: chosenCohort.value?.id ?? null, study_meta: studyMeta.value,
      column_mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)) }
    const start = await api<{ id: number, part_size: number, parts: number }>(`/projects/${pid.value}/uploads`,
      { method: 'POST', body: { meta, file_name: f.name, file_size: f.size } })
    datasetId = start.id
    const etags = await uploadInParts(f, {
      partSize: start.part_size, parts: start.parts, onProgress: (x) => { progress.value = Math.round(99 * x) },
      urlFor: async n => (await api<{ urls: Record<string, string> }>(`/datasets/${start.id}/upload/urls`, { method: 'POST', body: { parts: [n] } })).urls[String(n)]!,
    })
    await api(`/datasets/${start.id}/upload/complete`, { method: 'POST', body: { parts: etags } })
    progress.value = 100
    toast.add({ severity: 'success', summary: 'Uploaded: waiting for QC', detail: 'Check the details, then click Start QC in its panel.', life: 5000 })
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
      <p class="page-sub">Describe the dataset, upload summary statistics, check the column mapping, then upload. The dataset waits as Pending QC until you start QC.</p>

      <Card>
        <template #content>
          <Stepper v-model:value="step" linear data-tour="upload-stepper">
            <StepList>
              <Step value="1">Details</Step>
              <Step value="2">Study metadata</Step>
              <Step value="3">File</Step>
              <Step value="4">Map columns</Step>
              <Step value="5">Upload</Step>
            </StepList>
            <StepPanels>
              <StepPanel v-slot="{ activateCallback }" value="1" data-tour="upload-details">
                <div class="grid grid-2">
                  <div class="field"><label for="u-cohort">Cohort</label>
                    <AutoComplete v-model="details.cohort" input-id="u-cohort" :suggestions="cohortSuggestions" dropdown fluid
                                  placeholder="Choose a cohort, or type a new name" @complete="e => suggestCohorts(e.query)" />
                    <small v-if="chosenCohort" class="muted"><i class="pi pi-check-circle ok" /> Existing cohort: its study, genotyping and imputation details are filled in for you.</small>
                    <small v-else-if="details.cohort.trim()" class="muted"><i class="pi pi-plus-circle" /> New cohort: it will be added to this project.</small>
                    <Message v-if="similar.length && !chosenCohort" severity="warn" size="small" :closable="false" class="sim">
                      Is it one of these? {{ similar.map(s => s.name).join(', ') }}. Pick it from the list to avoid a duplicate cohort.
                    </Message>
                  </div>
                  <div class="field"><label>Phenotype</label><Select v-model="details.phenotype" :options="phenoOptions" option-label="label" option-value="value" /></div>
                  <div class="field"><label>Ancestry</label><Select v-model="details.ancestry" :options="project.ancestries" /></div>
                  <div class="field"><label>Sex</label><SelectButton v-model="details.sex" :options="project.sexes" :allow-empty="false" /></div>
                  <div class="field"><label>Genome build</label><Select v-model="details.build" :options="['GRCh37', 'GRCh38']" /></div>
                  <div v-if="quantitative" class="field">
                    <label>Total sample size</label><InputNumber v-model="details.n_total" fluid :min="1" />
                    <small class="muted">{{ details.phenotype }} is a quantitative trait.</small>
                  </div>
                  <div v-else class="grid grid-2 tight">
                    <div class="field"><label>Cases</label><InputNumber v-model="details.n_cases" fluid :min="1" /></div>
                    <div class="field"><label>Controls</label><InputNumber v-model="details.n_controls" fluid :min="1" /></div>
                  </div>
                </div>
                <div class="row end"><Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="!detailsOk" @click="activateCallback('2')" /></div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="2" data-tour="upload-metadata">
                <div class="row copy">
                  <span class="muted">{{ metaConfig?.required?.length ? 'Fields marked * are required by this project; the rest can be completed later from the dataset viewer.' : 'All optional; you can complete or correct these later from the dataset viewer.' }}
                    <template v-if="chosenCohort"> Cohort details come from {{ chosenCohort.name }}; a change here applies to this dataset only.</template>
                    <template v-else-if="details.cohort.trim()"> Cohort details are saved with {{ details.cohort.trim() }} and reused for its future datasets.</template></span>
                  <span class="spacer" />
                  <Select v-if="previous.length" v-model="copyFrom" :options="previous" option-label="label" option-value="id" placeholder="Copy from a previous upload" show-clear
                          class="copysel" @change="copyMeta" />
                </div>
                <MetadataForm v-model="studyMeta" :config="metaConfig" :inherited="chosenCohort?.meta ?? null" />
                <Message v-if="missing.length" severity="warn" size="small" :closable="false">Still needed: {{ missing.join(', ') }}.</Message>
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('1')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="missing.length > 0" @click="activateCallback('3')" />
                </div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="3" data-tour="upload-file">
                <FileUpload mode="advanced" :auto="false" :show-upload-button="false" :show-cancel-button="false"
                            accept=".tsv,.txt,.csv,.gz" choose-label="Choose file" @select="onSelect" @remove="onRemove">
                  <template #content="{ files, removeFileCallback }"><ChosenFiles :files="files" :remove="removeFileCallback" /></template>
                  <template #empty>
                    <div class="drop">
                      <i class="pi pi-cloud-upload" />
                      <p>Drag and drop a summary-statistics file here</p>
                      <p class="muted">.tsv, .txt, .csv: tab, comma or space separated, optionally gzipped. Any column names.</p>
                    </div>
                  </template>
                </FileUpload>
                <div v-if="file" class="row status">
                  <span v-if="reading" class="muted"><i class="pi pi-spin pi-spinner" /> Reading header…</span>
                  <span v-else-if="suggestion" class="ok"><i class="pi pi-check" /> {{ suggestion.columns.length }} columns found</span>
                </div>
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('2')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="!suggestion" @click="activateCallback('4')" />
                </div>
              </StepPanel>

              <StepPanel v-slot="{ activateCallback }" value="4" data-tour="upload-mapping">
                <Message severity="info" :closable="false" class="mb">We suggested a mapping from your file's header. Check it before running QC.</Message>
                <div class="grid grid-2">
                  <div v-for="f in standardFields" :key="f.key" class="field">
                    <label>{{ f.label }} <span v-if="f.required" class="req">*</span></label>
                    <Select v-model="mapping[f.key]" :options="suggestion?.columns ?? []" show-clear placeholder="Not in file" />
                  </div>
                  <div class="field">
                    <label>Effect column contains</label>
                    <SelectButton v-model="details.effect_type" :options="[{ l: 'BETA (log scale)', v: 'beta' }, { l: 'Odds ratios', v: 'or', disabled: quantitative }]" option-label="l" option-value="v" option-disabled="disabled" :allow-empty="false" />
                    <small class="muted">{{ quantitative ? 'Quantitative traits have BETA effects.' : 'Detected from the values; odds ratios are converted to log-odds.' }}</small>
                  </div>
                </div>
                <p v-if="unmappedColumns.length" class="muted small">Ignored columns: <span class="mono">{{ unmappedColumns.join(', ') }}</span></p>
                <h3 class="h3">File preview</h3>
                <div class="table-wrap">
                  <DataTable :value="(suggestion?.preview ?? []).map((r: any[]) => Object.fromEntries(suggestion.columns.map((c: string, i: number) => [c, r[i]])))" size="small">
                    <!-- the file's own column names, as written (table headers are upper-cased elsewhere) -->
                    <Column v-for="c in suggestion?.columns ?? []" :key="c" :field="c">
                      <template #header><span class="nocase">{{ c }}</span></template>
                    </Column>
                  </DataTable>
                </div>
                <div class="row end">
                  <Button label="Back" severity="secondary" text @click="activateCallback('3')" />
                  <Button label="Next" icon="pi pi-arrow-right" icon-pos="right" :disabled="missingColumns.length > 0" @click="activateCallback('5')" />
                </div>
                <small v-if="missingColumns.length" class="err">Map: {{ missingColumns.join(', ') }}</small>
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
                        <template v-if="quantitative"><dt>Sample size</dt><dd>{{ details.n_total?.toLocaleString() }}</dd></template>
                        <template v-else><dt>Cases / controls</dt><dd>{{ details.n_cases?.toLocaleString() }} / {{ details.n_controls?.toLocaleString() }}</dd></template>
                        <dt>Build</dt><dd>{{ details.build }}<span v-if="details.build === 'GRCh37'" class="muted"> → lifted to GRCh38</span></dd>
                        <dt>Effect</dt><dd>{{ details.effect_type === 'or' ? 'Odds ratios → log-odds' : 'BETA' }}</dd>
                        <dt>File</dt><dd class="mono">{{ file?.name }} ({{ fmtSize(file?.size) }})</dd>
                        <dt>Columns</dt><dd>{{ Object.values(mapping).filter(Boolean).length }} mapped</dd>
                        <dt>Study metadata</dt><dd>{{ Object.keys(studyMeta).length }} fields<span v-if="!chosenCohort"> · new cohort {{ details.cohort.trim() }}</span></dd>
                      </dl>
                    </template>
                  </Card>
                </div>
                <ProgressBar v-if="submitting" :value="progress" class="up" />
                <div class="row end">
                  <Button label="Back" severity="secondary" text :disabled="submitting" @click="activateCallback('4')" />
                  <Button :label="submitting ? `Uploading ${progress}%` : 'Upload'" icon="pi pi-upload" :loading="submitting" data-tour="upload-submit" @click="submit" />
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
.ok { color: #15803d; }
.nocase { text-transform: none; letter-spacing: 0; }
.sim { margin-top: 6px; }
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
/* thicker than its percentage label, which sits inside the bar */
.up { margin-top: 16px; height: 22px; }
.up :deep(.p-progressbar-label) { font-size: 0.75rem; line-height: 1; }
.copy { margin-bottom: 16px; font-size: 0.88rem; }
.copysel { min-width: 320px; }
</style>
