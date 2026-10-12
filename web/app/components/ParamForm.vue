<script setup lang="ts">
// A form drawn from typed parameters (params: in step.yaml / analysis.yaml; see pipeline/runner/params.py).
// v-model is {key: value}; keys left out use the field's default.
export interface ParamField {
  key: string, label: string, type: 'select' | 'boolean' | 'number' | 'integer' | 'text' | 'range'
  default?: any, min?: number, max?: number, options?: any[], help?: string, sides?: ('min' | 'max')[]
}
// A range: the filter's own switch, and each limit (min / max) switched on or off with its value kept.
type Range = { on: boolean, min_on: boolean, min: number | null, max_on: boolean, max: number | null }
const props = withDefaults(defineProps<{ schema: ParamField[], modelValue: Record<string, any>, disabled?: boolean, compact?: boolean, idPrefix?: string }>(),
  { disabled: false, compact: false, idPrefix: 'p' })
const emit = defineEmits<{ 'update:modelValue': [Record<string, any>] }>()
const value = (f: ParamField) => props.modelValue?.[f.key] ?? f.default
function set(f: ParamField, v: any) { emit('update:modelValue', { ...props.modelValue, [f.key]: v }) }
const changed = (f: ParamField) => props.modelValue?.[f.key] != null && props.modelValue[f.key] !== f.default
const inputs = computed(() => props.schema.filter(f => !['boolean', 'range'].includes(f.type)))
const ranges = computed(() => props.schema.filter(f => f.type === 'range'))
const toggles = computed(() => props.schema.filter(f => f.type === 'boolean'))
const rangeOf = (f: ParamField): Range => ({ ...f.default, ...(props.modelValue?.[f.key] ?? {}) })
function setRange(f: ParamField, part: keyof Range, v: any) { set(f, { ...rangeOf(f), [part]: v }) }
const sideLabel = { min: 'Min', max: 'Max' } as const
const id = (f: ParamField) => `${props.idPrefix}-${f.key}`
// Decimal parameters and range limits are typed as text so scientific notation works ("5e-20" for a P threshold), as
// in MetadataForm. While a box is being typed in, its text is kept as typed; anything that isn't a number is sent as
// it is, and the server names the field.
const drafts = reactive<Record<string, string>>({})
const isNum = (s: string) => s.trim() !== '' && Number.isFinite(Number(s.trim()))
const parsed = (s: string) => s.trim() === '' ? null : isNum(s) ? Number(s.trim()) : s.trim()
const numText = (k: string, v: any) => drafts[k] ?? (v === null || v === undefined ? '' : String(v))
const endDraft = (k: string) => { delete drafts[k] }
function setNum(f: ParamField, s: string) { drafts[f.key] = s; set(f, parsed(s)) }
function setLimit(f: ParamField, sd: 'min' | 'max', s: string) { drafts[`${f.key}.${sd}`] = s; setRange(f, sd, parsed(s)) }
</script>

<template>
  <div class="pf" :class="{ compact }">
    <div v-if="inputs.length" class="pf-grid">
      <div v-for="f in inputs" :key="f.key" class="field">
        <label :for="id(f)">{{ f.label }} <i v-if="f.help" v-tooltip.top="f.help" class="pi pi-info-circle info" :aria-label="f.help" /></label>
        <Select v-if="f.type === 'select'" :input-id="id(f)" :model-value="value(f)" :options="f.options" :disabled="disabled" size="small" @update:model-value="v => set(f, v)" />
        <InputNumber v-else-if="f.type === 'integer'" :input-id="id(f)" :model-value="value(f)" :min="f.min" :max="f.max" :use-grouping="false" :disabled="disabled"
                     size="small" show-buttons @update:model-value="v => set(f, v)" />
        <InputText v-else-if="f.type === 'number'" :id="id(f)" inputmode="decimal" :model-value="numText(f.key, value(f))" :disabled="disabled" size="small"
                   @update:model-value="v => setNum(f, v ?? '')" @blur="endDraft(f.key)" />
        <InputText v-else :id="id(f)" :model-value="value(f)" :disabled="disabled" size="small" @update:model-value="v => set(f, v)" />
        <small v-if="changed(f)" class="muted">Default {{ f.default }}</small>
      </div>
    </div>
    <div v-if="ranges.length" class="ranges">
      <div v-for="f in ranges" :key="f.key" class="range" :class="{ off: !rangeOf(f).on }">
        <div class="toggle">
          <ToggleSwitch :model-value="!!rangeOf(f).on" :input-id="id(f)" :disabled="disabled" @update:model-value="v => setRange(f, 'on', v)" />
          <label :for="id(f)">{{ f.label }}</label>
          <i v-if="f.help" v-tooltip.top="f.help" class="pi pi-info-circle info" :aria-label="f.help" />
        </div>
        <template v-for="sd in (['min', 'max'] as const)" :key="sd">
          <div v-if="!(f.sides ?? ['min', 'max']).includes(sd)" class="limit" aria-hidden="true" />
          <div v-else class="limit">
            <Checkbox :model-value="!!rangeOf(f)[`${sd}_on`]" binary :input-id="`${id(f)}-${sd}`" :disabled="disabled || !rangeOf(f).on"
                      @update:model-value="v => setRange(f, `${sd}_on`, v)" />
            <label :for="`${id(f)}-${sd}`" class="muted small">{{ sideLabel[sd] }}</label>
            <InputText :model-value="numText(`${f.key}.${sd}`, rangeOf(f)[sd])" inputmode="decimal"
                       :disabled="disabled || !rangeOf(f).on || !rangeOf(f)[`${sd}_on`]" size="small" :aria-label="`${f.label} ${sideLabel[sd].toLowerCase()}`"
                       class="limit-input" @update:model-value="v => setLimit(f, sd, v ?? '')" @blur="endDraft(`${f.key}.${sd}`)" />
          </div>
        </template>
      </div>
    </div>
    <div v-for="f in toggles" :key="f.key" class="toggle">
      <ToggleSwitch :model-value="!!value(f)" :input-id="id(f)" :disabled="disabled" @update:model-value="v => set(f, v)" />
      <label :for="id(f)">{{ f.label }}</label>
      <i v-if="f.help" v-tooltip.top="f.help" class="pi pi-info-circle info" :aria-label="f.help" />
    </div>
  </div>
</template>

<style scoped>
.pf { display: flex; flex-direction: column; gap: 10px; }
.pf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 20px; }
.compact .pf-grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.pf-grid .field { margin-bottom: 6px; }
.pf-grid :deep(.p-inputnumber), .pf-grid :deep(.p-select), .pf-grid :deep(input) { width: 100%; }
.toggle { display: flex; align-items: center; gap: 10px; font-size: 0.9rem; }
.toggle :deep(.p-toggleswitch) { flex: 0 0 auto; }
.toggle label { cursor: pointer; }
.info { color: var(--p-text-muted-color); cursor: help; font-size: 0.85em; }
/* compact (e.g. inside the pipeline page's step contract): the same size as the text around it */
.compact .toggle, .compact label, .compact small { font-size: inherit; }
.compact :deep(.p-inputtext), .compact :deep(.p-select-label), .compact :deep(.p-inputnumber-input) { font-size: inherit; padding-top: 4px; padding-bottom: 4px; }
.ranges { display: flex; flex-direction: column; gap: 6px; }
.range { display: grid; grid-template-columns: minmax(180px, 1fr) auto auto; gap: 4px 18px; align-items: center; }
.range.off .limit { opacity: 0.5; }
.limit { display: flex; align-items: center; gap: 6px; }
.limit-input { width: 7rem; }
.toggle .info { margin-left: -4px; }
</style>
