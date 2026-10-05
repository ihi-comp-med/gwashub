<script setup lang="ts">
// QQ plot from plot_data.json "qq" ({expected, observed, lambda}); synthetic when no data.
// `size` is the plot's height (and width) in pixels, to match a Manhattan plot of the same height.
const props = withDefaults(defineProps<{ data?: any, seed?: number, lambda?: number, size?: number }>(), { seed: 1, lambda: 1.04, size: 280 })
const S = 260, PAD = 36

const pts = computed(() => {
  if (props.data) return props.data.expected.map((x: number, i: number) => ({ x, y: props.data.observed[i] }))
  let s = props.seed >>> 0
  const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32)
  return Array.from({ length: 400 }, (_, i) => {
    const x = -Math.log10((i + 0.5) / 400) * 2.4
    return { x, y: Math.max(0, x * props.lambda + (x > 4 ? (x - 4) ** 1.6 * 0.5 : 0) + (r() - 0.5) * 0.15) }
  })
})
const lam = computed(() => props.data?.lambda ?? props.lambda)
// Independent axes: with strong signals the observed maximum dwarfs the expected one.
const xMax = computed(() => Math.max(2, Math.ceil(Math.max(...pts.value.map((p: any) => p.x), 0))))
const yMax = computed(() => Math.max(xMax.value, Math.ceil(Math.max(...pts.value.map((p: any) => p.y), 0))))
const sx = (x: number) => PAD + (x / xMax.value) * (S - PAD - 8)
const sy = (y: number) => S - PAD - (y / yMax.value) * (S - PAD - 10)
const stepFor = (m: number) => m > 100 ? 50 : m > 40 ? 20 : m > 16 ? 8 : m > 8 ? 4 : m > 4 ? 2 : 1
const xTicks = computed(() => Array.from({ length: Math.floor(xMax.value / stepFor(xMax.value)) + 1 }, (_, i) => i * stepFor(xMax.value)))
const yTicks = computed(() => Array.from({ length: Math.floor(yMax.value / stepFor(yMax.value)) + 1 }, (_, i) => i * stepFor(yMax.value)))
const diag = computed(() => Math.min(xMax.value, yMax.value))
</script>

<template>
  <svg :viewBox="`0 0 ${S} ${S}`" class="plot" :style="{ width: `${size}px`, height: `${size}px` }" role="img" aria-label="QQ plot">
    <line :x1="sx(0)" :y1="sy(0)" :x2="sx(diag)" :y2="sy(diag)" class="diag" />
    <circle v-for="(p, i) in pts" :key="i" :cx="sx(p.x)" :cy="sy(p.y)" r="1.8" class="pt" />
    <line :x1="PAD" :x2="PAD" :y1="10" :y2="S - PAD" class="axis" />
    <line :x1="PAD" :x2="S - 8" :y1="S - PAD" :y2="S - PAD" class="axis" />
    <text v-for="v in xTicks" :key="'x' + v" :x="sx(v)" :y="S - PAD + 14" class="tick" text-anchor="middle">{{ v }}</text>
    <text v-for="v in yTicks" :key="'y' + v" :x="PAD - 6" :y="sy(v) + 3" class="tick" text-anchor="end">{{ v }}</text>
    <text :x="(S + PAD) / 2" :y="S - 4" class="label" text-anchor="middle">Expected −log10(P)</text>
    <text :x="12" :y="S / 2" class="label" :transform="`rotate(-90 12 ${S / 2})`" text-anchor="middle">Observed</text>
    <text v-if="lam != null" :x="PAD + 8" :y="22" class="label">λGC = {{ Number(lam).toFixed(3) }}</text>
  </svg>
</template>

<style scoped>
.plot { max-width: 100%; display: block; margin: 0 auto; }
.pt { fill: #303880; opacity: 0.7; }
.diag { stroke: #c2410c; stroke-dasharray: 4 3; }
.axis { stroke: #9aa7b0; }
.tick { font-size: 9px; fill: #5b6b77; }
.label { font-size: 11px; fill: #1d2a33; }
</style>
