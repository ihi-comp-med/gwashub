<script setup lang="ts">
// Validation: each test compares GWASHub with the original tool or pipeline on the same inputs (validation/ in the
// repository; plan in docs/VALIDATION_PLAN.md). Results are committed with the code, so this shows the deployed version.
const { data: tests } = useApiGet<any[]>('/validation')
const plots = ref<Record<string, any>>({})
watch(tests, async (list) => {
  for (const t of list ?? []) {
    if (t.has_plot && !plots.value[t.test]) plots.value[t.test] = await api(`/validation/${t.test}/plot`).catch(() => null)
  }
}, { immediate: true })

const STATUS: Record<string, { label: string, severity: string, icon: string }> = {
  passed: { label: 'Passed', severity: 'success', icon: 'pi pi-check-circle' },
  failed: { label: 'Failed', severity: 'danger', icon: 'pi pi-times-circle' },
  not_run: { label: 'Not run yet', severity: 'secondary', icon: 'pi pi-clock' },
}
const status = (s: string) => STATUS[s] ?? STATUS.not_run
const counts = computed(() => {
  const c = { passed: 0, failed: 0, not_run: 0 } as Record<string, number>
  for (const t of tests.value ?? []) c[t.status in c ? t.status : 'not_run']++
  return c
})
const num = (v: any) => {
  if (typeof v !== 'number') return String(v)
  if (v === 0 || Number.isInteger(v)) return v.toLocaleString()
  return Math.abs(v) < 1e-3 || Math.abs(v) >= 1e4 ? v.toExponential(1) : v.toPrecision(3)
}
const threshold = (c: any) => c.direction === 'equal' ? `= ${num(c.threshold)}` : `≤ ${num(c.threshold)}`
const fileUrl = (t: string, f: string) => `/api/validation/${t}/files/${f}`
// Checks under their group headings (a check's `group` names its run or comparison), in the order they come.
const grouped = (checks: any[]) => {
  const out: { name: string, checks: any[] }[] = []
  for (const c of checks) {
    const name = c.group ?? ''
    const last = out[out.length - 1]
    if (last && last.name === name) last.checks.push(c)
    else out.push({ name, checks: [c] })
  }
  return out
}
const passedCount = (t: any) => t.checks.filter((c: any) => c.passed).length
// The site analyses a test compared: meta-analyses (one, with its re-run, or one per run), or QC runs (test 4).
const siteRuns = (t: any) => {
  const sr = t.site_run ?? {}
  const ids = (r: any) => [r.meta_analysis, r.meta_analysis_rerun].filter(v => v != null).join(' and ')
  if (sr.runs) return `meta-analyses ${Object.entries(sr.runs).map(([k, r]: [string, any]) => `${k}: ${ids(r)}`).join(' · ')}`
  if (sr.meta_analysis != null) return `meta-analyses ${ids(sr)}`
  if (sr.planted?.run != null) {
    const n = Object.keys(sr.cases ?? {}).length
    return `QC run ${sr.planted.run} (planted file)${n ? ` · ${n} whole-file uploads` : ''}`
  }
  return ''
}
</script>

<template>
  <div class="container page validation">
    <h1 class="page-title">Validation</h1>
    <p class="lead">Each test runs the same input files two ways: through GWASHub, and through the original tool or
      pipeline run outside it (for example GWAMA, by hand). The outputs are then compared against thresholds fixed
      before the test was run. Every site analysis is also run twice, and the two outputs must be identical.</p>

    <div v-if="tests" class="summary row">
      <span><i class="pi pi-check-circle ok" /> {{ counts.passed }} passed</span>
      <span><i class="pi pi-times-circle bad" /> {{ counts.failed }} failed</span>
      <span><i class="pi pi-clock muted" /> {{ counts.not_run }} not run yet</span>
    </div>

    <div v-if="!tests" class="loading"><ProgressSpinner /></div>
    <Card v-for="t in tests ?? []" :key="t.test" class="test">
      <template #title>
        <div class="row head">
          <span>{{ t.title }}</span>
          <Tag :value="status(t.status).label" :severity="status(t.status).severity" :icon="status(t.status).icon" />
          <span class="spacer" />
          <span v-if="t.run_date" class="muted small">Run {{ fmtDate(t.run_date) }}</span>
        </div>
      </template>
      <template #content>
        <p class="claim">{{ t.claim }}</p>
        <div v-if="t.status === 'not_run'" class="muted">This test is set up but hasn't been run yet. Inputs: {{ t.inputs.join(', ') }}.</div>
        <template v-else>
          <div class="overview">
            <div>
              <p class="outcome">
                <i :class="passedCount(t) === t.checks.length ? 'pi pi-check-circle ok' : 'pi pi-times-circle bad'" />
                {{ passedCount(t) }} of {{ t.checks.length }} checks passed
              </p>
              <dl class="kv small">
                <template v-if="t.inputs.length"><dt>Inputs</dt><dd>{{ t.inputs.join(', ') }}</dd></template>
                <template v-if="t.versions.gwama"><dt>GWAMA</dt><dd>{{ t.versions.gwama }}</dd></template>
                <template v-if="t.versions.metal"><dt>METAL</dt><dd>{{ t.versions.metal }}</dd></template>
                <template v-if="t.versions.site_pipeline_version"><dt>QC pipeline</dt><dd>version {{ t.versions.site_pipeline_version }}</dd></template>
                <template v-if="siteRuns(t)"><dt>Site runs</dt><dd>{{ t.site_run.project }} · {{ siteRuns(t) }}</dd></template>
                <template v-if="t.archive"><dt>Data archive</dt><dd>{{ t.archive }}</dd></template>
              </dl>
            </div>
            <div v-if="plots[t.test]" class="plotbox">
              <ValidationScatter :data="plots[t.test]" />
              <p class="caption muted">Each point is a variant (all strong signals and a random sample). The dashed line is perfect agreement.</p>
            </div>
          </div>
          <details class="details" :open="t.status === 'failed'">
            <summary>Checks, notes and files</summary>
            <table class="checks">
              <thead><tr><th>Check</th><th>Threshold</th><th>Observed</th><th /></tr></thead>
              <tbody v-for="g in grouped(t.checks)" :key="g.name">
                <tr v-if="g.name" class="grp"><td colspan="4">{{ g.name }}</td></tr>
                <tr v-for="c in g.checks" :key="c.id" :class="{ fail: !c.passed }">
                  <td>{{ c.label }}</td>
                  <td class="mono">{{ threshold(c) }}</td>
                  <td class="mono">{{ num(c.observed) }}</td>
                  <td><i :class="c.passed ? 'pi pi-check ok' : 'pi pi-times bad'" :aria-label="c.passed ? 'passed' : 'failed'" /></td>
                </tr>
              </tbody>
            </table>
            <template v-if="t.notes.length">
              <h4 class="sub">Notes</h4>
              <ul class="notes">
                <li v-for="(n, i) in t.notes" :key="i" class="small">{{ n }}</li>
              </ul>
            </template>
            <div v-if="t.files.length" class="files row small">
              <span class="muted">Files:</span>
              <a v-for="f in t.files" :key="f" :href="fileUrl(t.test, f)" class="mono"><i class="pi pi-download" /> {{ f }}</a>
            </div>
          </details>
        </template>
      </template>
    </Card>

  </div>
</template>

<style scoped>
.lead { max-width: 820px; }
.summary { gap: 20px; margin: 4px 0 18px; font-size: 0.9rem; }
.ok { color: #15803d; }
.bad { color: #b91c1c; }
.test { margin-bottom: 18px; }
.head { gap: 10px; align-items: center; font-size: 1.05rem; }
.claim { margin: 0 0 14px; }
.overview { display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: start; }
@media (max-width: 800px) { .overview { grid-template-columns: 1fr; } }
.outcome { margin: 0 0 10px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.details { margin-top: 16px; border-top: 1px solid var(--gh-border); padding-top: 10px; }
.details summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; color: var(--gh-accent); padding: 4px 0; }
.details[open] summary { margin-bottom: 10px; }
.checks tr.grp td { font-weight: 600; font-size: 0.8rem; color: var(--gh-muted); padding-top: 14px; border-bottom: 1px solid var(--gh-border); }
.checks tbody:first-of-type tr.grp td { padding-top: 6px; }
.sub { margin: 16px 0 4px; font-size: 0.85rem; color: var(--gh-muted); font-weight: 600; }
.checks { border-collapse: collapse; width: 100%; font-size: 0.86rem; }
.checks th { text-align: left; color: var(--gh-muted); font-weight: 500; border-bottom: 1px solid var(--gh-border); padding: 4px 8px; }
.checks td { border-bottom: 1px solid var(--gh-border); padding: 5px 8px; vertical-align: top; }
.checks tr.fail td { background: #fef2f2; }
.mono { font-family: ui-monospace, Menlo, monospace; white-space: nowrap; }
.notes { margin: 0; padding-left: 18px; color: var(--gh-muted); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; }
.plotbox { max-width: 280px; }
.caption { font-size: 0.72rem; line-height: 1.35; margin: 4px 0 0; }
.files { gap: 12px; flex-wrap: wrap; margin-top: 14px; }
.files a { text-decoration: none; }
.loading { display: flex; justify-content: center; padding: 60px; }
</style>
