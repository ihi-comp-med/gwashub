<script setup lang="ts">
// Manhattan plot. With `data` (plot_data.json "manhattan") it draws real results; without, a seeded
// synthetic example (landing page).
const props = withDefaults(defineProps<{ data?: any, gws?: number, seed?: number, highlight?: { chr: string, pos: number }[] }>(),
  { seed: 1, gws: 5e-8, highlight: () => [] })
const emit = defineEmits<{ select: [p: { chr: string, pos: number, logp: number }] }>()
const W = 900, H = 260, PAD = 36

function synthetic(seed: number) {
  let s = seed >>> 0
  const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32)
  const lens = [249, 243, 198, 191, 182, 171, 159, 146, 141, 136, 135, 133, 115, 107, 102, 90, 83, 80, 59, 64, 47, 51]
  const x: number[] = [], y: number[] = [], chr: number[] = [], pos: number[] = [], ticks: any[] = []
  let off = 0
  lens.forEach((len, i) => {
    ticks.push({ chr: String(i + 1), x: off + len / 2 })
    for (let k = 0; k < len * 1.6; k++) {
      const p = r() * len
      let lp = -Math.log10(Math.max(r(), 1e-12)) * 0.9
      if ([2, 6, 10, 16].includes(i + 1) && Math.abs(p - len * 0.45) < 4) lp += (4 - Math.abs(p - len * 0.45)) * (2 + r() * 2.2)
      x.push(off + p); y.push(Math.min(lp, 20)); chr.push(i + 1); pos.push(Math.round(p * 1e6))
    }
    off += len
  })
  return { x, y, chr, pos, ticks, x_max: off }
}

const d = computed(() => props.data ?? synthetic(props.seed))
const yMax = computed(() => Math.max(10, Math.ceil(Math.max(...d.value.y, 0) * 1.05)))
const sx = (x: number) => PAD + (x / d.value.x_max) * (W - PAD - 8)
const sy = (y: number) => H - PAD - (y / yMax.value) * (H - PAD - 10)
const yTicks = computed(() => { const step = yMax.value > 60 ? 20 : yMax.value > 25 ? 10 : 5; return Array.from({ length: Math.floor(yMax.value / step) + 1 }, (_, i) => i * step) })
const points = computed(() => d.value.x.map((x: number, i: number) => ({ x: sx(x), y: sy(d.value.y[i]), c: d.value.chr[i] % 2, i })))
const hover = ref<number | null>(null)
const hovered = computed(() => hover.value == null ? null : { chr: String(d.value.chr[hover.value] === 23 ? 'X' : d.value.chr[hover.value]), pos: d.value.pos[hover.value], logp: d.value.y[hover.value] })
function onMove(e: MouseEvent) {
  const svg = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
  const mx = (e.clientX - svg.left) * (W / svg.width), my = (e.clientY - svg.top) * (H / svg.height)
  let best = -1, bd = 64
  for (const p of points.value) {
    const dd = (p.x - mx) ** 2 + (p.y - my) ** 2
    if (dd < bd && d.value.y[p.i] > 3) { bd = dd; best = p.i }
  }
  hover.value = best >= 0 ? best : null
}
</script>

<template>
  <div class="wrap">
    <svg :viewBox="`0 0 ${W} ${H}`" class="plot" role="img" aria-label="Manhattan plot" @mousemove="onMove" @mouseleave="hover = null"
         @click="hovered && emit('select', hovered)">
      <line :x1="PAD" :x2="W - 8" :y1="sy(-Math.log10(gws))" :y2="sy(-Math.log10(gws))" class="gws" />
      <circle v-for="p in points" :key="p.i" :cx="p.x" :cy="p.y" r="1.8" :class="p.c ? 'c1' : 'c0'" />
      <circle v-if="hover != null" :cx="sx(d.x[hover])" :cy="sy(d.y[hover])" r="4.5" class="hl" />
      <line :x1="PAD" :x2="PAD" :y1="10" :y2="H - PAD" class="axis" />
      <line :x1="PAD" :x2="W - 8" :y1="H - PAD" :y2="H - PAD" class="axis" />
      <text v-for="t in d.ticks" :key="t.chr" :x="sx(t.x)" :y="H - PAD + 14" class="tick" text-anchor="middle">{{ t.chr }}</text>
      <text v-for="y in yTicks" :key="y" :x="PAD - 6" :y="sy(y) + 3" class="tick" text-anchor="end">{{ y }}</text>
      <text :x="12" :y="H / 2" class="label" :transform="`rotate(-90 12 ${H / 2})`" text-anchor="middle">−log10(P)</text>
      <text :x="(W + PAD) / 2" :y="H - 4" class="label" text-anchor="middle">Chromosome</text>
    </svg>
    <div v-if="hovered" class="tip mono">chr{{ hovered.chr }}:{{ hovered.pos.toLocaleString() }} · P = 10<sup>−{{ hovered.logp.toFixed(1) }}</sup></div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.plot { width: 100%; height: auto; display: block; cursor: crosshair; }
.c0 { fill: #303880; opacity: 0.75; }
.c1 { fill: #8b95cc; opacity: 0.75; }
.hl { fill: none; stroke: #c2410c; stroke-width: 2; }
.gws { stroke: #c2410c; stroke-dasharray: 4 3; stroke-width: 1; }
.axis { stroke: #9aa7b0; }
.tick { font-size: 9px; fill: #5b6b77; }
.label { font-size: 11px; fill: #1d2a33; }
.tip { position: absolute; top: 4px; right: 8px; background: rgba(255, 255, 255, .92); border: 1px solid var(--gh-border); border-radius: 6px; padding: 3px 8px; font-size: 0.75rem; }
</style>
