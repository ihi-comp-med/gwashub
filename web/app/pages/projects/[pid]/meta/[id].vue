<script setup lang="ts">
// One meta-analysis: summary, interactive Manhattan + QQ, lead loci with forest plots, inputs, settings, downloads.
const route = useRoute()
const toast = useToast()
const { pid, project, error: projectError, isReviewer } = useProject()
const id = computed(() => String(route.params.id))
const { data: ma, refresh } = useApiGet<any>(() => `/meta/${id.value}`)
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => { timer = setInterval(() => ['queued', 'running'].includes(ma.value?.status) && refresh(), 2000) })
onBeforeUnmount(() => clearInterval(timer))

const selectedLocus = ref<any>(null)
// The selected locus's plot data (region_<locus_id>.json), loaded when a locus is picked.
const region = ref<any>(null)
watch(selectedLocus, async (f) => {
  region.value = null
  if (!f?.locus_id || ma.value?.status !== 'completed' || !ma.value?.summary?.n_region_files) return
  try { region.value = await $fetch(fileUrl(`region_${f.locus_id}.json`)) }
  catch { region.value = null }
})
watch(ma, (v) => { if (v?.forest?.length && !selectedLocus.value) selectedLocus.value = v.forest[0] })
const forestFor = (row: any) => ma.value?.forest?.find((f: any) => f.chr === String(row.chr) && f.pos === Number(row.pos))
function pickRow(row: any) { const f = forestFor(row); if (f) selectedLocus.value = f }
function pickPoint(p: { chr: string, pos: number }) {
  const near = ma.value?.forest?.filter((f: any) => f.chr === p.chr).sort((a: any, b: any) => Math.abs(a.pos - p.pos) - Math.abs(b.pos - p.pos))[0]
  if (near && Math.abs(near.pos - p.pos) < 1_000_000) selectedLocus.value = near
}
const cnt = (v: any, minus = false) => v == null ? '–' : v === 0 && minus ? '0' : `${minus ? '−' : ''}${Number(v).toLocaleString()}`
const hasCount = (k: string) => studies.value.some((s: any) => s[k] != null)
const has = (name: string) => (ma.value?.files ?? []).includes(name)
const fileUrl = (name: string) => `/api/meta/${id.value}/files/${name}`
const s = computed(() => ma.value?.summary ?? {})
// Sample-size box: summed over the included datasets (a meta-analysis is all case/control or all quantitative).
const sampleTile = computed(() => {
  const rows: any[] = datasetRows.value ?? []
  if (!rows.length) return null
  const given = (vals: any[]) => vals.filter(v => v != null).map(Number)
  const fmt = (vals: number[]) => vals.reduce((t, v) => t + v, 0).toLocaleString()
  const totals = given(rows.map(r => r.n_total ?? (r.n_cases != null && r.n_controls != null ? Number(r.n_cases) + Number(r.n_controls) : null)))
  const note = totals.length < rows.length ? ` (${totals.length} of ${rows.length} datasets)` : ''
  const value = fmt(totals), label = `Total sample size${note}`
  if (rows.every(r => r.trait_type === 'quantitative')) return { value, label, detail: '' }
  return { value, label, detail: `${fmt(given(rows.map(r => r.n_cases)))} cases · ${fmt(given(rows.map(r => r.n_controls)))} controls` }
})
const studies = computed(() => s.value.studies ?? [])
// Per-dataset rows: the post-processing's counts joined with each dataset's details and QC (from the API).
const datasetRows = computed(() => studies.value.map((st: any) => ({ ...(ma.value?.datasets ?? []).find((d: any) => d.id === st.id), ...st })))
const HELP = {
  n: 'Cases and controls given at upload, or the total sample size for a quantitative trait.',
  covered: 'How many of the lead loci (their lead SNP) this dataset has. Low coverage can mean a different genotyping or imputation panel, or a problem such as the wrong genome build.',
  direction: 'Of the lead loci this dataset has, the share where its effect points the same way as the meta-analysis. Well below 100% suggests swapped effect and other alleles.',
  outliers: "Lead loci where this dataset's effect differs from the pooled effect by more than 3 of its own standard errors: the datasets driving heterogeneity.",
  lambda: 'Genomic inflation of the cleaned file: median χ² over its expected value. Above about 1.1, check for inflation.',
  ldsc: 'LD score regression intercept from QC: near 1 means inflation is polygenic signal; well above 1 points to confounding or stratification.',
  qc: 'QC status of the dataset; a warning icon lists the QC steps that warned on the run used here.',
}
async function stop() {
  if (!window.confirm('Stop this meta-analysis? It stays in the list as stopped; delete it or start a new one.')) return
  try {
    await api(`/meta/${id.value}/stop`, { method: 'POST' })
    toast.add({ severity: 'info', summary: 'Meta-analysis stopped', life: 2500 })
    await refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not stop it', detail: e.message, life: 4000 }) }
}
async function remove() {
  if (!window.confirm('Delete this meta-analysis? This cannot be undone.')) return
  try {
    await api(`/meta/${id.value}`, { method: 'DELETE' })
    navigateTo(`/projects/${pid.value}/meta`)
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 4000 }) }
}
// Settings as labelled rows: the method's own parameters (from its analysis.yaml); then, separately, the post-processing
// ones every method shares (lead loci, heterogeneity flag).
const fmtVal = (v: any) => typeof v === 'boolean' ? (v ? 'yes' : 'no') : typeof v === 'number' && v !== 0 && Math.abs(v) < 1e-3 ? v.toExponential(0) : String(v ?? '–')
const rowsOf = (schema: any[], values: Record<string, any> = {}) => schema.map((f: any) => ({
  key: f.key, label: f.label, value: fmtVal(values[f.key] ?? f.default), changed: values[f.key] != null && values[f.key] !== f.default,
}))
const settingRows = computed(() => {
  const v = ma.value
  if (!v) return []
  return [...rowsOf(v.method?.params ?? [], v.settings.params),
    { key: 'include_statuses', label: 'Eligible QC statuses', value: (v.settings.include_statuses ?? []).join(', '), changed: false }]
})
const postRows = computed(() => rowsOf(ma.value?.common_params ?? [], ma.value?.settings.common))
// The one-line summary: the method's first setting (e.g. model or scheme), anything changed from the defaults, and the
// lead-loci basics; the rest under "All settings".
const keyRows = computed(() => {
  const m = settingRows.value, c = postRows.value
  const pick = [m[0], ...m.slice(1).filter(r => r.changed), ...c.filter(r => ['gws', 'ld_panel'].includes(r.key) || r.changed)]
  return [...new Map(pick.filter(Boolean).map(r => [r.key, r])).values()]
})
const showSettings = ref(false)
const downloadMenu = ref()
const downloads = computed(() => {
  const v = ma.value
  if (!v) return []
  const item = (label: string, name: string, icon = 'pi pi-download') => ({ label, icon, url: fileUrl(name), target: '_blank' })
  const results = [
    has('README.txt') && item('README: what each file contains', 'README.txt', 'pi pi-file'),
    has('lead_loci.tsv') && item('Lead loci', 'lead_loci.tsv'),
    has('signals.tsv') && item('Signals: independent significant SNPs', 'signals.tsv'),
    has('datasets.tsv') && item('Datasets and filter counts', 'datasets.tsv'),
    isReviewer.value && has('meta_results.tsv.gz') && item('Full results: every variant', 'meta_results.tsv.gz'),
  ].filter(Boolean)
  const program = (v.method_files ?? []).map((f: any) => item(f.label, f.path, 'pi pi-file'))
  return [{ label: 'Results', items: results }, ...(program.length ? [{ label: `${v.method?.title} files`, items: program }] : [])]
})
const common = computed(() => ma.value?.settings?.common ?? {})
// How long ago the running meta-analysis's output tail was published (it refreshes about every 15 seconds).
function ago(iso: string) {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000))
  return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`
}
const num = (x: any, d = 4) => x === '' || x == null || Number.isNaN(Number(x)) ? '–' : Number(x).toFixed(d)
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <NuxtLink :to="`/projects/${pid}/meta`" class="back"><i class="pi pi-arrow-left" /> All meta-analyses</NuxtLink>
      <div v-if="ma">
        <div class="row head">
          <div>
            <h2 class="h2">{{ ma.name }}</h2>
          </div>
          <span class="spacer" />
          <template v-if="ma.status === 'completed'">
            <Button label="Download" icon="pi pi-download" icon-pos="left" size="small" aria-haspopup="true" aria-controls="ma-downloads"
                    @click="e => downloadMenu.toggle(e)" />
            <Menu id="ma-downloads" ref="downloadMenu" :model="downloads" popup class="dl-menu" />
          </template>
          <Button v-if="isReviewer && ['queued', 'running'].includes(ma.status)" label="Stop" icon="pi pi-stop-circle" size="small" text severity="danger" @click="stop" />
          <Button v-if="isReviewer && !['queued', 'running'].includes(ma.status)" icon="pi pi-trash" text severity="danger" aria-label="Delete" @click="remove" />
        </div>

        <div class="info">
          <div class="who">
            <strong>{{ ma.method?.title }}</strong>
            <i v-if="ma.method?.reference" v-tooltip.top="ma.method.reference" class="pi pi-info-circle muted" :aria-label="ma.method.reference" />
            <span class="muted">· {{ ma.n_datasets }} datasets · run by {{ ma.created_by }} on {{ fmtDate(ma.created_at) }}</span>
          </div>
          <div class="settings">
            <span v-for="r in keyRows" :key="r.key"><span class="muted">{{ r.label }}</span> {{ r.value }}</span>
            <a href="#" class="toggle" @click.prevent="showSettings = !showSettings">{{ showSettings ? 'Hide settings' : 'All settings' }}</a>
          </div>
          <div v-if="showSettings" class="all-settings">
            <section>
              <h5>{{ ma.method?.title }} settings</h5>
              <dl><template v-for="r in settingRows" :key="r.key"><dt>{{ r.label }}</dt><dd :class="{ changed: r.changed }">{{ r.value }}</dd></template></dl>
            </section>
            <section>
              <h5>Post-processing</h5>
              <dl><template v-for="r in postRows" :key="r.key"><dt>{{ r.label }}</dt><dd :class="{ changed: r.changed }">{{ r.value }}</dd></template></dl>
            </section>
          </div>
        </div>

        <Message v-if="['queued', 'running'].includes(ma.status)" :severity="ma.batch_note ? 'warn' : 'info'" :closable="false" icon="pi pi-spin pi-spinner">{{ ma.batch_note || 'Running…' }}</Message>
        <div v-if="ma.status === 'running' && ma.live?.tail?.lines?.length" class="tail" aria-live="polite">
          <pre>{{ ma.live.tail.lines.join('\n') }}</pre>
          <span class="muted small">The {{ ma.live.stage === 'post-processing' ? 'post-processing (lead loci, plots)' : 'method' }}'s last {{ ma.live.tail.lines.length }} output lines · updated {{ ago(ma.live.tail.at) }}</span>
        </div>
        <Message v-if="ma.status === 'failed'" severity="error" :closable="false">
          <pre class="err">{{ ma.error }}</pre>
          <a v-if="ma.has_output && isReviewer" :href="`/api/meta/${ma.id}/files/method_output.txt`" class="p-button p-button-sm p-button-outlined outlink"><i class="pi pi-download" />&nbsp;Method output</a>
        </Message>
        <Message v-if="ma.status === 'stopped'" severity="info" :closable="false" icon="pi pi-stop-circle">{{ ma.error }}</Message>

        <template v-if="ma.status === 'completed'">
          <div class="grid tiles">
            <Card><template #content><div class="stat">{{ s.n_datasets }}</div><div class="stat-label">Datasets</div></template></Card>
            <Card v-if="sampleTile"><template #content><div class="stat">{{ sampleTile.value }}</div><div class="stat-label">{{ sampleTile.label }}</div><div v-if="sampleTile.detail" class="stat-detail">{{ sampleTile.detail }}</div></template></Card>
            <Card><template #content><div class="stat">{{ s.n_variants?.toLocaleString() }}</div><div class="stat-label">Variants meta-analysed</div></template></Card>
            <Card><template #content><div class="stat">{{ s.n_loci }}</div><div class="stat-label">Genome-wide loci</div></template></Card>
            <Card><template #content><div class="stat">{{ s.lambda_gc?.toFixed(3) }}</div><div class="stat-label">λGC</div></template></Card>
          </div>

          <div class="plots">
            <Card>
              <template #title>Manhattan</template>
              <template #subtitle>Hover a peak; click it to show its forest plot.</template>
              <template #content><ManhattanPlot :data="ma.plot_data.manhattan" :gws="ma.plot_data.gws" :height="280" @select="pickPoint" /></template>
            </Card>
            <Card><template #title>QQ</template><template #subtitle>Observed against expected −log10(P).</template><template #content><QQPlot :data="ma.plot_data.qq" :size="280" /></template></Card>
          </div>

          <Card class="hits">
            <template #title>Lead loci</template>
            <template #subtitle>
              P &lt; {{ Number(common.gws ?? 5e-8).toExponential(0) }};
              <template v-if="s.loci_method === 'distance'">{{ common.boundary_kb ?? 500 }} kb windows (no LD panel uploaded yet).</template>
              <template v-else-if="s.loci_method === 'clumping'">LD clumping against the 1000 Genomes {{ s.ld_panel }} panel; each locus is bounded by its leads' LD clouds.</template>
              <template v-if="s.n_gws_not_in_panel">{{ ' ' + s.n_gws_not_in_panel.toLocaleString() }} significant variants aren't in the panel and couldn't be clumped.</template>
              Direction: one symbol per dataset, in input order (? = variant missing).
            </template>
            <template #content>
              <DataTable :value="ma.lead_loci" row-hover class="dt clickable" scrollable scroll-height="420px" @row-click="e => pickRow(e.data)"
                         :row-class="(r: any) => selectedLocus && selectedLocus.chr === String(r.chr) && selectedLocus.pos === Number(r.pos) ? 'sel' : ''">
                <Column header="Variant"><template #body="{ data }"><span class="mono">{{ data.rsid || '–' }}</span></template></Column>
                <Column header="Position"><template #body="{ data }"><span class="mono">{{ data.chr }}:{{ Number(data.pos).toLocaleString() }}</span></template></Column>
                <Column header="Nearest gene"><template #body="{ data }"><i v-if="data.nearest_pc_gene || data.nearest_gene" v-tooltip.top="data.genes_in_locus ? `Protein-coding genes in the locus: ${String(data.genes_in_locus).split(';').join(', ')}` : undefined">{{ data.nearest_pc_gene || data.nearest_gene }}</i></template></Column>
                <Column header="Locus">
                  <template #body="{ data }">
                    <span v-if="data.locus_start !== undefined && data.locus_start !== ''" class="mono">{{ (Number(data.locus_start) / 1e6).toFixed(2) }}–{{ (Number(data.locus_end) / 1e6).toFixed(2) }} Mb</span>
                  </template>
                </Column>
                <Column header="EA/OA"><template #body="{ data }"><span class="mono">{{ data.ea }}/{{ data.oa }}</span></template></Column>
                <Column header="BETA"><template #body="{ data }">{{ num(data.beta) }}</template></Column>
                <Column header="SE"><template #body="{ data }">{{ num(data.se) }}</template></Column>
                <Column header="P"><template #body="{ data }">{{ fmtP(Number(data.p)) }}</template></Column>
                <Column header="Direction"><template #body="{ data }"><span class="mono dir">{{ data.direction }}</span></template></Column>
                <Column header="I²"><template #body="{ data }"><span :class="{ warnv: data.het_p !== '' && Number(data.het_p) < common.het_p_threshold }">{{ data.i2 === '' ? '–' : `${(Number(data.i2) * 100).toFixed(0)}%` }}</span></template></Column>
              </DataTable>
            </template>
          </Card>
          <!-- The selected locus: its plot (most of the width) and forest plot, below the table. -->
          <div class="locus-row" :class="{ single: !region }">
            <Card v-if="region" class="region">
              <template #title>Locus {{ region.locus_id }}: {{ region.lead.rsid || `chr${region.chr}:${region.lead.pos.toLocaleString()}` }}
                <span class="muted small mono">chr{{ region.chr }}:{{ region.locus_start.toLocaleString() }}–{{ region.locus_end.toLocaleString() }}</span></template>
              <template #subtitle>Every variant in the locus, coloured by r² with the lead SNP; the shaded band is the locus.</template>
              <template #content><RegionPlot :data="region" /></template>
            </Card>
            <Card v-if="selectedLocus">
              <template #title>{{ selectedLocus.rsid }} <span class="muted small mono">chr{{ selectedLocus.chr }}:{{ selectedLocus.pos.toLocaleString() }} · {{ selectedLocus.ea }}/{{ selectedLocus.oa }}</span></template>
              <template #subtitle>P = {{ fmtP(selectedLocus.p) }} <span v-if="selectedLocus.i2 != null"> · I² = {{ (selectedLocus.i2 * 100).toFixed(0) }}%</span><span v-if="selectedLocus.het_p != null"> · heterogeneity P = {{ fmtP(selectedLocus.het_p) }}</span></template>
              <template #content><ForestPlot :locus="selectedLocus" /></template>
            </Card>
          </div>

          <Card>
            <template #title>Datasets included</template>
            <template #subtitle>In input order: # is the dataset's position in the direction column.</template>
            <template #content>
              <DataTable :value="datasetRows" scrollable class="dt nowrap">
                <Column header="#"><template #body="{ index }">{{ index + 1 }}</template></Column>
                <Column header="Dataset"><template #body="{ data }"><span v-tooltip.top="`${cnt(data.variants_used)} variants used`">{{ data.label }}</span></template></Column>
                <Column>
                  <template #header><span>Cases / controls or N <i v-tooltip.top="HELP.n" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }">
                    <template v-if="data.trait_type === 'quantitative'">N {{ cnt(data.n_total) }}</template>
                    <template v-else>{{ cnt(data.n_cases) }} / {{ cnt(data.n_controls) }}</template>
                  </template>
                </Column>
                <Column>
                  <template #header><span>Lead loci <i v-tooltip.top="HELP.covered" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }"><span :class="{ warnv: data.lead_loci_total && data.lead_loci_covered < data.lead_loci_total / 2 }">{{ data.lead_loci_covered ?? '–' }} / {{ data.lead_loci_total ?? '–' }}</span></template>
                </Column>
                <Column>
                  <template #header><span>Same direction <i v-tooltip.top="HELP.direction" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }"><span :class="{ warnv: data.direction_agreement != null && data.direction_agreement < 0.75 }">{{ data.direction_agreement == null ? '–' : `${Math.round(data.direction_agreement * 100)}%` }}</span></template>
                </Column>
                <Column>
                  <template #header><span>Outlier loci <i v-tooltip.top="HELP.outliers" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }"><span :class="{ warnv: data.outlier_loci > 0 }">{{ data.outlier_loci ?? '–' }}</span></template>
                </Column>
                <Column>
                  <template #header><span>λGC <i v-tooltip.top="HELP.lambda" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }"><span :class="{ warnv: data.lambda_gc > 1.1 }">{{ data.lambda_gc?.toFixed(3) ?? '–' }}</span></template>
                </Column>
                <Column>
                  <template #header><span>LDSC intercept <i v-tooltip.top="HELP.ldsc" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }"><span :class="{ warnv: data.ldsc_intercept > 1.1 }">{{ data.ldsc_intercept == null ? '–' : Number(data.ldsc_intercept).toFixed(3) }}</span></template>
                </Column>
                <Column>
                  <template #header><span>QC <i v-tooltip.top="HELP.qc" class="pi pi-info-circle hint" /></span></template>
                  <template #body="{ data }">
                    <span class="row nowrap qc">
                      <Tag v-if="data.status" :value="STATUS_META[data.status as DatasetStatus]?.label ?? data.status" :severity="STATUS_META[data.status as DatasetStatus]?.severity" />
                      <i v-if="data.qc_warnings?.length" v-tooltip.top="`Warned: ${data.qc_warnings.join(', ')}`" class="pi pi-exclamation-triangle warnv" />
                    </span>
                  </template>
                </Column>
              </DataTable>
            </template>
          </Card>
        </template>
        <SupportRef :parts="[pid, `meta-analysis ${ma.id}`]" :location="ma.location" />
      </div>
    </div>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
/* Same text size as the Datasets tables. */
.dt :deep(td), .dt :deep(th) { font-size: 0.78rem; padding: 0.45rem 0.6rem; }
.dt :deep(.p-tag) { font-size: 0.7rem; padding: 0.1rem 0.4rem; }
.back { text-decoration: none; font-size: 0.85rem; display: inline-flex; gap: 6px; align-items: center; margin-bottom: 10px; }
.head { align-items: flex-start; gap: 8px; }
.head a { text-decoration: none; }
.h2 { font-size: 1.35rem; margin: 0 0 4px; }
.small { font-size: 0.8rem; }
.tail { margin: 6px 0 2px; }
.tail pre { margin: 0 0 2px; padding: 6px 8px; font-size: 0.72rem; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--p-content-hover-background); border: 1px solid var(--p-content-border-color); border-radius: 4px; }
.err { white-space: pre-wrap; font-size: 0.75rem; margin: 0; max-height: 200px; overflow: auto; }
.outlink { margin-top: 8px; }
.tiles { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin-bottom: 16px; }
.stat-detail { font-size: 0.75rem; color: var(--gh-muted); margin-top: 2px; }
.plots { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: stretch; margin-bottom: 16px; }
.hits { margin-bottom: 16px; }
.hint { color: var(--gh-muted); cursor: help; font-size: 0.85em; margin-left: 3px; font-weight: 400; }
.qc { gap: 6px; }
.locus-row { display: grid; grid-template-columns: minmax(0, 2.2fr) minmax(0, 1fr); gap: 16px; align-items: stretch; margin-bottom: 16px; }
.locus-row.single { grid-template-columns: minmax(0, 1fr); }
@media (max-width: 1000px) { .plots, .locus-row { grid-template-columns: 1fr; } }
.info { font-size: 0.8rem; line-height: 1.5; background: var(--p-surface-0, #fff); border: 1px solid var(--gh-border); border-radius: 8px;
  padding: 10px 14px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 4px; }
.info .who { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }
.info .who .pi { cursor: help; font-size: 0.85em; }
.info .settings { display: flex; flex-wrap: wrap; gap: 2px 16px; align-items: baseline; }
.info .settings .muted { margin-right: 3px; }
.info .toggle { white-space: nowrap; }
.info .all-settings { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 8px 32px; margin-top: 10px;
  padding-top: 10px; border-top: 1px solid var(--gh-border); }
.info h5 { margin: 0 0 4px; font-size: 0.8rem; }
.info dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 14px; margin: 0; }
.info dt { color: var(--gh-muted); }
.info dd { margin: 0; }
.info dd.changed { font-weight: 600; }
.dir { letter-spacing: 1px; }
.warnv { color: #b45309; font-weight: 600; }
.clickable :deep(tbody tr) { cursor: pointer; }
:deep(tr.sel td) { background: #eef0f8 !important; }
.clickable :deep(td) { white-space: nowrap; }
.nowrap :deep(td), .nowrap :deep(th) { white-space: nowrap; }
</style>
