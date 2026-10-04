<script setup lang="ts">
// Study metadata from the field catalogue (api/gwashub_api/metadata_schema.py). Edit mode renders inputs;
// readonly renders a compact summary that hides empty fields.
//   config     the project's {shown, required}: only shown fields appear; required ones are marked
//   level      'cohort' or 'dataset' to show just those sections
//   inherited  the cohort's values: shown as "from cohort", and a dataset value that differs is an override
const props = defineProps<{ readonly?: boolean, config?: { shown: string[], required: string[] } | null,
  level?: 'cohort' | 'dataset' | null, inherited?: Record<string, any> | null }>()
const model = defineModel<Record<string, any>>({ default: () => ({}) })
const { schema: allSections } = useMetadataSchema()
const isShown = (k: string) => !props.config || props.config.shown.includes(k)
const isRequired = (k: string) => !!props.config?.required.includes(k)
// Sections and fields this project shows (a grid section is switched on as a whole).
const schema = computed(() => (allSections.value ?? [])
  .filter((sec: any) => !props.level || sec.level === props.level)
  .map((sec: any) => sec.grid ? (isShown(sec.key) ? sec : null) : { ...sec, fields: sec.fields.filter((f: any) => isShown(f.key)) })
  .filter((sec: any) => sec && (sec.grid || sec.fields.length)))
const fromCohort = (sec: any, k: string) => sec.level === 'cohort' && props.inherited && props.inherited[k] !== undefined && props.inherited[k] !== null
const overrides = (sec: any, k: string) => fromCohort(sec, k) && model.value[k] !== props.inherited![k]

function set(k: string, v: any) {
  const next = { ...model.value }
  if (v === null || v === '' || v === undefined) delete next[k]
  else next[k] = v
  model.value = next
}
const dateValue = (k: string) => model.value[k] ? new Date(`${model.value[k]}T00:00:00`) : null
const toIso = (d: Date | null) => d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : null
const has = (k: string) => model.value[k] !== undefined && model.value[k] !== null && model.value[k] !== ''
const sectionHasData = (s: any) => s.fields ? s.fields.some((f: any) => has(f.key)) : s.grid.rows.some((r: any) => s.grid.cols.some((c: any) => has(`${r.key}_${c.key}`)))
const fmtVal = (f: any, v: any) => f.type === 'date' ? fmtDate(v) : f.type === 'number' || f.type === 'integer' ? fmtNum(v) : v
</script>

<template>
  <div v-if="schema" class="mf">
    <template v-if="readonly">
      <p v-if="!schema.some(sectionHasData)" class="muted m0">No study metadata yet.</p>
      <template v-for="s in schema" :key="s.key">
        <section v-if="sectionHasData(s)" class="sec">
          <h4>{{ s.title }}</h4>
          <dl v-if="s.fields" class="kv">
            <template v-for="f in s.fields" :key="f.key">
              <template v-if="has(f.key)">
                <dt>{{ f.label }}</dt>
                <dd :class="{ pre: f.type === 'textarea' }">{{ fmtVal(f, model[f.key]) }}
                  <Tag v-if="overrides(s, f.key)" v-tooltip.top="`The cohort says: ${fmtVal(f, inherited![f.key])}`" value="overrides cohort" severity="warn" class="src" />
                  <span v-else-if="fromCohort(s, f.key)" class="src muted">from cohort</span>
                </dd>
              </template>
            </template>
          </dl>
          <table v-else class="grid-t">
            <thead><tr><th /><th v-for="c in s.grid.cols" :key="c.key">{{ c.label }}</th></tr></thead>
            <tbody>
              <tr v-for="r in s.grid.rows" :key="r.key">
                <th>{{ r.label }}</th>
                <td v-for="c in s.grid.cols" :key="c.key">{{ has(`${r.key}_${c.key}`) ? fmtNum(model[`${r.key}_${c.key}`]) : '–' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>
    </template>

    <template v-else>
      <section v-for="s in schema" :key="s.key" class="sec">
        <h4>{{ s.title }}<span v-if="s.grid && isRequired(s.key)" class="req" title="Required"> *</span></h4>
        <div v-if="s.fields" class="fields">
          <div v-for="f in s.fields" :key="f.key" :class="['field', { wide: f.type === 'textarea' }]">
            <label :for="`md-${f.key}`">{{ f.label }}<span v-if="isRequired(f.key)" class="req" title="Required"> *</span>
              <span v-if="overrides(s, f.key)" class="src">
                <Tag value="overrides cohort" severity="warn" />
                <button type="button" class="reset" :title="`Use the cohort's value: ${fmtVal(f, inherited![f.key])}`" @click="set(f.key, inherited![f.key])">use cohort's</button>
              </span>
              <span v-else-if="fromCohort(s, f.key)" class="src muted">from cohort</span>
            </label>
            <InputText v-if="f.type === 'text'" :id="`md-${f.key}`" :model-value="model[f.key] ?? ''" :placeholder="f.placeholder" @update:model-value="v => set(f.key, v)" />
            <Textarea v-else-if="f.type === 'textarea'" :id="`md-${f.key}`" :model-value="model[f.key] ?? ''" :placeholder="f.placeholder" rows="2" auto-resize @update:model-value="v => set(f.key, v)" />
            <Select v-else-if="f.type === 'select'" :input-id="`md-${f.key}`" :model-value="model[f.key] ?? null" :options="f.options" show-clear placeholder="Not specified" @update:model-value="v => set(f.key, v)" />
            <DatePicker v-else-if="f.type === 'date'" :input-id="`md-${f.key}`" :model-value="dateValue(f.key)" date-format="yy-mm-dd" show-button-bar placeholder="YYYY-MM-DD" @update:model-value="(v: any) => set(f.key, toIso(v))" />
            <InputNumber v-else :input-id="`md-${f.key}`" :model-value="model[f.key] ?? null" :min="f.min" :max="f.max" :min-fraction-digits="0" :max-fraction-digits="f.type === 'integer' ? 0 : 10"
                         :use-grouping="f.type === 'integer'" :placeholder="f.placeholder" @update:model-value="v => set(f.key, v)" />
          </div>
        </div>
        <div v-else class="table-wrap">
          <table class="grid-t edit">
            <thead><tr><th /><th v-for="c in s.grid.cols" :key="c.key">{{ c.label }}</th></tr></thead>
            <tbody>
              <tr v-for="r in s.grid.rows" :key="r.key">
                <th>{{ r.label }}</th>
                <td v-for="c in s.grid.cols" :key="c.key">
                  <InputNumber :model-value="model[`${r.key}_${c.key}`] ?? null" :min="r.min" :max="r.max" :min-fraction-digits="0" :max-fraction-digits="3"
                               :aria-label="`${r.label}, ${c.label}`" size="small" fluid @update:model-value="v => set(`${r.key}_${c.key}`, v)" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.mf { display: flex; flex-direction: column; gap: 18px; }
.m0 { margin: 0; }
h4 { margin: 0 0 10px; font-size: 0.78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--gh-muted); }
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 0 16px; }
.field.wide { grid-column: 1 / -1; }
.field { margin-bottom: 12px; }
.kv { display: grid; grid-template-columns: minmax(160px, auto) 1fr; gap: 6px 16px; margin: 0; font-size: 0.88rem; }
.kv dt { color: var(--gh-muted); }
.kv dd { margin: 0; }
.kv dd.pre { white-space: pre-wrap; }
.table-wrap { overflow-x: auto; }
.grid-t { border-collapse: collapse; font-size: 0.88rem; }
.grid-t th, .grid-t td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--gh-border); }
.grid-t thead th { font-size: 0.75rem; color: var(--gh-muted); font-weight: 600; }
.grid-t tbody th { font-weight: 500; color: var(--gh-muted); white-space: nowrap; }
.grid-t.edit td { min-width: 120px; }
.req { color: #b91c1c; }
.src { font-size: 0.72rem; margin-left: 6px; font-weight: 400; }
.src :deep(.p-tag) { font-size: 0.68rem; padding: 0 5px; }
.reset { background: none; border: 0; color: var(--gh-accent); cursor: pointer; font: inherit; font-size: 0.72rem; padding: 0 0 0 4px; }
</style>
