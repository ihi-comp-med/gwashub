<script setup lang="ts">
// Locus plot (LocusZoom-style) from a meta-analysis's region_<locus_id>.json (_metalib/regions.py): every variant in the
// locus by position and -log10(P), coloured by r² with the locus lead in the 1000 Genomes panel; the lead as a diamond,
// the other independent signals outlined, the locus bounds shaded; GENCODE genes underneath.
const props = defineProps<{ data: any }>()
const W = 900, PAD_L = 44, PAD_R = 12, TOP = 12, PLOT_H = 230, AXIS = 30, ROW = 26, MAX_ROWS = 6

const d = computed(() => props.data)
const sx = (pos: number) => PAD_L + ((pos - d.value.start) / Math.max(1, d.value.end - d.value.start)) * (W - PAD_L - PAD_R)
const yMax = computed(() => Math.max(10, Math.ceil(Math.max(...d.value.points.logp, d.value.lead.logp, 0) * 1.08)))
const sy = (y: number) => TOP + PLOT_H - (y / yMax.value) * PLOT_H
const yTicks = computed(() => { const m = yMax.value, step = m > 60 ? 20 : m > 25 ? 10 : 5; return Array.from({ length: Math.floor(m / step) + 1 }, (_, i) => i * step) })
const xTicks = computed(() => {
  const span = d.value.end - d.value.start, step = [1e4, 2.5e4, 5e4, 1e5, 2.5e5, 5e5, 1e6].find(s => span / s <= 8) ?? 2e6
  const first = Math.ceil(d.value.start / step) * step
  return Array.from({ length: Math.floor((d.value.end - first) / step) + 1 }, (_, i) => first + i * step)
})

// r² bins, as LocusZoom.
const BINS = [{ min: 0.8, c: '#d43f3a', l: '0.8–1' }, { min: 0.6, c: '#eea236', l: '0.6–0.8' }, { min: 0.4, c: '#5cb85c', l: '0.4–0.6' },
  { min: 0.2, c: '#46b8da', l: '0.2–0.4' }, { min: 0, c: '#357ebd', l: '0–0.2' }]
const colour = (r2: number | null) => r2 == null ? '#b8bec4' : BINS.find(b => r2 >= b.min)!.c
const signals = computed(() => new Set(d.value.signals))
const points = computed(() => d.value.points.pos.map((pos: number, i: number) => ({
  pos, x: sx(pos), y: sy(d.value.points.logp[i]), r2: d.value.points.r2[i], rsid: d.value.points.rsid?.[i] ?? '', logp: d.value.points.logp[i],
  signal: signals.value.has(pos), lead: pos === d.value.lead.pos,
})).sort((a: any, b: any) => (a.r2 ?? -1) - (b.r2 ?? -1)))  // high r² drawn last, on top

// Genes packed into rows so labels don't overlap; protein-coding first when there are more than fit.
const genes = computed(() => {
  const rows: number[] = [], out: any[] = []
  const list = [...(d.value.genes ?? [])].sort((a, b) => a.start - b.start)
  for (const g of list) {
    const x1 = Math.max(PAD_L, sx(g.start)), x2 = Math.min(W - PAD_R, sx(g.end))
    const labelEnd = Math.max(x2, (x1 + x2) / 2 + g.name.length * 3.2) + 6
    let row = rows.findIndex(end => end < Math.min(x1, (x1 + x2) / 2 - g.name.length * 3.2))
    if (row === -1) { if (rows.length >= MAX_ROWS) continue; rows.push(0); row = rows.length - 1 }
    rows[row] = labelEnd
    out.push({ ...g, x1, x2, row, pc: g.type === 'protein_coding',
      exons: g.exons.map((e: number[]) => [Math.max(x1, sx(e[0])), Math.min(x2, sx(e[1]))]).filter((e: number[]) => e[1] > e[0]) })
  }
  return { list: out, rows: Math.max(rows.length, d.value.genes?.length ? 1 : 0), hidden: list.length - out.length }
})
const geneTop = TOP + PLOT_H + AXIS
const H = computed(() => geneTop + genes.value.rows * ROW + (genes.value.rows ? 8 : 0))

const hover = ref<any>(null)
function onMove(e: MouseEvent) {
  const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
  const mx = (e.clientX - box.left) * (W / box.width), my = (e.clientY - box.top) * (H.value / box.height)
  let best: any = null, bd = 80
  for (const p of points.value) { const dd = (p.x - mx) ** 2 + (p.y - my) ** 2; if (dd < bd) { bd = dd; best = p } }
  hover.value = best
}
const fmtR2 = (v: number | null) => v == null ? 'not in panel' : v.toFixed(2)
</script>

<template>
  <div class="wrap">
    <svg :viewBox="`0 0 ${W} ${H}`" class="plot" role="img" :aria-label="`Locus plot, chromosome ${d.chr}`" @mousemove="onMove" @mouseleave="hover = null">
      <rect :x="sx(d.locus_start)" :y="TOP" :width="Math.max(1, sx(d.locus_end) - sx(d.locus_start))" :height="PLOT_H" class="locus" />
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="sy(-Math.log10(d.gws))" :y2="sy(-Math.log10(d.gws))" class="gws" />
      <template v-for="p in points" :key="p.pos">
        <path v-if="p.lead" :d="`M ${p.x} ${p.y - 6} L ${p.x + 6} ${p.y} L ${p.x} ${p.y + 6} L ${p.x - 6} ${p.y} Z`" class="lead" />
        <circle v-else :cx="p.x" :cy="p.y" :r="p.signal ? 4 : 2.6" :fill="colour(p.r2)" :class="{ sig: p.signal }" />
      </template>
      <line :x1="PAD_L" :x2="PAD_L" :y1="TOP" :y2="TOP + PLOT_H" class="axis" />
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="TOP + PLOT_H" :y2="TOP + PLOT_H" class="axis" />
      <text v-for="y in yTicks" :key="'y' + y" :x="PAD_L - 6" :y="sy(y) + 3" class="tick" text-anchor="end">{{ y }}</text>
      <text v-for="x in xTicks" :key="'x' + x" :x="sx(x)" :y="TOP + PLOT_H + 13" class="tick" text-anchor="middle">{{ (x / 1e6).toFixed(2) }}</text>
      <text :x="(W + PAD_L) / 2" :y="TOP + PLOT_H + 25" class="label" text-anchor="middle">Position on chromosome {{ d.chr }} (Mb, GRCh38)</text>
      <text :x="12" :y="TOP + PLOT_H / 2" class="label" :transform="`rotate(-90 12 ${TOP + PLOT_H / 2})`" text-anchor="middle">−log10(P)</text>
      <g v-for="g in genes.list" :key="g.name + g.start" :class="['gene', { pc: g.pc }]">
        <line :x1="g.x1" :x2="g.x2" :y1="geneTop + g.row * ROW + 6" :y2="geneTop + g.row * ROW + 6" class="body" />
        <rect v-for="(e, i) in g.exons" :key="i" :x="e[0]" :y="geneTop + g.row * ROW + 2" :width="Math.max(1, e[1] - e[0])" height="8" class="exon" />
        <text :x="(g.x1 + g.x2) / 2" :y="geneTop + g.row * ROW + 20" class="gname" text-anchor="middle">{{ g.strand === '-' ? '← ' : '' }}{{ g.name }}{{ g.strand === '+' ? ' →' : '' }}</text>
      </g>
    </svg>
    <div class="legend">
      <span class="muted">r² with {{ d.lead.rsid || 'the lead' }}<template v-if="d.ld_panel"> ({{ d.ld_panel }})</template>:</span>
      <span v-for="b in [...BINS].reverse()" :key="b.l" class="key"><i :style="{ background: b.c }" />{{ b.l }}</span>
      <span class="key"><i style="background: #b8bec4" />not in panel</span>
      <span class="key"><i class="dia" />lead</span>
      <span class="key"><i class="ring" />independent signal</span>
      <span v-if="genes.hidden" class="muted">{{ genes.hidden }} more genes not shown</span>
      <span v-if="!d.genes?.length" class="muted">No gene track (genes not uploaded yet)</span>
    </div>
    <div v-if="hover" class="tip mono">
      <template v-if="hover.rsid">{{ hover.rsid }} · </template>chr{{ d.chr }}:{{ hover.pos.toLocaleString() }} · P = 10<sup>−{{ hover.logp.toFixed(1) }}</sup> · r² {{ fmtR2(hover.r2) }}
    </div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.plot { width: 100%; height: auto; display: block; }
.locus { fill: #303880; opacity: 0.06; }
.gws { stroke: #c2410c; stroke-dasharray: 4 3; stroke-width: 1; }
.axis { stroke: #9aa7b0; }
.tick { font-size: 9px; fill: #5b6b77; }
.label { font-size: 10px; fill: #1d2a33; }
.sig { stroke: #1d2a33; stroke-width: 1.2; }
.lead { fill: #7b3294; stroke: #fff; stroke-width: 1; }
.gene .body { stroke: #9aa7b0; stroke-width: 1; }
.gene .exon { fill: #9aa7b0; }
.gene.pc .body { stroke: #303880; }
.gene.pc .exon { fill: #303880; }
.gname { font-size: 9px; fill: #5b6b77; font-style: italic; }
.gene.pc .gname { fill: #1d2a33; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 0.72rem; margin-top: 4px; align-items: center; }
.key { display: inline-flex; align-items: center; gap: 4px; }
.key i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.key i.dia { background: #7b3294; border-radius: 0; transform: rotate(45deg); width: 8px; height: 8px; }
.key i.ring { border: 1.5px solid #1d2a33; background: transparent; }
.tip { position: absolute; top: 4px; right: 8px; background: rgba(255, 255, 255, .92); border: 1px solid var(--gh-border); border-radius: 6px; padding: 3px 8px; font-size: 0.75rem; }
</style>
