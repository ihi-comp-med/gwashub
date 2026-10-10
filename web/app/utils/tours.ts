/** Guided tours (the Help menu in the header; run by composables/useTours.ts with driver.js).
 * A step points at an element marked data-tour="…" on a page ("/projects/:pid/…", :pid being the current project, or
 * the Practice project outside a project). `wait`: the element only appears after the user does something; the tour
 * says so and stays put until it's there. `optional`: skipped when the element isn't on the page. No element: a
 * message in the middle of the screen. Every data-tour named here must exist (checked by api/tests/test_tours.py). */
export interface TourStep { page?: string, el?: string, title: string, text: string, wait?: string, optional?: boolean }
export interface Tour { id: string, title: string, summary: string, reviewer?: boolean, steps: TourStep[] }

const P = '/projects/:pid'

export const TOURS: Tour[] = [
  {
    id: 'upload', title: 'Upload a dataset', summary: 'One file, through the upload wizard',
    steps: [
      { page: P, el: 'upload-button', title: 'Upload a dataset', text: 'Every single upload starts here. For many files at once, see the bulk upload tour.' },
      { page: `${P}/upload`, el: 'upload-stepper', title: 'Five steps', text: 'Details, study metadata, the file, the column mapping, then upload. You can go back to any step.' },
      { el: 'upload-details', title: 'Details', text: 'The cohort (pick one or type a new name), phenotype, ancestry, sex, genome build and sample sizes. When they are complete, click Next in the wizard.' },
      { el: 'upload-metadata', title: 'Study metadata', wait: 'Fill in the details, click Next in the wizard, then Next here.',
        text: 'The project\'s metadata fields. A cohort\'s fields come from its record; required fields are marked.' },
      { el: 'upload-file', title: 'The file', wait: 'Fill in the metadata, click Next in the wizard, then Next here.',
        text: 'Plain or gzipped; tab-, comma- or space-separated, with a header line. In the Practice project, use one of its practice files (on the project\'s Overview).' },
      { el: 'upload-mapping', title: 'Map the columns', wait: 'Choose a file, click Next in the wizard, then Next here.',
        text: 'Which column holds the chromosome, position, alleles, effect, standard error, allele frequency and P. GWASHub suggests a mapping from the header: check it.' },
      { el: 'upload-submit', title: 'Upload', wait: 'Finish the mapping, click Next in the wizard, then Next here.',
        text: 'Sends the file. The dataset then waits as Pending QC: nothing runs until you click Start QC.' },
      { el: 'panel-start-qc', title: 'Start QC', wait: 'Click Upload and wait for the dataset\'s panel to open, then click Next here.',
        text: 'Check the details in the panel (build, sample sizes, mapping, metadata), then click Start QC. That\'s the end of this tour.' },
    ],
  },
  {
    id: 'qc', title: 'Check QC and start it', summary: 'Pending datasets, QC steps, plots and downloads',
    steps: [
      { page: `${P}/datasets`, el: 'datasets-table', title: 'Datasets', text: 'Every dataset in the project. Uploaders open only their own: click a row to open it.' },
      { el: 'datasets-filters', title: 'Filters', text: 'By cohort, phenotype, ancestry, sex or QC status. Mine shows only yours.' },
      { el: 'datasets-waiting', title: 'Waiting for QC', optional: true, text: 'Datasets uploaded but not yet checked. Start QC on all of them here, or one at a time from each dataset\'s panel.' },
      { el: 'datasets-bulk', title: 'Several at once', text: 'Tick datasets to start QC on several pending ones, or to delete them.' },
      { el: 'panel-start-qc', title: 'Start QC', optional: true, text: 'A pending dataset: check its details, then click Start QC.' },
      { el: 'panel-steps', title: 'Processing steps', wait: 'Open a dataset that has been through QC by clicking its row, then click Next here.',
        text: 'Each QC step with its verdict (pass, warn or fail). Click a step for its messages, numbers and files, such as the rows it removed.' },
      { el: 'panel-metrics', title: 'QC metrics', optional: true, text: 'Genomic inflation, hits, the LD score regression intercept and more.' },
      { el: 'panel-plots', title: 'Plots', optional: true, text: 'Manhattan and QQ plots of the cleaned file.' },
      { el: 'panel-actions', title: 'Downloads', text: 'The raw file, the QC\'d file and the QC report.' },
      { el: 'panel-details-tab', title: 'Details', text: 'The dataset\'s description and study metadata, editable by the uploader or a reviewer, with an edit history.' },
    ],
  },
  {
    id: 'review', title: 'Review a dataset', summary: 'Approve, request changes or reject', reviewer: true,
    steps: [
      { page: `${P}/datasets`, el: 'datasets-filters', title: 'Find datasets to review', text: 'Datasets that passed QC with warnings show as For review. Filter by QC status to list them.' },
      { el: 'datasets-bulk', title: 'Re-run or pin', text: 'Tick datasets to re-run their QC, or to pin them to another QC pipeline.' },
      { el: 'panel-review', title: 'Your decision', wait: 'Open a dataset that is For review by clicking its row, then click Next here.',
        text: 'Approve it for meta-analysis, request changes or reject it. Your note is shown on the dataset; requesting changes lets the uploader upload a corrected file or re-run QC, and their Datasets tab shows a badge until they do.' },
      { el: 'panel-steps', title: 'The evidence', text: 'Click a step for its messages, numbers and files: plots, tables and the rows it removed, each to open or download.' },
      { el: 'panel-actions', title: 'Other actions', text: 'Override QC outcome replaces any verdict; Re-run QC runs it again with the dataset\'s pipeline as it is now.' },
    ],
  },
  {
    id: 'meta', title: 'Run a meta-analysis', summary: 'Choose datasets, settings, run, read results', reviewer: true,
    steps: [
      { page: `${P}/meta`, el: 'meta-list', title: 'Meta-analyses', text: 'Every meta-analysis run in this project. Click one to see its results.' },
      { el: 'meta-new', title: 'New meta-analysis', text: 'Click this to start one.' },
      { el: 'meta-datasets', title: 'Choose datasets', wait: 'Click New meta-analysis, then Next here.',
        text: 'Pick at least two datasets that passed QC (or were approved). Datasets that can\'t be combined say why.' },
      { el: 'meta-settings', title: 'Method and settings', text: 'GWAMA (the default) or METAL, and their settings. They start from the project\'s defaults.' },
      { el: 'meta-post', title: 'Post-processing', text: 'Lead loci (by LD clumping) and heterogeneity flags, the same whichever method runs.' },
      { el: 'meta-run', title: 'Run', text: 'Name it and run it. The results page shows the sample sizes, Manhattan and QQ plots, lead loci with locus and forest plots, and the files to download.' },
    ],
  },
  {
    id: 'bulk', title: 'Bulk upload', summary: 'Many files at once with the upload script',
    steps: [
      { page: '/account', el: 'account-bulk', title: 'Bulk upload', text: 'Upload many files at once, with their metadata, from an Excel manifest, using a small Python script.' },
      { el: 'account-script', title: 'The script', text: 'Download it. It needs Python 3.9 or later and nothing else, and already knows this site\'s address.' },
      { el: 'account-token-form', title: 'An API token', text: 'Make a token for the project. It can only upload, works in that one project and lasts 30 days. Copy it when it is shown: it isn\'t shown again.' },
      { title: 'On your computer', text: 'Run: python3 gwashub_upload.py template manifest.xlsx. Fill in its Manifest sheet (the README sheet explains every column), then run check, and upload once check says OK.' },
      { page: `${P}/datasets`, el: 'datasets-table', title: 'Then on the site', text: 'Your files arrive as Pending QC. Open them, check them, then start QC.' },
    ],
  },
  {
    id: 'setup', title: 'Set up a project', summary: 'Members, upload options, metadata and pipelines', reviewer: true,
    steps: [
      { page: P, el: 'overview-members', title: 'Members', text: 'Invite people and set their roles. Uploaders submit data and see their own datasets; reviewers do everything else.' },
      { page: `${P}/pipeline`, el: 'setup-upload-options', title: 'Upload options', text: 'Phenotypes (case/control or quantitative), allowed ancestries and expected sex strata. The upload form offers only these.' },
      { el: 'setup-metadata', title: 'Metadata fields', text: 'Which study metadata fields are shown and which are required, and the project\'s own fields.' },
      { el: 'setup-pipelines', title: 'QC pipelines', text: 'The project\'s default pipeline and any others. Datasets follow the default unless a reviewer pins them to another.' },
      { el: 'setup-steps', title: 'Steps', text: 'The selected pipeline\'s steps, in order, with what happens on failure. Open a step to see its contract and change its settings.' },
      { el: 'setup-save', title: 'Save a version', optional: true, text: 'Saving makes a new version. Datasets that ran on an older one show as out of date, with a re-run button.' },
      { el: 'setup-resources', title: 'Resources', text: 'The reference files steps use. Each pipeline version pins their versions.' },
    ],
  },
]
