<script setup lang="ts">
// GWASHub against the reference for one validation test (plot.json: {x, y, x_label, y_label}), with the line of
// identity. Points on the line mean the two agree.
const props = withDefaults(defineProps<{ data: any, size?: number }>(), { size: 300 })
const S = 260, PAD = 40
const pts = computed(() => (props.data?.x ?? []).map((x: number, i: number) => ({ x, y: props.data.y[i] })))
// Axes fitted to the data with a tidy step (1, 2 or 5 × 10^k): from 0 when every value is >= 0 (−log10 p), otherwise
// from the smallest value (β can be negative). Both axes share the range, so the line of identity is the diagonal.
const vals = computed(() => pts.value.flatMap((p: any) => [p.x, p.y]).filter((v: number) => Number.isFinite(v)))
const step = computed(() => {
  const v = vals.value
  const lo = Math.min(0, ...v), hi = Math.max(0, ...v)
  const raw = (hi - lo || 1) / 5
  const mag = 10 ** Math.floor(Math.log10(raw))
  return [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw)!
})
const lo = computed(() => Math.floor(Math.min(0, ...vals.value) / step.value) * step.value)
const hi = computed(() => Math.max(lo.value + step.value, Math.ceil(Math.max(0, ...vals.value) / step.value) * step.value))
const s = (v: number) => ((v - lo.value) / (hi.value - lo.value)) * (S - PAD - 10)
const sx = (x: number) => PAD + s(x)
const sy = (y: number) => S - PAD - s(y)
const ticks = computed(() => Array.from({ length: Math.round((hi.value - lo.value) / step.value) + 1 }, (_, i) => lo.value + i * step.value))
const decimals = computed(() => Math.max(0, -Math.floor(Math.log10(step.value))))
const label = (v: number) => (Math.abs(v) < step.value / 1e6 ? 0 : v).toFixed(decimals.value)
</script>

<template>
  <svg :viewBox="`0 0 ${S} ${S}`" class="plot" :style="{ maxWidth: `${size}px` }" role="img"
       :aria-label="`${data?.x_label} against ${data?.y_label}`">
    <line :x1="sx(lo)" :y1="sy(lo)" :x2="sx(hi)" :y2="sy(hi)" class="diag" />
    <circle v-for="(p, i) in pts" :key="i" :cx="sx(p.x)" :cy="sy(p.y)" r="1.8" class="pt" />
    <line :x1="PAD" :x2="PAD" :y1="10" :y2="S - PAD" class="axis" />
    <line :x1="PAD" :x2="S - 10" :y1="S - PAD" :y2="S - PAD" class="axis" />
    <text v-for="v in ticks" :key="'x' + v" :x="sx(v)" :y="S - PAD + 14" class="tick" text-anchor="middle">{{ label(v) }}</text>
    <text v-for="v in ticks" :key="'y' + v" :x="PAD - 6" :y="sy(v) + 3" class="tick" text-anchor="end">{{ label(v) }}</text>
    <text :x="(S + PAD) / 2" :y="S - 6" class="label" text-anchor="middle">{{ data?.x_label }}</text>
    <text :x="12" :y="(S - PAD) / 2" class="label" :transform="`rotate(-90 12 ${(S - PAD) / 2})`" text-anchor="middle">{{ data?.y_label }}</text>
  </svg>
</template>

<style scoped>
.plot { display: block; width: 100%; height: auto; aspect-ratio: 1; } /* fits its box, up to size px */
.diag { stroke: #94a3b8; stroke-dasharray: 4 3; stroke-width: 1; }
.pt { fill: var(--gh-accent); fill-opacity: 0.55; }
.axis { stroke: #64748b; stroke-width: 1; }
.tick { font-size: 9px; fill: #64748b; }
.label { font-size: 10px; fill: #334155; }
</style>
