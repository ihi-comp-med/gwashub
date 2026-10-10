<script setup lang="ts">
const { pid, project, error: projectError, isReviewer, refresh } = useProject()
const toast = useToast()
const inviteOpen = ref(false)
const deleteOpen = ref(false)
async function setRole(m: any, role: string) {
  try {
    await api(`/projects/${pid.value}/members/${m.id}`, { method: 'PATCH', body: { role } })
    toast.add({ severity: 'success', summary: `${m.name} is now ${role === 'reviewer' ? 'a reviewer' : 'an uploader'}`, life: 3000 })
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not change the role', detail: e.message, life: 5000 }) }
}
async function removeMember(m: any) {
  if (!window.confirm(`Remove ${m.name} from ${project.value.short}? They keep their account, and datasets they uploaded stay in the project.`)) return
  try {
    await api(`/projects/${pid.value}/members/${m.id}`, { method: 'DELETE' })
    toast.add({ severity: 'success', summary: `${m.name} removed`, life: 3000 })
    refresh()
  }
  catch (e: any) { toast.add({ severity: 'error', summary: 'Could not remove', detail: e.message, life: 5000 }) }
}
const memberMenu = ref()
const menuFor = ref<any>(null)
const menuItems = computed(() => !menuFor.value ? [] : [
  menuFor.value.role === 'reviewer'
    ? { label: 'Make uploader', icon: 'pi pi-arrow-down', command: () => setRole(menuFor.value, 'uploader') }
    : { label: 'Make reviewer', icon: 'pi pi-arrow-up', command: () => setRole(menuFor.value, 'reviewer') },
  { label: 'Remove from project', icon: 'pi pi-user-minus', command: () => removeMember(menuFor.value) },
])
function openMenu(ev: Event, m: any) { menuFor.value = m; memberMenu.value.toggle(ev) }
const statuses: DatasetStatus[] = ['passed', 'review', 'changes_requested', 'failed', 'processing', 'pending']
const groupings = [
  { label: 'Phenotype × ancestry × sex', value: 'phenotype_ancestry_sex' },
  { label: 'Ancestry × sex', value: 'ancestry_sex' },
  { label: 'Ancestry', value: 'ancestry' },
  { label: 'Sex', value: 'sex' },
  { label: 'Phenotype', value: 'phenotype' },
]
const groupBy = ref('phenotype_ancestry_sex')
const fieldLabel: Record<string, string> = { phenotype: 'Phenotype', ancestry: 'Ancestry', sex: 'Sex' }
// Blank a cell when it and every column to its left repeat the previous row (reads as a hierarchy).
const repeats = (i: number, j: number) => i > 0 && summary.value.fields.slice(0, j + 1).every((f: string) => rows.value[i - 1]?.[f] === rows.value[i][f])
const { data: summary } = useApiGet<any>(() => `/projects/${pid.value}/summary?by=${groupBy.value}`, { keep: true })
const rows = computed(() => summary.value?.rows ?? [])
const datasetsLink = (query: Record<string, string>) => ({ path: `/projects/${pid.value}/datasets`, query })
const rowQuery = (r: any) => Object.fromEntries((summary.value?.fields ?? []).map((f: string) => [f, r[f]]))
// Sample sizes by (phenotype ×) ancestry × sex: every dataset, or those that haven't failed QC.
const excludeFailed = ref(false)
// keep: the current table stays while the other one loads (no jump when toggling)
const { data: sizes } = useApiGet<any>(() => `/projects/${pid.value}/sample-sizes?exclude_failed=${excludeFailed.value}`, { keep: true })
const sizeRows = computed(() => sizes.value?.rows ?? [])
const sizeRepeats = (i: number, j: number) => i > 0 && sizes.value.fields.slice(0, j + 1).every((f: string) => sizeRows.value[i - 1]?.[f] === sizeRows.value[i][f])
const sizeTotal = (k: string) => sizeRows.value.some((r: any) => r[k] != null) ? sizeRows.value.reduce((a: number, r: any) => a + (r[k] ?? 0), 0) : null
const fmtCount = (v: number | null | undefined) => v == null ? '–' : v.toLocaleString()
const reviewers = computed(() => project.value?.members.filter((m: any) => m.role === 'reviewer') ?? [])
const uploaders = computed(() => project.value?.members.filter((m: any) => m.role === 'uploader') ?? [])
</script>

<template>
  <div v-if="project">
    <ProjectHeader :project="project" />
    <div class="container page">
      <div class="layout">
        <div class="main">
          <Card>
            <template #title>About this project</template>
            <template #content>
              <p class="m0">{{ project.description }}</p>
              <div class="row phen">
                <span class="muted">Phenotypes:</span>
                <Tag v-for="ph in project.phenotypes" :key="ph" :value="ph" severity="secondary" />
              </div>
            </template>
          </Card>
          <PracticeFiles v-if="project.is_practice" />

          <Card v-if="sizeRows.length">
            <template #title>
              <div class="row">Sample sizes<span class="spacer" />
                <label class="row toggle small"><ToggleSwitch v-model="excludeFailed" input-id="exclude-failed" />
                  <span>Exclude datasets that failed QC</span></label>
              </div>
            </template>
            <template #subtitle>Cases, controls and total N, summed over {{ excludeFailed ? "the datasets that haven't failed QC" : 'every dataset submitted' }}.</template>
            <template #content>
              <div class="table-wrap">
                <table class="summary">
                  <thead>
                    <tr>
                      <th v-for="f in sizes.fields" :key="f">{{ fieldLabel[f] }}</th>
                      <th class="num">Datasets</th>
                      <th class="num">Cases</th>
                      <th class="num">Controls</th>
                      <th class="num">N total</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(r, i) in sizeRows" :key="i" :class="{ first: sizes.fields.length > 1 && sizeRows[i - 1]?.[sizes.fields[0]] !== r[sizes.fields[0]] }">
                      <td v-for="(f, j) in sizes.fields" :key="f" :class="{ strong: j < sizes.fields.length - 1 }">
                        {{ j < sizes.fields.length - 1 && sizeRepeats(i, j) ? '' : r[f] }}
                      </td>
                      <td class="num">{{ fmtCount(r.datasets) }}</td>
                      <td class="num">{{ fmtCount(r.n_cases) }}</td>
                      <td class="num">{{ fmtCount(r.n_controls) }}</td>
                      <td class="num strong">{{ fmtCount(r.n_total) }}</td>
                    </tr>
                  </tbody>
                  <tfoot v-if="!sizes.fields.includes('phenotype')"><!-- no grand total across phenotypes -->
                    <tr>
                      <td :colspan="sizes.fields.length">Total</td>
                      <td class="num">{{ fmtCount(sizeTotal('datasets')) }}</td>
                      <td class="num">{{ fmtCount(sizeTotal('n_cases')) }}</td>
                      <td class="num">{{ fmtCount(sizeTotal('n_controls')) }}</td>
                      <td class="num">{{ fmtCount(sizeTotal('n_total')) }}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </template>
          </Card>

          <Card data-tour="overview-summary">
            <template #title>
              <div class="row">Dataset summary<span class="spacer" />
                <Select v-model="groupBy" :options="groupings" option-label="label" option-value="value" size="small" aria-label="Group by" />
              </div>
            </template>
            <template #subtitle>Raw files submitted, by QC outcome. Click a number to see those datasets.</template>
            <template #content>
              <p v-if="!rows.length" class="muted">No datasets yet. <NuxtLink :to="`/projects/${pid}/upload`">Upload the first one</NuxtLink>.</p>
              <div v-else class="table-wrap">
                <table class="summary">
                  <thead>
                    <tr>
                      <th v-for="f in summary.fields" :key="f">{{ fieldLabel[f] }}</th>
                      <th v-for="s in statuses" :key="s" class="num">{{ STATUS_META[s].label }}</th>
                      <th class="num">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(r, i) in rows" :key="i" :class="{ first: summary.fields.length > 1 && rows[i - 1]?.[summary.fields[0]] !== r[summary.fields[0]] }">
                      <td v-for="(f, j) in summary.fields" :key="f" :class="{ strong: j < summary.fields.length - 1 }">
                        {{ j < summary.fields.length - 1 && repeats(i, j) ? '' : r[f] }}
                      </td>
                      <td v-for="s in statuses" :key="s" class="num">
                        <NuxtLink v-if="r[s]" :to="datasetsLink({ ...rowQuery(r), status: s })" :class="['cnt', s]">{{ r[s] }}</NuxtLink>
                        <span v-else class="zero">–</span>
                      </td>
                      <td class="num strong"><NuxtLink :to="datasetsLink(rowQuery(r))" class="cnt">{{ r.total }}</NuxtLink></td>
                    </tr>
                  </tbody>
                  <tfoot>
                    <tr>
                      <td :colspan="summary.fields.length">Total</td>
                      <td v-for="s in statuses" :key="s" class="num"><NuxtLink :to="datasetsLink({ status: s })" :class="['cnt', s]">{{ summary.totals[s] }}</NuxtLink></td>
                      <td class="num"><NuxtLink :to="datasetsLink({})" class="cnt">{{ summary.totals.total }}</NuxtLink></td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </template>
          </Card>
        </div>

        <aside class="side">
          <Card data-tour="overview-members">
            <template #title><div class="row">Members<span class="spacer" />
              <Button v-if="isReviewer && !project.is_practice" label="Invite" icon="pi pi-user-plus" size="small" outlined @click="inviteOpen = true" /></div></template>
            <template #content>
              <p v-if="project.is_practice" class="muted small m0">Everyone is a reviewer in the Practice project; you see only yourself here.</p>
              <h4 class="grp">Reviewers</h4>
              <ul class="members">
                <li v-for="m in reviewers" :key="m.id" class="row">
                  <Avatar :label="initials(m.name)" shape="circle" class="av rev" />
                  <div class="who"><div class="strong">{{ m.name }}<span v-if="m.is_me" class="muted"> (you)</span></div><div class="muted small">{{ m.email ?? m.institution }}</div></div>
                  <span class="spacer" /><span class="muted small">{{ m.uploads }} {{ m.uploads === 1 ? 'file' : 'files' }}</span>
                  <Button v-if="isReviewer && !project.is_practice" icon="pi pi-ellipsis-v" text rounded size="small" :aria-label="`Manage ${m.name}`" @click="openMenu($event, m)" />
                </li>
              </ul>
              <h4 class="grp">Uploaders</h4>
              <p v-if="!uploaders.length" class="muted small m0">None yet.</p>
              <ul class="members">
                <li v-for="m in uploaders" :key="m.id" class="row">
                  <Avatar :label="initials(m.name)" shape="circle" class="av" />
                  <div class="who"><div class="strong">{{ m.name }}<span v-if="m.is_me" class="muted"> (you)</span></div><div class="muted small">{{ m.email ?? m.institution }}</div></div>
                  <span class="spacer" /><span class="muted small">{{ m.uploads }} {{ m.uploads === 1 ? 'file' : 'files' }}</span>
                  <Button v-if="isReviewer && !project.is_practice" icon="pi pi-ellipsis-v" text rounded size="small" :aria-label="`Manage ${m.name}`" @click="openMenu($event, m)" />
                </li>
              </ul>
            </template>
          </Card>
          <Card v-if="project.pipeline">
            <template #title>Pipeline</template>
            <template #content>
              <dl class="kv">
                <dt>Version</dt><dd>v{{ project.pipeline.version }}</dd>
                <dt>Steps</dt><dd>{{ project.pipeline.steps }}<span v-if="project.pipeline.consortium_steps"> ({{ project.pipeline.consortium_steps }} from consortia)</span></dd>
                <dt>Updated</dt><dd>{{ fmtDate(project.pipeline.created_at) }}</dd>
              </dl>
              <NuxtLink :to="`/projects/${pid}/pipeline`" class="small">{{ isReviewer ? 'Pipeline settings' : 'View pipeline settings' }} →</NuxtLink>
            </template>
          </Card>
          <Card v-if="project.is_admin" class="danger">
            <template #title>Delete project</template>
            <template #content>
              <p class="muted small m0">Platform admins only. Deletes the project, its datasets, QC and meta-analysis results and their files.</p>
              <Button label="Delete project…" icon="pi pi-trash" severity="danger" size="small" outlined class="delbtn" @click="deleteOpen = true" />
            </template>
          </Card>
        </aside>
      </div>
    </div>
    <Menu ref="memberMenu" :model="menuItems" popup />
    <InviteMemberDialog v-model:visible="inviteOpen" :project-id="pid" :project-name="project.short" @added="refresh" />
    <DeleteProjectDialog v-if="project.is_admin" v-model:visible="deleteOpen" :project="project" :datasets="summary?.totals?.total ?? 0" />
  </div>
  <ProjectError v-else :error="projectError" />
</template>

<style scoped>
.toggle { gap: 8px; font-weight: 400; cursor: pointer; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; align-items: start; }
@media (max-width: 960px) { .layout { grid-template-columns: 1fr; } }
.main, .side { display: flex; flex-direction: column; gap: 16px; }
.m0 { margin: 0; }
.danger { border: 1px solid var(--p-red-200, #fecaca); }
.delbtn { margin-top: 10px; }
.phen { margin-top: 14px; gap: 6px; }
.tile { text-decoration: none; color: inherit; }
.tile :deep(.p-card) { height: 100%; transition: box-shadow .15s; }
.tile:hover :deep(.p-card) { box-shadow: 0 4px 14px rgba(48, 56, 128, .12); }
.stat i { font-size: 1rem; vertical-align: middle; }
.table-wrap { overflow-x: auto; }
.summary { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
.summary th { text-align: left; font-weight: 600; color: var(--gh-muted); font-size: 0.8rem; padding: 8px 10px; border-bottom: 1px solid var(--gh-border); white-space: nowrap; }
.summary td { padding: 7px 10px; border-bottom: 1px solid #f0f3f5; }
.summary tr.first td { border-top: 1px solid var(--gh-border); }
.summary tfoot td { font-weight: 700; border-top: 2px solid var(--gh-border); border-bottom: 0; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; }
.zero { color: #c3ccd3; }
.cnt { text-decoration: none; color: var(--gh-text); padding: 2px 8px; border-radius: 10px; }
.cnt:hover { background: var(--gh-bg); }
.cnt.passed { color: #15803d; } .cnt.review { color: #b45309; } .cnt.failed { color: #b91c1c; } .cnt.processing { color: #0369a1; } .cnt.pending { color: var(--gh-muted); }
.grp { margin: 0 0 8px; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--gh-muted); }
.members { list-style: none; padding: 0; margin: 0 0 16px; display: flex; flex-direction: column; gap: 10px; }
.members li { flex-wrap: nowrap; }
.who { min-width: 0; }
.av { background: #e2e8f0; color: #334155; font-size: 0.75rem; flex-shrink: 0; }
.av.rev { background: #d9ddef; color: #303880; }
.small { font-size: 0.8rem; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0 0 12px; font-size: 0.85rem; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; }
</style>
