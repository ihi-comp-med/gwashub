<script setup lang="ts">
// How a consortium adds its own QC step (and, if needed, an image): a pull request to the GWASHub repository.
defineProps<{ images: any[], contributingUrl?: string }>()
const visible = defineModel<boolean>('visible', { default: false })
const stepYaml = `name: my_filter
title: My filter
description: What it removes or checks, in one or two sentences.
command: [python, my_filter.py]      # or [Rscript, my_filter.R]
image: gwashub/base                  # or gwashub/base-r, or your own image
default_on_fail: flag                # block | flag | continue
resources: []                        # reference files it needs, e.g. [my_regions.bed]
params:                              # defaults reviewers can change per project
  threshold: 0.01`
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Contribute a custom step" class="csd" :draggable="false">
    <div class="body">
      <p class="m0">Custom steps live in the GWASHub repository next to the default ones. Adding one is a pull request, so the code
        is reviewed and tested before it runs on anyone's data. Once it's merged and deployed, every project can add it to its pipeline.</p>

      <h4>1. Add a folder for your step</h4>
      <p class="m0">Under <code>pipeline/qc_steps/&lt;consortium&gt;/&lt;step&gt;/</code>, for example <code>pipeline/qc_steps/hcm/sarcomere_filter/</code>:</p>
      <ul>
        <li>the script (Python or R),</li>
        <li><code>step.yaml</code> describing it,</li>
        <li>a test, <code>test_&lt;step&gt;.py</code>, that runs the script on a small table,</li>
        <li>for a new consortium, a short <code>README.md</code> in <code>pipeline/qc_steps/&lt;consortium&gt;/</code> naming who looks after it.</li>
      </ul>
      <pre class="code">{{ stepYaml }}</pre>

      <h4>2. Follow the step contract</h4>
      <ul>
        <li>The script is called with <code>--input working.tsv --resources DIR --params params.json --log step_log.json</code>.</li>
        <li>It may rewrite <code>--input</code> in place (tab-separated, standard columns <code>chr pos ea oa beta se p</code> …).</li>
        <li>It writes a JSON step log with a <code>verdict</code> of <code>pass</code>, <code>warn</code> or <code>fail</code>, plus optional <code>metrics</code>, <code>messages</code> and <code>artifacts</code>.</li>
        <li>Extra files (plots, tables) go in <code>$GWASHUB_ARTIFACT_DIR</code> and can be downloaded from the run.</li>
        <li>Exit 0 when it ran. Steps work only with their inputs: no network calls, no database.</li>
      </ul>

      <h4>3. Need other software? Add an image</h4>
      <p class="m0">Use an existing image if you can:</p>
      <ul class="imgs">
        <li v-for="i in images" :key="i.ref"><code>{{ i.ref }}</code> · {{ i.title }}<span v-if="i.description" class="muted">: {{ i.description }}</span></li>
      </ul>
      <p class="m0">Otherwise add <code>pipeline/images/&lt;consortium&gt;/&lt;image&gt;/Dockerfile</code>, built <code>FROM gwashub/base</code> or
        <code>FROM gwashub/base-r</code> (they contain the GWASHub runner), installing only what your step needs. Images are built for
        ARM (Graviton) and pinned by digest when deployed. Consecutive steps that share an image run together as one job,
        so a different image costs a few extra minutes per run.</p>

      <h4>4. Open the pull request</h4>
      <p class="m0">The automated checks run every step's tests. Your consortium's maintainers and a GWASHub maintainer review it. After it's
        merged and deployed, the step appears in the step list here.</p>
      <p class="m0">
        <a v-if="contributingUrl" :href="contributingUrl" target="_blank" rel="noopener">Full guide: CONTRIBUTING.md</a>
        <span v-else class="muted">The full guide is <code>CONTRIBUTING.md</code> in the GWASHub repository.</span>
      </p>
    </div>
  </Dialog>
</template>

<style scoped>
.body { display: flex; flex-direction: column; gap: 8px; font-size: 0.92rem; }
h4 { margin: 10px 0 0; font-size: 0.95rem; }
ul { margin: 0; padding-left: 20px; }
.code { background: #1e1e1e; color: #d4d4d4; padding: 10px 12px; border-radius: 6px; font-size: 12px; overflow: auto; margin: 4px 0; }
.imgs code { font-size: 0.85rem; }
.m0 { margin: 0; }
</style>
