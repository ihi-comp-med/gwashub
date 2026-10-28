<script setup lang="ts">
// Served from public/; bound so Vite doesn't try to bundle them. The CVDKP logo is white, so it sits on a navy tile.
const logoSrc = '/brand/hermes-logo-full.png'
const cvdkpSrc = '/brand/cvdkp_header_logo.svg'
const features = [
  { icon: 'pi pi-cloud-upload', title: 'Upload', text: 'Gzipped or plain TSV, CSV or TXT, uploaded directly to secure storage.' },
  { icon: 'pi pi-sliders-h', title: 'Map columns', text: 'Column names are matched to a standard schema; you confirm the mapping.' },
  { icon: 'pi pi-check-circle', title: 'Standard QC', text: 'The HERMES 3 pipeline: parsing and filtering, harmonisation to the reference genome with gwas2vcf, liftover to GRCh38, LD score regression and allele-frequency checks.' },
  { icon: 'pi pi-file', title: 'Reports', text: 'Per-step results, λGC, Manhattan and QQ plots, and the cleaned file to download.' },
  { icon: 'pi pi-chart-bar', title: 'Meta-analysis', text: 'Fixed- or random-effects meta-analysis of selected datasets, with a forest plot for each locus.' },
]
</script>

<template>
  <section class="hero">
    <div class="container hero-grid">
      <div>
        <h1>Upload, QC and meta-analyse GWAS summary statistics.</h1>
        <p class="lead">Every upload goes through the project's versioned QC pipeline. You get a cleaned GRCh38 file and a report of every check, and reviewers can meta-analyse the datasets that pass.</p>
        <div class="row">
          <Button label="Get started" icon="pi pi-arrow-right" icon-pos="right" @click="navigateTo('/login')" />
          <Button label="About" severity="secondary" outlined @click="navigateTo('/about')" />
        </div>
      </div>
      <div class="consortium">
        <span class="by">In collaboration with</span>
        <a href="https://hermesconsortium.org" target="_blank" rel="noopener">
          <img :src="logoSrc" width="320" height="121"
               alt="HERMES: Heart Failure Molecular Epidemiology for Therapeutic Targets">
        </a>
        <a class="cvdkp" href="https://cvd.hugeamp.org/" target="_blank" rel="noopener">
          <img :src="cvdkpSrc" alt="Cardiovascular Disease Knowledge Portal">
        </a>
      </div>
    </div>
  </section>
  <section class="container page">
    <div class="grid grid-3">
      <Card v-for="f in features" :key="f.title">
        <template #title><div class="row"><i :class="f.icon" class="feat-icon" />{{ f.title }}</div></template>
        <template #content><p class="muted m0">{{ f.text }}</p></template>
      </Card>
    </div>
  </section>
</template>

<style scoped>
.hero { background: linear-gradient(180deg, #eef0f8 0%, var(--gh-bg) 100%); padding: 64px 0 40px; }
.hero-grid { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 40px; align-items: center; }
.consortium { display: flex; flex-direction: column; gap: 10px; width: 360px; max-width: 100%; background: #fff; border: 1px solid var(--gh-border); border-radius: 12px; padding: 18px 20px; box-shadow: 0 6px 24px rgba(29, 42, 51, .06); }
.consortium img { width: 320px; max-width: 100%; height: auto; display: block; }
.cvdkp { display: flex; justify-content: center; background: var(--gh-accent); border-radius: 8px; padding: 14px; }
.cvdkp img { width: 180px; }
.consortium a { transition: opacity .15s; }
.consortium a:hover { opacity: .85; }
.by { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .08em; color: var(--gh-muted); font-weight: 600; }
@media (max-width: 860px) { .hero-grid { grid-template-columns: 1fr; } .consortium { justify-self: start; } }
h1 { font-size: clamp(1.8rem, 4vw, 2.6rem); line-height: 1.15; max-width: 760px; margin: 0 0 16px; }
.lead { font-size: 1.1rem; color: var(--gh-muted); max-width: 640px; margin: 0 0 24px; }
.feat-icon { color: var(--gh-accent); font-size: 1.2rem; }
.m0 { margin: 0; }
.mt { margin-top: 16px; }
</style>
