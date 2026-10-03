<script setup lang="ts">
// Study metadata (HERMES data-registry fields). Edit mode renders inputs; readonly renders a compact summary
// that hides empty fields.
const props = defineProps<{ readonly?: boolean }>()
const model = defineModel<Record<string, any>>({ default: () => ({}) })
const { schema } = useMetadataSchema()

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
              <template v-if="has(f.key)"><dt>{{ f.label }}</dt><dd :class="{ pre: f.type === 'textarea' }">{{ fmtVal(f, model[f.key]) }}</dd></template>
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
        <h4>{{ s.title }}</h4>
        <div v-if="s.fields" class="fields">
          <div v-for="f in s.fields" :key="f.key" :class="['field', { wide: f.type === 'textarea' }]">
            <label :for="`md-${f.key}`">{{ f.label }}</label>
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
</style>
