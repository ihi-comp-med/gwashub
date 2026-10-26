<script setup lang="ts">
const { data } = useApiGet<any>('/steps/default')
const template = computed(() => (data.value?.template ?? []).map((k: string) => data.value.steps.find((s: any) => s.key === k)).filter(Boolean))
const optional = computed(() => (data.value?.steps ?? []).filter((s: any) => !data.value.template.includes(s.key)))
const logoSrc = '/brand/hermes-logo-full.png'
const flow = [
  { icon: 'pi pi-upload', title: 'Upload', text: 'A cohort analyst uploads summary statistics with study metadata and maps the columns.' },
  { icon: 'pi pi-cog', title: 'QC', text: 'The project pipeline runs each step in order and records its verdict, metrics and outputs.' },
  { icon: 'pi pi-eye', title: 'Review', text: 'Reviewers approve datasets that passed with warnings, or send them back with a note.' },
  { icon: 'pi pi-chart-bar', title: 'Meta-analyse', text: 'Reviewers choose datasets by phenotype, ancestry and sex and run the meta-analysis.' },
]
const onFail: Record<string, string> = {
  block: 'stops the run (the QC report is still produced)',
  flag: 'is recorded and counts towards the overall verdict',
  continue: 'is recorded but ignored for the overall verdict',
}
</script>

<template>
  <div class="container page about">
    <div class="intro">
      <div>
        <h1 class="page-title">About GWASHub</h1>
        <p class="lead">GWASHub collects GWAS summary statistics from contributing cohorts, checks them with the same
          versioned QC pipeline, and prepares them for meta-analysis. It was built for the HERMES Consortium.</p>
      </div>
      <a href="https://hermesconsortium.org" target="_blank" rel="noopener" class="logo">
        <img :src="logoSrc" width="240" height="91" alt="HERMES: Heart Failure Molecular Epidemiology for Therapeutic Targets">
      </a>
    </div>

    <h2>How it works</h2>
    <ol class="flow">
      <li v-for="(f, i) in flow" :key="f.title">
        <span class="n">{{ i + 1 }}</span>
        <div><strong><i :class="f.icon" /> {{ f.title }}</strong><p>{{ f.text }}</p></div>
      </li>
    </ol>

    <h2>Roles</h2>
    <dl class="roles">
      <dt>Uploader</dt><dd>Uploads datasets and edits their metadata. Sees project summaries, and opens only their own datasets: those they uploaded, and those whose contact email is theirs (when someone else uploads for them).</dd>
      <dt>Reviewer</dt><dd>Sees every dataset, reviews QC outcomes, manages the pipeline and upload options, and runs meta-analyses.</dd>
    </dl>

    <h2 id="qc">The default QC pipeline</h2>
    <p class="muted">Every project starts from these steps and can add, remove, reorder or configure them. Each step is a script with a fixed
      contract (<code>--input --resources --params --log</code>), so any step can be inspected with the code viewer and re-run on the same commit.</p>
    <table class="steps">
      <thead><tr><th>#</th><th>Step</th><th>What it does</th><th>If it fails</th></tr></thead>
      <tbody>
        <tr v-for="(s, i) in template" :key="s.key">
          <td class="muted">{{ i + 1 }}</td>
          <td><strong>{{ s.title }}</strong><div class="mono muted small">{{ s.script }}</div></td>
          <td>{{ s.description }}<div v-if="s.resources.length" class="muted small">Uses {{ s.resources.join(', ') }}</div></td>
          <td><Tag :value="s.on_fail" :severity="s.on_fail === 'block' ? 'danger' : s.on_fail === 'flag' ? 'warn' : 'secondary'" /></td>
        </tr>
      </tbody>
    </table>
    <p class="muted small">A failing step that is set to <b>block</b> {{ onFail.block }}; <b>flag</b> {{ onFail.flag }}; <b>continue</b> {{ onFail.continue }}.</p>
    <p v-if="optional.length" class="muted">Optional steps: <span v-for="(s, i) in optional" :key="s.key">{{ s.title }}<span v-if="i < optional.length - 1">, </span></span>.</p>

    <h2>Data handling</h2>
    <ul class="plain">
      <li>Files are stored privately and only reachable through permission-checked links.</li>
      <li>Reference files are checksummed and versioned; a pipeline version records the exact files and code it used.</li>
      <li>Only summary statistics are accepted. Reviewers confirm that reference files contain no individual-level data.</li>
    </ul>

    <!-- The posters live in docs/images (served as /posters); their prompts, kept up to date, in docs/IMAGE_PROMPTS.md. -->
    <h2>The whole picture</h2>
    <a href="/posters/GWASHub-how-it-works.png" target="_blank" rel="noopener" class="poster">
      <img src="/posters/GWASHub-how-it-works.png" loading="lazy" alt="How GWASHub works: people and roles, QC steps and pipelines, meta-analysis, and the AWS deployment">
    </a>
    <p class="muted small">Click to open it full size.</p>

    <h2>Developers</h2>
    <p class="muted">The software behind GWASHub, from the website down to the AWS infrastructure. How consortia add QC steps,
      meta-analysis methods and images is in CONTRIBUTING.md in the repository.</p>
    <a href="/posters/GWASHub-stack.png" target="_blank" rel="noopener" class="poster">
      <img src="/posters/GWASHub-stack.png" loading="lazy" alt="The GWASHub stack: website, API and worker, pipeline jobs and images, data, and infrastructure as code">
    </a>
    <p class="muted small">Click to open it full size.</p>
  </div>
</template>

<style scoped>
.about { max-width: 980px; }
.intro { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 32px; align-items: center; margin-bottom: 8px; }
@media (max-width: 760px) { .intro { grid-template-columns: 1fr; } }
.lead { font-size: 1.05rem; color: var(--gh-muted); margin: 0; }
.logo img { display: block; width: 240px; height: auto; border: 1px solid var(--gh-border); border-radius: 10px; padding: 10px; background: #fff; }
h2 { font-size: 1.1rem; margin: 36px 0 14px; }
.flow { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.flow li { display: flex; gap: 12px; background: var(--gh-surface); border: 1px solid var(--gh-border); border-radius: 12px; padding: 14px; }
.flow .n { flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%; background: var(--gh-accent); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 600; }
.flow p { margin: 4px 0 0; color: var(--gh-muted); font-size: 0.88rem; }
.flow i { color: var(--gh-accent); font-size: 0.85rem; }
.roles { display: grid; grid-template-columns: 110px 1fr; gap: 8px 16px; margin: 0; }
.roles dt { font-weight: 600; }
.roles dd { margin: 0; color: var(--gh-muted); }
.steps { width: 100%; border-collapse: collapse; background: var(--gh-surface); border: 1px solid var(--gh-border); border-radius: 12px; overflow: hidden; font-size: 0.9rem; margin-bottom: 10px; }
.steps th { text-align: left; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--gh-muted); padding: 10px 12px; border-bottom: 1px solid var(--gh-border); }
.steps td { padding: 10px 12px; border-bottom: 1px solid var(--gh-border); vertical-align: top; }
.steps tr:last-child td { border-bottom: 0; }
.small { font-size: 0.78rem; }
.plain { margin: 0; padding-left: 18px; color: var(--gh-muted); line-height: 1.7; }
.poster { display: block; border: 1px solid var(--gh-border); border-radius: 12px; overflow: hidden; background: #fff; }
.poster img { display: block; width: 100%; height: auto; }
</style>
