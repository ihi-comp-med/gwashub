<script setup lang="ts">
// GWASHub against the reference for one validation test (plot.json: {x, y, x_label, y_label}), with the line of
// identity. Points on the line mean the two agree.
const props = withDefaults(defineProps<{ data: any, size?: number }>(), { size: 300 })
const S = 260, PAD = 40
const pts = computed(() => (props.data?.x ?? []).map((x: number, i: number) => ({ x, y: props.data.y[i] })))
const max = computed(() => Math.max(2, Math.ceil(Math.max(...pts.value.flatMap((p: any) => [p.x, p.y]), 0))))
const s = (v: number) => (v / max.value) * (S - PAD - 10)
const sx = (x: number) => PAD + s(x)
const sy = (y: number) => S - PAD - s(y)
const step = computed(() => { const m = max.value; return m > 100 ? 50 : m > 40 ? 20 : m > 16 ? 8 : m > 8 ? 4 : m > 4 ? 2 : 1 })
const ticks = computed(() => Array.from({ length: Math.floor(max.value / step.value) + 1 }, (_, i) => i * step.value))
</script>

<template>
  <svg :viewBox="`0 0 ${S} ${S}`" class="plot" :style="{ width: `${size}px`, height: `${size}px` }" role="img"
       :aria-label="`${data?.x_label} against ${data?.y_label}`">
    <line :x1="sx(0)" :y1="sy(0)" :x2="sx(max)" :y2="sy(max)" class="diag" />
    <circle v-for="(p, i) in pts" :key="i" :cx="sx(p.x)" :cy="sy(p.y)" r="1.8" class="pt" />
    <line :x1="PAD" :x2="PAD" :y1="10" :y2="S - PAD" class="axis" />
    <line :x1="PAD" :x2="S - 10" :y1="S - PAD" :y2="S - PAD" class="axis" />
    <text v-for="v in ticks" :key="'x' + v" :x="sx(v)" :y="S - PAD + 14" class="tick" text-anchor="middle">{{ v }}</text>
    <text v-for="v in ticks" :key="'y' + v" :x="PAD - 6" :y="sy(v) + 3" class="tick" text-anchor="end">{{ v }}</text>
    <text :x="(S + PAD) / 2" :y="S - 6" class="label" text-anchor="middle">{{ data?.x_label }}</text>
    <text :x="12" :y="(S - PAD) / 2" class="label" :transform="`rotate(-90 12 ${(S - PAD) / 2})`" text-anchor="middle">{{ data?.y_label }}</text>
  </svg>
</template>

<style scoped>
.plot { display: block; }
.diag { stroke: #94a3b8; stroke-dasharray: 4 3; stroke-width: 1; }
.pt { fill: var(--gh-accent); fill-opacity: 0.55; }
.axis { stroke: #64748b; stroke-width: 1; }
.tick { font-size: 9px; fill: #64748b; }
.label { font-size: 10px; fill: #334155; }
</style>
