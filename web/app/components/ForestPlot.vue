<script setup lang="ts">
// Forest plot: per-dataset effect ± 95% CI and the pooled estimate (diamond; absent for methods that give only Z).
const props = defineProps<{ locus: any }>()
const ROW = 24, LEFT = 170, RIGHT = 150, W = 600
const studies = computed(() => props.locus.studies ?? [])
const pooled = computed(() => props.locus.beta != null && props.locus.se != null)
const H = computed(() => (studies.value.length + 2) * ROW + 40)
const lo = (b: number, se: number) => b - 1.96 * se
const hi = (b: number, se: number) => b + 1.96 * se
const range = computed(() => {
  const all = [...studies.value.flatMap((s: any) => [lo(s.beta, s.se), hi(s.beta, s.se)]), ...(pooled.value ? [lo(props.locus.beta, props.locus.se), hi(props.locus.beta, props.locus.se)] : []), 0]
  const pad = (Math.max(...all) - Math.min(...all)) * 0.08 || 0.1
  return [Math.min(...all) - pad, Math.max(...all) + pad]
})
const sx = (v: number) => LEFT + ((v - range.value[0]) / (range.value[1] - range.value[0])) * (W - LEFT - RIGHT)
const maxW = computed(() => Math.max(...studies.value.map((s: any) => 1 / s.se ** 2)))
const box = (s: any) => 3 + 6 * Math.sqrt((1 / s.se ** 2) / maxW.value)
const yPooled = computed(() => (studies.value.length + 1) * ROW + 14)
const ticks = computed(() => {
  const [a, b] = range.value, step = Math.pow(10, Math.floor(Math.log10((b - a) / 4)))
  const n = (b - a) / step > 8 ? step * 2 : step
  const out = []
  for (let t = Math.ceil(a / n) * n; t <= b; t += n) out.push(Number(t.toFixed(6)))
  return out
})
const ci = (b: number, se: number) => `${b.toFixed(3)} [${lo(b, se).toFixed(3)}, ${hi(b, se).toFixed(3)}]`
</script>

<template>
  <svg :viewBox="`0 0 ${W} ${H}`" class="forest" role="img" :aria-label="`Forest plot for ${locus.rsid}`">
    <text x="0" y="14" class="hd">Dataset</text>
    <text :x="W" y="14" class="hd" text-anchor="end">BETA [95% CI]</text>
    <line :x1="sx(0)" :x2="sx(0)" y1="22" :y2="H - 26" class="zero" />
    <line v-if="pooled" :x1="sx(locus.beta)" :x2="sx(locus.beta)" y1="22" :y2="H - 26" class="pooled-line" />
    <g v-for="(s, i) in studies" :key="s.id" :transform="`translate(0, ${(i + 1) * ROW + 10})`">
      <text x="0" y="4" class="lbl">{{ s.label }}</text>
      <line :x1="sx(lo(s.beta, s.se))" :x2="sx(hi(s.beta, s.se))" y1="0" y2="0" class="ci" />
      <rect :x="sx(s.beta) - box(s)" :y="-box(s)" :width="2 * box(s)" :height="2 * box(s)" class="box" />
      <text :x="W" y="4" class="val" text-anchor="end">{{ ci(s.beta, s.se) }}</text>
    </g>
    <g :transform="`translate(0, ${yPooled})`">
      <text x="0" y="4" class="lbl strong">Meta-analysis</text>
      <polygon v-if="pooled" :points="`${sx(lo(locus.beta, locus.se))},0 ${sx(locus.beta)},-7 ${sx(hi(locus.beta, locus.se))},0 ${sx(locus.beta)},7`" class="diamond" />
      <text :x="W" y="4" class="val strong" text-anchor="end">{{ pooled ? ci(locus.beta, locus.se) : 'no pooled effect' }}</text>
    </g>
    <line :x1="LEFT" :x2="W - RIGHT" :y1="H - 26" :y2="H - 26" class="axis" />
    <g v-for="t in ticks" :key="t"><line :x1="sx(t)" :x2="sx(t)" :y1="H - 26" :y2="H - 22" class="axis" /><text :x="sx(t)" :y="H - 10" class="tick" text-anchor="middle">{{ t }}</text></g>
  </svg>
</template>

<style scoped>
.forest { width: 100%; height: auto; display: block; }
.hd { font-size: 10px; fill: #5b6b77; text-transform: uppercase; letter-spacing: .04em; }
.lbl { font-size: 12px; fill: #1d2a33; }
.val { font-size: 11.5px; fill: #1d2a33; font-family: ui-monospace, Menlo, monospace; }
.strong { font-weight: 700; }
.ci { stroke: #303880; stroke-width: 1.5; }
.box { fill: #303880; }
.diamond { fill: #c2410c; }
.zero { stroke: #9aa7b0; stroke-dasharray: 3 3; }
.pooled-line { stroke: #c2410c; stroke-dasharray: 2 4; opacity: .6; }
.axis { stroke: #9aa7b0; }
.tick { font-size: 10px; fill: #5b6b77; }
</style>
