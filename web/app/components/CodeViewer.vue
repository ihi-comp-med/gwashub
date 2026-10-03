<script setup lang="ts">
// Read-only "editor" for browsing a step's source as deployed (its folder in pipeline/qc_steps plus the shared _lib).
interface RepoFile { path: string, lang: Lang, content: string }
const props = defineProps<{ projectId: string, stepKey: string | null }>()
const visible = defineModel<boolean>('visible', { default: false })

const info = ref<any>(null)
const files = computed<RepoFile[]>(() => (info.value?.files ?? []).map((f: any) => ({ ...f, lang: langOf(f.path) })))
const openTabs = ref<string[]>([])
const active = ref('')
const loadError = ref<string | null>(null)

watch(() => [visible.value, props.stepKey], async () => {
  if (!visible.value || !props.stepKey) return
  info.value = null
  loadError.value = null
  try { info.value = await api(`/projects/${props.projectId}/steps/files`, { query: { key: props.stepKey } }) }
  catch (e: any) { loadError.value = e.message; return }
  active.value = files.value[0]?.path ?? ''
  openTabs.value = active.value ? [active.value] : []
}, { immediate: true })

function open(path: string) {
  if (!openTabs.value.includes(path)) openTabs.value.push(path)
  active.value = path
}
function close(path: string) {
  const i = openTabs.value.indexOf(path)
  openTabs.value.splice(i, 1)
  if (active.value === path) active.value = openTabs.value[Math.max(0, i - 1)] ?? ''
}

const tree = computed(() => {
  const dirs = new Map<string, RepoFile[]>()
  for (const f of files.value) {
    const d = f.path.includes('/') ? f.path.slice(0, f.path.lastIndexOf('/')) : ''
    dirs.set(d, [...(dirs.get(d) ?? []), f])
  }
  return [...dirs.entries()].sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
})
const collapsed = ref(new Set<string>())
function toggleDir(d: string) {
  const s = new Set(collapsed.value)
  s.has(d) ? s.delete(d) : s.add(d)
  collapsed.value = s
}

const file = computed(() => files.value.find(f => f.path === active.value))
const lines = computed(() => file.value ? file.value.content.replace(/\n$/, '').split('\n').map(l => highlight(l, file.value!.lang)) : [])
const basename = (p: string) => p.split('/').pop()
const icon = (f: RepoFile) => ({ python: 'pi pi-code', r: 'pi pi-chart-line', yaml: 'pi pi-cog', markdown: 'pi pi-book', tsv: 'pi pi-table', json: 'pi pi-code', text: 'pi pi-file' })[f.lang]
const langLabel = (l?: Lang) => ({ python: 'Python', r: 'R', yaml: 'YAML', markdown: 'Markdown', tsv: 'TSV', json: 'JSON', text: 'Text' })[l ?? 'text']
const src = computed(() => info.value?.source ?? {})
const repoName = computed(() => src.value.repo === 'gwashub' ? 'gwashub' : String(src.value.repo ?? '').split('/').filter(Boolean).slice(-2).join('/'))
const refLabel = computed(() => src.value.repo === 'gwashub' ? 'working tree' : `${String(src.value.commit ?? '').slice(0, 7)} (${src.value.ref})`)
const copied = ref(false)
async function copy() {
  try { await navigator.clipboard.writeText(file.value?.content ?? ''); copied.value = true; setTimeout(() => (copied.value = false), 1500) }
  catch { /* clipboard unavailable */ }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal maximizable class="code-dialog" :draggable="false" :pt="{ content: { class: 'cd-content' } }">
    <template #header>
      <div v-if="info" class="cd-head">
        <i :class="src.repo === 'gwashub' ? 'pi pi-box' : 'pi pi-github'" />
        <div class="cd-title">
          <strong>{{ info.title }}</strong>
          <span class="mono small">{{ repoName }} @ {{ refLabel }}</span>
        </div>
      </div>
      <span v-else-if="loadError" class="err">{{ loadError }}</span>
    </template>

    <div class="editor">
      <aside class="tree">
        <div class="tree-head">{{ repoName.split('/').pop() }}</div>
        <template v-for="[dir, fs] in tree" :key="dir">
          <button v-if="dir" class="node dir" @click="toggleDir(dir)">
            <i :class="collapsed.has(dir) ? 'pi pi-angle-right' : 'pi pi-angle-down'" /><i class="pi pi-folder" /> {{ dir }}
          </button>
          <template v-if="!collapsed.has(dir)">
            <button v-for="f in fs" :key="f.path" :class="['node', 'file', { on: f.path === active, nested: !!dir }]" @click="open(f.path)">
              <i :class="icon(f)" /> {{ basename(f.path) }}
            </button>
          </template>
        </template>
      </aside>

      <section class="pane">
        <div class="tabs">
          <div v-for="t in openTabs" :key="t" :class="['tab', { on: t === active }]" @click="active = t">
            <span>{{ basename(t) }}</span>
            <button class="x" aria-label="Close tab" @click.stop="close(t)"><i class="pi pi-times" /></button>
          </div>
          <span class="spacer" />
          <button class="tool" :title="copied ? 'Copied' : 'Copy file'" @click="copy"><i :class="copied ? 'pi pi-check' : 'pi pi-copy'" /></button>
        </div>
        <div v-if="file" class="code">
          <div class="gutter"><div v-for="(_, i) in lines" :key="i">{{ i + 1 }}</div></div>
          <pre class="src"><code><div v-for="(l, i) in lines" :key="i" class="ln" v-html="l" /></code></pre>
        </div>
        <div v-else class="empty">No file open.</div>
        <div class="status">
          <span><i class="pi pi-lock" /> Read-only</span>
          <span>{{ file?.path }}</span>
          <span class="spacer" />
          <span>{{ lines.length }} lines</span>
          <span>{{ langLabel(file?.lang) }}</span>
          <span>UTF-8</span>
        </div>
      </section>
    </div>
  </Dialog>
</template>

<style scoped>
.cd-head { display: flex; gap: 12px; align-items: center; min-width: 0; }
.cd-head > i { font-size: 1.2rem; color: var(--gh-accent); }
.cd-title { display: flex; flex-direction: column; min-width: 0; }
.small { font-size: 0.78rem; color: var(--gh-muted); }
.err { color: #b91c1c; }
.editor { display: grid; grid-template-columns: 230px minmax(0, 1fr); height: 100%; min-height: 0; background: #1e1e1e; color: #d4d4d4; border-radius: 8px; overflow: hidden; font-size: 13px; }
@media (max-width: 760px) { .editor { grid-template-columns: 1fr; } .tree { display: none; } }
.tree { background: #252526; border-right: 1px solid #333; overflow: auto; padding-bottom: 8px; }
.tree-head { text-transform: uppercase; font-size: 11px; letter-spacing: .06em; color: #bbb; padding: 10px 12px 6px; font-weight: 600; }
.node { display: flex; gap: 6px; align-items: center; width: 100%; background: none; border: 0; color: #ccc; text-align: left; padding: 3px 12px; cursor: pointer; font: inherit; font-size: 13px; white-space: nowrap; }
.node:hover { background: #2a2d2e; }
.node.on { background: #37373d; color: #fff; }
.node.nested { padding-left: 32px; }
.node i { font-size: 12px; color: #9cdcfe; }
.node.dir i { color: #c5c5c5; }
.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.tabs { display: flex; background: #252526; border-bottom: 1px solid #333; align-items: stretch; overflow-x: auto; }
.tab { display: flex; gap: 8px; align-items: center; padding: 8px 10px 8px 14px; color: #969696; background: #2d2d2d; border-right: 1px solid #252526; cursor: pointer; white-space: nowrap; }
.tab.on { background: #1e1e1e; color: #fff; border-top: 1px solid #303880; }
.x { background: none; border: 0; color: inherit; cursor: pointer; padding: 2px; border-radius: 3px; display: flex; }
.x:hover { background: #444; }
.x i { font-size: 10px; }
.tool { background: none; border: 0; color: #ccc; padding: 0 12px; cursor: pointer; display: flex; align-items: center; text-decoration: none; }
.tool:hover { color: #fff; }
.note { background: #2b2b1e; color: #e5d58a; font-size: 12px; padding: 6px 14px; border-bottom: 1px solid #333; }
.code { flex: 1; display: flex; overflow: auto; min-height: 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.55; }
.gutter { color: #6e7681; text-align: right; padding: 10px 12px 10px 16px; user-select: none; position: sticky; left: 0; background: #1e1e1e; }
.src { margin: 0; padding: 10px 24px 10px 4px; flex: 1; }
.ln { white-space: pre; min-height: 1.55em; }
.ln:hover { background: #2a2d2e; }
.empty { flex: 1; display: flex; align-items: center; justify-content: center; color: #777; }
.status { display: flex; gap: 16px; background: #303880; color: #fff; font-size: 11.5px; padding: 3px 12px; white-space: nowrap; overflow: hidden; }
.status i { font-size: 10px; }
.src :deep(.tk-com) { color: #6a9955; }
.src :deep(.tk-str) { color: #ce9178; }
.src :deep(.tk-num) { color: #b5cea8; }
.src :deep(.tk-kw) { color: #c586c0; }
.src :deep(.tk-fn) { color: #dcdcaa; }
.src :deep(.tk-key) { color: #9cdcfe; }
</style>
