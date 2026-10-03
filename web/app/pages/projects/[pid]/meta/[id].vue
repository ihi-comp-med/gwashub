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
watch(ma, (v) => { if (v?.forest?.length && !selectedLocus.value) selectedLocus.value = v.forest[0] })
const forestFor = (row: any) => ma.value?.forest?.find((f: any) => f.chr === String(row.chr) && f.pos === Number(row.pos))
function pickRow(row: any) { const f = forestFor(row); if (f) selectedLocus.value = f }
function pickPoint(p: { chr: string, pos: number }) {
  const near = ma.value?.forest?.filter((f: any) => f.chr === p.chr).sort((a: any, b: any) => Math.abs(a.pos - p.pos) - Math.abs(b.pos - p.pos))[0]
  if (near && Math.abs(near.pos - p.pos) < 1_000_000) selectedLocus.value = near
}
const fileUrl = (name: string) => `/api/meta/${id.value}/files/${name}`
const s = computed(() => ma.value?.summary ?? {})
const studies = computed(() => s.value.studies ?? [])
async function remove() {
  if (!window.confirm('Delete this meta-analysis? This cannot be undone.')) return
  try {
    await api(`/meta/${id.value}`, { method: 'DELETE' })
    navigateTo(`/projects/${pid.value}/meta`)
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 4000 }) }
}
const settingLabels: Record<string, string> = {
  method: 'Method', min_studies: 'Min datasets per variant', maf_threshold: 'MAF filter', drop_palindromic: 'Drop palindromic',
  genomic_control: 'Genomic control', gws: 'Significance threshold', clump_kb: 'Locus window (kb)', het_p_threshold: 'Heterogeneity flag P', include_statuses: 'Eligible statuses',
}
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
            <p class="page-sub">{{ ma.n_datasets }} datasets · {{ ma.settings.method === 'random' ? 'random' : 'fixed' }} effects · run by {{ ma.created_by }} on {{ fmtDate(ma.created_at) }}</p>
          </div>
          <span class="spacer" />
          <template v-if="ma.status === 'completed'">
            <a :href="fileUrl('lead_loci.tsv')" class="p-button p-button-outlined p-button-sm"><i class="pi pi-download" />&nbsp;Lead loci</a>
            <a v-if="isReviewer" :href="fileUrl('meta_results.tsv.gz')" class="p-button p-button-sm"><i class="pi pi-download" />&nbsp;Full results</a>
          </template>
          <Button v-if="isReviewer && !['queued', 'running'].includes(ma.status)" icon="pi pi-trash" text severity="danger" aria-label="Delete" @click="remove" />
        </div>

        <Message v-if="['queued', 'running'].includes(ma.status)" severity="info" :closable="false" icon="pi pi-spin pi-spinner">Running…</Message>
        <Message v-if="ma.status === 'failed'" severity="error" :closable="false"><pre class="err">{{ ma.error }}</pre></Message>

        <template v-if="ma.status === 'completed'">
          <div class="grid tiles">
            <Card><template #content><div class="stat">{{ s.n_datasets }}</div><div class="stat-label">Datasets</div></template></Card>
            <Card><template #content><div class="stat">{{ s.n_variants?.toLocaleString() }}</div><div class="stat-label">Variants meta-analysed</div></template></Card>
            <Card><template #content><div class="stat">{{ s.n_loci }}</div><div class="stat-label">Genome-wide loci</div></template></Card>
            <Card><template #content><div class="stat">{{ s.lambda_gc?.toFixed(3) }}</div><div class="stat-label">λGC</div></template></Card>
            <Card><template #content><div class="stat" :class="{ warnv: s.n_loci_heterogeneous }">{{ s.n_loci_heterogeneous }}</div><div class="stat-label">Heterogeneous loci</div></template></Card>
          </div>

          <div class="plots">
            <Card>
              <template #title>Manhattan</template>
              <template #subtitle>Hover a peak; click it to show its forest plot.</template>
              <template #content><ManhattanPlot :data="ma.plot_data.manhattan" :gws="ma.plot_data.gws" @select="pickPoint" /></template>
            </Card>
            <Card><template #title>QQ</template><template #content><QQPlot :data="ma.plot_data.qq" /></template></Card>
          </div>

          <div class="loci">
            <Card>
              <template #title>Lead loci</template>
              <template #subtitle>P &lt; {{ Number(ma.settings.gws).toExponential(0) }}, {{ ma.settings.clump_kb }} kb windows. Direction: one symbol per dataset, in input order (? = variant missing).</template>
              <template #content>
                <DataTable :value="ma.lead_loci" size="small" row-hover class="clickable" scrollable scroll-height="420px" @row-click="e => pickRow(e.data)"
                           :row-class="(r: any) => selectedLocus && selectedLocus.chr === String(r.chr) && selectedLocus.pos === Number(r.pos) ? 'sel' : ''">
                  <Column header="Variant"><template #body="{ data }"><span class="mono">{{ data.rsid || '–' }}</span></template></Column>
                  <Column header="Position"><template #body="{ data }"><span class="mono">{{ data.chr }}:{{ Number(data.pos).toLocaleString() }}</span></template></Column>
                  <Column header="EA/OA"><template #body="{ data }"><span class="mono">{{ data.ea }}/{{ data.oa }}</span></template></Column>
                  <Column header="BETA"><template #body="{ data }">{{ Number(data.beta).toFixed(4) }}</template></Column>
                  <Column header="SE"><template #body="{ data }">{{ Number(data.se).toFixed(4) }}</template></Column>
                  <Column header="P"><template #body="{ data }">{{ fmtP(Number(data.p)) }}</template></Column>
                  <Column header="Direction"><template #body="{ data }"><span class="mono dir">{{ data.direction }}</span></template></Column>
                  <Column header="I²"><template #body="{ data }"><span :class="{ warnv: data.het_p !== '' && Number(data.het_p) < ma.settings.het_p_threshold }">{{ (Number(data.i2) * 100).toFixed(0) }}%</span></template></Column>
                </DataTable>
              </template>
            </Card>
            <Card v-if="selectedLocus">
              <template #title>{{ selectedLocus.rsid }} <span class="muted small mono">chr{{ selectedLocus.chr }}:{{ selectedLocus.pos.toLocaleString() }} · {{ selectedLocus.ea }}/{{ selectedLocus.oa }}</span></template>
              <template #subtitle>P = {{ fmtP(selectedLocus.p) }} · I² = {{ (selectedLocus.i2 * 100).toFixed(0) }}%<span v-if="selectedLocus.het_p != null"> · heterogeneity P = {{ fmtP(selectedLocus.het_p) }}</span></template>
              <template #content><ForestPlot :locus="selectedLocus" /></template>
            </Card>
          </div>

          <div class="grid two">
            <Card>
              <template #title>Datasets included</template>
              <template #content>
                <DataTable :value="studies" size="small">
                  <Column field="label" header="Dataset" />
                  <Column header="Variants used"><template #body="{ data }">{{ data.variants_used?.toLocaleString() }}</template></Column>
                  <Column header="λGC"><template #body="{ data }"><span :class="{ warnv: data.lambda_gc > 1.1 }">{{ data.lambda_gc?.toFixed(3) }}</span></template></Column>
                  <Column header="GC applied"><template #body="{ data }">{{ data.gc_applied ? `× √${data.gc_applied}` : '–' }}</template></Column>
                </DataTable>
              </template>
            </Card>
            <Card>
              <template #title>Settings</template>
              <template #content>
                <dl class="kv">
                  <template v-for="(label, k) in settingLabels" :key="k">
                    <dt>{{ label }}</dt><dd>{{ Array.isArray(ma.settings[k]) ? ma.settings[k].join(', ') : String(ma.settings[k]) }}</dd>
                  </template>
                </dl>
              </template>
            </Card>
          </div>
        </template>
      </div>
    </div>
  </div>
  <ProjectError v-if="!project" :error="projectError" />
</template>

<style scoped>
.back { text-decoration: none; font-size: 0.85rem; display: inline-flex; gap: 6px; align-items: center; margin-bottom: 10px; }
.head { align-items: flex-start; gap: 8px; }
.head a { text-decoration: none; }
.h2 { font-size: 1.35rem; margin: 0 0 4px; }
.small { font-size: 0.8rem; }
.err { white-space: pre-wrap; font-size: 0.75rem; margin: 0; max-height: 200px; overflow: auto; }
.tiles { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); margin-bottom: 16px; }
.plots { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; margin-bottom: 16px; }
.loci { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 16px; align-items: start; margin-bottom: 16px; }
@media (max-width: 1000px) { .plots, .loci { grid-template-columns: 1fr; } }
.two { grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); }
.dir { letter-spacing: 1px; }
.warnv { color: #b45309; font-weight: 600; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 0; font-size: 0.88rem; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; }
.clickable :deep(tbody tr) { cursor: pointer; }
:deep(tr.sel td) { background: #eef0f8 !important; }
.clickable :deep(td) { white-space: nowrap; }
</style>
