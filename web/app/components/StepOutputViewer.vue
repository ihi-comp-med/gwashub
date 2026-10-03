<script setup lang="ts">
// What a step produced: its step log, console output (if it printed anything), the files it declared
// as artifacts, and any other file it wrote. Images inline, tables and text as text; everything downloadable.
const props = defineProps<{ datasetId: number, runId?: number, step: any | null }>()
const visible = defineModel<boolean>('visible', { default: false })

interface Item { key: string, label: string, path: string, kind: 'text' | 'json' | 'image' | 'table' | 'html' | 'binary', size?: number, other?: boolean }
const kindOf = (p: string): Item['kind'] => /\.(png|jpe?g|svg|gif)$/i.test(p) ? 'image' : /\.tsv$/i.test(p) ? 'table' : /\.json$/i.test(p) ? 'json'
  : /\.html?$/i.test(p) ? 'html' : /\.(gz|zip|bgz|parquet|rds|bin)$/i.test(p) ? 'binary' : 'text'
const files = ref<{ path: string, size: number }[] | null>(null)
watch(() => [visible.value, props.step, props.runId], async () => {
  files.value = null
  if (!visible.value || !props.step?.dir || !props.runId) return
  files.value = await api<any[]>(`/datasets/${props.datasetId}/runs/${props.runId}/files`, { query: { dir: props.step.dir } }).catch(() => [])
}, { immediate: true })
const items = computed<Item[]>(() => {
  const s = props.step
  if (!s?.dir) return []
  const size = (p: string) => files.value?.find(f => f.path === p)?.size
  const out: Item[] = [{ key: 'log', label: 'Step log', path: `${s.dir}/step_log.json`, kind: 'json' }]
  if (!files.value || size(`${s.dir}/output.txt`) != null)
    out.push({ key: 'console', label: 'Console output', path: `${s.dir}/output.txt`, kind: 'text' })
  for (const a of s.artifacts ?? []) {
    const p = `${s.dir}/${a.path}`
    out.push({ key: p, label: a.label || a.path, path: p, kind: kindOf(a.path) })
  }
  const shown = new Set(out.map(i => i.path))
  for (const f of files.value ?? []) {
    if (!shown.has(f.path)) out.push({ key: f.path, label: f.path.slice(s.dir.length + 1), path: f.path, kind: kindOf(f.path), other: true })
  }
  return out.map(i => ({ ...i, size: size(i.path) }))
})
const active = ref<Item | null>(null)
const text = ref('')
const loading = ref(false)
const url = (it: Item, download = false) => `/api/datasets/${props.datasetId}/files/artifact?path=${encodeURIComponent(it.path)}${props.runId ? `&run_id=${props.runId}` : ''}${download ? '&download=1' : ''}`
const MAX_LINES = 400

async function open(it: Item) {
  active.value = it
  text.value = ''
  if (it.kind === 'image' || it.kind === 'html' || it.kind === 'binary') return
  loading.value = true
  try {
    const raw = await $fetch<string>(url(it), { responseType: 'text' })
    if (it.kind === 'json') {
      try { text.value = JSON.stringify(JSON.parse(raw), null, 2) }
      catch { text.value = raw }
    }
    else text.value = raw
  }
  catch (e: any) {
    const st = e?.response?.status
    text.value = st === 404 ? '(nothing was written)' : st === 413 ? 'This file is too large to preview. Use the download button.' : `Could not load: ${e.message}`
  }
  finally { loading.value = false }
}
watch(() => [visible.value, props.step], () => { if (visible.value && items.value.length) open(items.value[0]!) }, { immediate: true })

const lines = computed(() => text.value.replace(/\n$/, '').split('\n'))
const shown = computed(() => lines.value.slice(0, MAX_LINES))
const lang = computed<Lang>(() => active.value?.kind === 'json' ? 'json' : 'text')
const table = computed(() => {
  if (active.value?.kind !== 'table' || !text.value) return null
  const [head, ...rows] = shown.value
  const cols = head?.split('\t') ?? []
  return { cols, rows: rows.filter(Boolean).map(r => r.split('\t')) }
})
const icon = (k: string) => ({ text: 'pi pi-align-left', json: 'pi pi-code', image: 'pi pi-image', table: 'pi pi-table', html: 'pi pi-file', binary: 'pi pi-box' } as any)[k]
const firstOther = computed(() => items.value.findIndex(i => i.other))
</script>

<template>
  <Dialog v-model:visible="visible" modal maximizable class="code-dialog" :draggable="false" :pt="{ content: { class: 'cd-content' } }">
    <template #header>
      <div v-if="step" class="hd">
        <i class="pi pi-desktop" />
        <div class="t"><strong>{{ step.title }}: output</strong><span class="muted small">{{ step.state }} · {{ step.duration }}s<span v-if="step.rows_after != null"> · {{ step.rows_after.toLocaleString() }} rows after</span></span></div>
      </div>
    </template>
    <div class="ov">
      <aside class="list">
        <template v-for="(it, i) in items" :key="it.key">
          <div v-if="i === firstOther" class="group muted">Other files</div>
          <button :class="['item', { on: active?.key === it.key }]" @click="open(it)">
            <i :class="icon(it.kind)" /> <span class="lbl">{{ it.label }}</span><span v-if="it.size != null" class="sz muted">{{ fmtSize(it.size) }}</span>
          </button>
        </template>
      </aside>
      <section class="pane">
        <div class="bar mono">{{ active?.path.split('/').pop() }}<span class="spacer" />
          <a v-if="active" :href="url(active, true)" class="dl" title="Download"><i class="pi pi-download" />&nbsp;Download</a>
        </div>
        <div v-if="loading" class="center"><ProgressSpinner style="width: 32px; height: 32px" /></div>
        <div v-else-if="active?.kind === 'binary'" class="center muted">No preview for this file type. Use Download.</div>
        <div v-else-if="active?.kind === 'image'" class="img"><img :src="url(active)" :alt="active.label"></div>
        <iframe v-else-if="active?.kind === 'html'" :src="url(active)" class="frame" :title="active.label" sandbox="" />
        <div v-else-if="table" class="tbl">
          <table>
            <thead><tr><th v-for="c in table.cols" :key="c">{{ c }}</th></tr></thead>
            <tbody><tr v-for="(r, i) in table.rows" :key="i"><td v-for="(v, j) in r" :key="j">{{ v }}</td></tr></tbody>
          </table>
        </div>
        <pre v-else class="code"><code><div v-for="(l, i) in shown" :key="i" class="ln"><span class="no">{{ i + 1 }}</span><span v-html="highlight(l, lang)" /></div></code></pre>
        <div v-if="lines.length > MAX_LINES && active?.kind !== 'image'" class="more muted">Showing the first {{ MAX_LINES }} of {{ lines.length.toLocaleString() }} lines; download for the rest.</div>
      </section>
    </div>
  </Dialog>
</template>

<style scoped>
.hd { display: flex; gap: 12px; align-items: center; }
.hd > i { color: var(--gh-accent); font-size: 1.1rem; }
.t { display: flex; flex-direction: column; }
.small { font-size: 0.78rem; }
.ov { display: grid; grid-template-columns: 220px minmax(0, 1fr); height: 100%; min-height: 0; border: 1px solid var(--gh-border); border-radius: 8px; overflow: hidden; }
@media (max-width: 760px) { .ov { grid-template-columns: 1fr; } .list { display: flex; overflow-x: auto; } }
.list { background: var(--gh-bg); border-right: 1px solid var(--gh-border); padding: 8px 0; overflow: auto; }
.item { display: flex; gap: 8px; align-items: center; width: 100%; background: none; border: 0; text-align: left; padding: 7px 14px; font: inherit; font-size: 0.88rem; color: var(--gh-text); cursor: pointer; white-space: nowrap; }
.item:hover { background: var(--gh-accent-soft); }
.item.on { background: var(--gh-accent-soft); color: var(--gh-accent); font-weight: 600; }
.item i { color: var(--gh-muted); font-size: 0.8rem; }
.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: #fff; }
.bar { display: flex; align-items: center; padding: 8px 14px; border-bottom: 1px solid var(--gh-border); font-size: 0.8rem; color: var(--gh-muted); }
.dl { color: var(--gh-accent); text-decoration: none; font-family: Inter, sans-serif; }
.group { font-size: 0.72rem; text-transform: uppercase; letter-spacing: .05em; padding: 12px 14px 4px; }
.lbl { overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.sz { font-size: 0.72rem; }
.center { flex: 1; display: flex; align-items: center; justify-content: center; }
.img { flex: 1; overflow: auto; padding: 16px; display: flex; justify-content: center; align-items: flex-start; }
.img img { max-width: 100%; height: auto; }
.frame { flex: 1; border: 0; width: 100%; }
.code { flex: 1; margin: 0; overflow: auto; background: #1e1e1e; color: #d4d4d4; padding: 10px 0; font-size: 12.5px; line-height: 1.55; font-family: ui-monospace, Menlo, monospace; }
.ln { white-space: pre; padding-right: 20px; }
.no { display: inline-block; width: 44px; padding-right: 12px; text-align: right; color: #6e7681; user-select: none; }
.code :deep(.tk-com) { color: #6a9955; } .code :deep(.tk-str) { color: #ce9178; } .code :deep(.tk-num) { color: #b5cea8; }
.code :deep(.tk-kw) { color: #c586c0; } .code :deep(.tk-fn) { color: #dcdcaa; }
.tbl { flex: 1; overflow: auto; }
.tbl table { border-collapse: collapse; font-size: 0.8rem; font-family: ui-monospace, Menlo, monospace; }
.tbl th, .tbl td { padding: 5px 12px; border-bottom: 1px solid var(--gh-border); text-align: left; white-space: nowrap; }
.tbl th { position: sticky; top: 0; background: var(--gh-bg); color: var(--gh-muted); font-weight: 600; }
.more { font-size: 0.78rem; padding: 6px 14px; border-top: 1px solid var(--gh-border); }
</style>
