<script setup lang="ts">
// A form drawn from typed parameters (params: in step.yaml / analysis.yaml; see pipeline/runner/params.py).
// v-model is {key: value}; keys left out use the field's default.
export interface ParamField {
  key: string, label: string, type: 'select' | 'boolean' | 'number' | 'integer' | 'text'
  default?: any, min?: number, max?: number, options?: any[], help?: string
}
const props = withDefaults(defineProps<{ schema: ParamField[], modelValue: Record<string, any>, disabled?: boolean, compact?: boolean, idPrefix?: string }>(),
  { disabled: false, compact: false, idPrefix: 'p' })
const emit = defineEmits<{ 'update:modelValue': [Record<string, any>] }>()
const value = (f: ParamField) => props.modelValue?.[f.key] ?? f.default
function set(f: ParamField, v: any) { emit('update:modelValue', { ...props.modelValue, [f.key]: v }) }
const changed = (f: ParamField) => props.modelValue?.[f.key] != null && props.modelValue[f.key] !== f.default
const inputs = computed(() => props.schema.filter(f => f.type !== 'boolean'))
const toggles = computed(() => props.schema.filter(f => f.type === 'boolean'))
const small = (v: any) => typeof v === 'number' && v !== 0 && Math.abs(v) < 1e-3
const id = (f: ParamField) => `${props.idPrefix}-${f.key}`
</script>

<template>
  <div class="pf" :class="{ compact }">
    <div v-if="inputs.length" class="pf-grid">
      <div v-for="f in inputs" :key="f.key" class="field">
        <label :for="id(f)">{{ f.label }}</label>
        <Select v-if="f.type === 'select'" :input-id="id(f)" :model-value="value(f)" :options="f.options" :disabled="disabled" size="small" @update:model-value="v => set(f, v)" />
        <InputNumber v-else-if="f.type === 'integer'" :input-id="id(f)" :model-value="value(f)" :min="f.min" :max="f.max" :use-grouping="false" :disabled="disabled"
                     size="small" show-buttons @update:model-value="v => set(f, v)" />
        <InputNumber v-else-if="f.type === 'number'" :input-id="id(f)" :model-value="value(f)" :min="f.min" :max="f.max" :min-fraction-digits="0" :max-fraction-digits="12"
                     :use-grouping="false" :disabled="disabled" size="small" @update:model-value="v => set(f, v)" />
        <InputText v-else :id="id(f)" :model-value="value(f)" :disabled="disabled" size="small" @update:model-value="v => set(f, v)" />
        <small v-if="f.help || small(value(f)) || changed(f)" class="muted">
          <span v-if="small(value(f))">{{ Number(value(f)).toExponential(0) }}. </span>{{ f.help }}<span v-if="changed(f)"> (default {{ f.default }})</span>
        </small>
      </div>
    </div>
    <div v-for="f in toggles" :key="f.key" class="toggle">
      <ToggleSwitch :model-value="!!value(f)" :input-id="id(f)" :disabled="disabled" @update:model-value="v => set(f, v)" />
      <div>
        <label :for="id(f)">{{ f.label }}</label>
        <small v-if="f.help" class="muted help">{{ f.help }}</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pf { display: flex; flex-direction: column; gap: 10px; }
.pf-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 4px 20px; }
.compact .pf-grid { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
.pf-grid .field { margin-bottom: 6px; }
.pf-grid :deep(.p-inputnumber), .pf-grid :deep(.p-select), .pf-grid :deep(input) { width: 100%; }
.toggle { display: flex; align-items: center; gap: 10px; font-size: 0.9rem; }
.toggle :deep(.p-toggleswitch) { flex: 0 0 auto; }
.toggle small { font-size: 0.78rem; }
.toggle .help { display: block; line-height: 1.3; }
.toggle label { cursor: pointer; }
</style>
