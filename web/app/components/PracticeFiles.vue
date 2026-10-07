<script setup lang="ts">
// The Practice project's synthetic files, with what to enter for each when uploading (practice/files.json).
const { data: files } = useApiGet<any[]>('/practice/files')
const FIELDS: [string, string][] = [['chr', 'Chromosome'], ['pos', 'Position'], ['ea', 'Effect allele'], ['oa', 'Other allele'],
  ['beta', 'Effect'], ['se', 'SE'], ['eaf', 'EAF'], ['p', 'P'], ['n', 'N'], ['rsid', 'rsID'], ['info', 'INFO']]
</script>

<template>
  <Card data-tour="practice-files">
    <template #title>Practice files</template>
    <template #subtitle>Synthetic summary statistics to practise with. Download one, then upload it (Help menu → Upload a dataset), entering the details below.
      QC warns about them on purpose: they don't match real reference genomes.</template>
    <template #content>
      <div v-for="f in files ?? []" :key="f.file" class="pf">
        <div class="row head">
          <a :href="`/api/practice/files/${f.file}`" download class="mono strong"><i class="pi pi-download" /> {{ f.file }}</a>
          <span class="muted small">{{ f.style }} format</span>
        </div>
        <div class="small kv">
          <span><span class="muted">Cohort</span> {{ f.cohort }}</span>
          <span><span class="muted">Phenotype</span> {{ f.phenotype }}</span>
          <span><span class="muted">Ancestry</span> {{ f.ancestry }}</span>
          <span><span class="muted">Sex</span> {{ f.sex }}</span>
          <span><span class="muted">Build</span> {{ f.build }}</span>
          <span><span class="muted">Effect</span> {{ f.effect_type }}</span>
          <span><span class="muted">Cases / controls</span> {{ f.n_cases.toLocaleString() }} / {{ f.n_controls.toLocaleString() }}</span>
        </div>
        <div class="small cols">
          <span v-for="[k, label] in FIELDS.filter(([k]) => f.columns[k])" :key="k"><span class="muted">{{ label }}</span> <code>{{ f.columns[k] }}</code></span>
        </div>
      </div>
    </template>
  </Card>
</template>

<style scoped>
.pf { padding: 10px 0; border-top: 1px solid var(--gh-border); }
.pf:first-child { border-top: 0; padding-top: 0; }
.head { gap: 10px; margin-bottom: 4px; }
.head a { text-decoration: none; }
.kv, .cols { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 2px; }
.cols code { font-size: 0.78rem; }
</style>
