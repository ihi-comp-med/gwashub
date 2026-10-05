# Image prompt: how GWASHub works, on one page

A prompt for an AI design tool (Claude Design or similar) to draw a single architecture poster of GWASHub. It shows
concepts (a step as a building block, images, parameters, resources, the AWS estate, security and logging), not the
individual QC steps. Paste everything between the two rules. Facts come from `CLAUDE.md`, `docs/AWS_FULL.md`,
`docs/AWS_ACCOUNT_SETUP.md` and `infra/`; update the prompt when those change.

---

Create one landscape architecture poster (16:9, designed to read at 1920×1080 and to print at A1) titled
**"GWASHub: how it works"**, subtitle **"Upload GWAS summary statistics, run them through versioned QC, meta-analyse
what passes"**. Audience: genetic epidemiologists in a research consortium (HERMES) and the engineers who run the
platform. It must explain the ideas at a glance, then reward close reading. Everything is real; do not invent
services, numbers or features beyond what is listed here.

## Visual style

- Clean, modern technical illustration: flat shapes, soft rounded rectangles (8 px radius), thin 1.5 px outlines,
  generous white space, a light warm-grey background (#F7F6F3). No photorealism, no 3D, no gradients heavier than a
  subtle tint, no stock-art people beyond simple neutral pictograms.
- Palette, used consistently as a code:
  - Navy #1F3A5F: the website and API (the "GWASHub" product).
  - Teal #2A9D8F: compute (QC steps, meta-analysis jobs, AWS Batch).
  - Amber #E9A23B: data and files (uploads, results, reference resources, S3, database).
  - Crimson #C8553D: security controls (sign-in, encryption, permissions, network walls). Draw these as small
    padlock, shield or key badges attached to the thing they protect, not as separate boxes where avoidable.
  - Slate #6B7280: logging, monitoring and audit.
  - Purple #7B5EA7: people and contributions (consortium members, pull requests).
- Typography: one clean sans-serif (Inter or similar). Title 48 pt bold; zone headings 22 pt semibold; box labels
  14 pt; annotations 11 pt in slate grey. Sentence case everywhere. No em dashes anywhere in the text; use colons,
  commas or separate lines.
- Use the official AWS architecture icon style (simplified, single colour per the palette above is fine) for AWS
  services, each with its service name underneath.
- Arrows: solid for data flow, dashed for control or events, dotted for "is recorded in" (logging). Number the main
  QC flow arrows with small circled numbers 1 to 7.

## Layout (left to right, top to bottom)

The poster has four zones. Put a thin labelled border round each.

1. **Left column, "People and roles"** (about 15% width).
2. **Centre top, "The building blocks"** (about 45% width, upper half): the conceptual heart of the poster.
3. **Centre bottom and right, "On AWS"** (lower half, spanning centre and right): the deployment.
4. **Right column top, "Meta-analysis"** and **bottom strip, "Security, logging and reproducibility"** running the
   full width under everything.

### Zone 1: People and roles

Three neutral person pictograms stacked vertically, purple, each with a role badge and one line of text:
- **Uploader** (a cohort analyst): "Uploads their cohort's summary statistics; sees only their own datasets."
- **Reviewer**: "Sees every dataset in the project, re-runs QC, manages members, runs meta-analyses."
- **Platform admin**: "Creates projects; adds shared reference resources."

Under them, a small note with a crimson padlock: "Permissions are checked by the server on every request.
Non-members get 'not found'."

Below, a fourth pictogram, **Consortium developer**, with a GitHub-style pull-request icon: "Adds QC steps,
meta-analysis methods and images by pull request; code owners review before anything runs."

An arrow labelled "invite only" goes from the people to **Amazon Cognito** in Zone 3.

### Zone 2: The building blocks (the most important part; make it the visual centre)

Explain that a pipeline is a list of interchangeable blocks.

**A. One step, drawn as a large exploded building block** (like a LEGO brick or a puzzle piece seen from above,
teal), labelled **"A QC step"**. Around it, four callouts with thin leader lines, each a small card:

- **Script**: a code icon. "Any language (Python, R, ...). Reads one table, writes one table."
- **Image**: a container (shipping-container icon) the block sits inside. "The software environment it runs in,
  pinned by digest."
- **Parameters**: a mini settings form with two sliders and a dropdown. "Typed settings (number, choice, yes/no)
  with defaults and limits. The website draws the form from them; the server checks them."
- **On fail**: a small traffic light. "What a failure does: stop the pipeline, or warn and carry on."

Two more small cards attached to the block's sides:
- **Resources it declares** (amber, a stack of files): "Reference files such as the genome, dbSNP, 1000 Genomes
  panels, LD scores. Only the files a step declares are given to it."
- **Step log** (slate, a document with a check mark): "Every step writes a log with a verdict: pass, warn or
  fail."

A caption under the block, in a light box, titled **"The step contract"**:
`--input  --resources  --params  --log`  ·  "exit 0 = it ran"  ·  "never touches the database or the network".
(The middle dots are allowed here as separators; no em dashes.)

**B. A pipeline, drawn as a row of these blocks clicking together** to the right of the big block: eight small
identical teal blocks in a horizontal chain, unlabelled except for a bracket above them reading "a pipeline = an
ordered list of steps". Do not name the individual steps. Group the eight blocks into four coloured underlay bands
(each band a different light tint) labelled "image 1", "image 2", "image 3", "image 4", with a note: "Consecutive steps
that share an image run together as one job; a new job starts where the image changes."

Above the chain, a version tag like a luggage label: **"Pipeline version 3"** with a note "Each project saves
versions. A version pins its steps, images and reference-file versions."

**C. Where blocks come from** (small, beneath the chain): a shelf or palette of spare blocks in three sections:
- "GWASHub defaults" (navy outline)
- "Consortium additions (by pull request)" (purple outline)
- "Images: base Python, base R, VCF tools, LDSC, GWAMA, METAL" drawn as six small container icons, with a
  note "consortium images build on these".
An arrow from the shelf into the chain reads "reviewers assemble and tune their project's pipeline in the website".

**D. The data contract**, as a slim amber ribbon running along the chain: "Every step after the first speaks one
table format (chromosome, position, alleles, effect, SE, P ...). Output: GRCh38, cleaned file and a QC report."
At the start of the ribbon an upload icon labelled "Any upload format, mapped once at upload"; at the end a
document icon labelled "Cleaned file + report + plots".

Also mark with a small slate gauge icon at both ends of the chain: "Before/after profile and row counts per step,
recorded automatically."

### Zone 3: On AWS

Draw a large rounded rectangle labelled **"AWS account: gwashub-prod (and an identical gwashub-dev), London
eu-west-2"**. Inside it, a **VPC** box (two availability zones shown as two faint vertical lanes) with **public**,
**private** and **database** subnet bands. Outside the account rectangle, at the far left edge of the zone, the
user's browser.

Place these elements and connect them exactly as follows:

1. **Browser** → **Amazon CloudFront** (edge, outside the VPC, a globe icon). Crimson badge: "HTTPS only".
   CloudFront has two origins:
   - **S3 bucket: website** (navy, static site files).
   - **/api/*** → a **VPC origin** into the private subnets → **internal Application Load Balancer** (no public
     address; crimson badge "not reachable from the internet").
2. **Internal ALB** → **API service on ECS Fargate (Graviton)** (navy, container icon, label "FastAPI").
3. **Amazon Cognito user pool** (crimson-accented, beside CloudFront): "Invite-only sign-in, password plus
   authenticator app (MFA) for everyone." Dashed arrow from browser to Cognito "sign in", dashed arrow from Cognito
   to API "verified identity token".
4. **API** ↔ **Amazon RDS PostgreSQL** in the database subnet (amber): "Projects, datasets, pipeline versions,
   metadata." Badges: "encrypted", "automated backups", "Multi-AZ in prod".
5. Three **S3 buckets** (amber) in a row, outside the VPC but connected through an **S3 gateway endpoint** drawn
   as a small gate on the VPC edge: **uploads**, **results**, **resources**. Badges on the group: "KMS encrypted",
   "versioned", "private", "TLS only".
   - Numbered flow **1**: Browser → **uploads** bucket directly, label "upload in 64 MB parts with short-lived
     signed links (the file never passes through the API)".
   - Numbered flow **2**: API → **results** bucket, label "job spec".
6. **AWS Batch** (teal, large): two **compute environments**, scaling to zero, on **EC2 Spot**:
   - "Graviton (arm64)" queue.
   - "Intel (x86-64)" queue, note "for tools without ARM builds".
   Each job box shows a small block-chain fragment (link back visually to Zone 2's image bands) and the note
   "one job per image group; sized from the steps' CPU and memory; 200 to 500 GB encrypted disk".
   - Numbered flow **3**: API → Batch "submit jobs, each waiting on the one before".
   - Numbered flow **4**: Batch jobs ← **Amazon ECR** (container registry icon, teal): "images pinned by digest".
   - Numbered flow **5**: Batch jobs ↔ S3: "read the upload and only the declared reference files; write outputs
     and live progress".
7. **Amazon EventBridge** → **Amazon SQS** → **Worker service on ECS Fargate** (navy): numbered flow **6**,
   dashed: "job finished / failed events". Worker → RDS: "records the result". A small loop arrow on the worker:
   "also checks unfinished runs every 5 minutes".
8. Numbered flow **7**: Browser ← S3 results, label "downloads by signed link, after a permission check".
9. **AWS Secrets Manager** (crimson key icon) linked by dotted lines to API and worker: "database password, session
   key, Cognito client secret".
10. **NAT gateway** in the public subnet (small, grey): "outbound only".
11. A tiny **jump host** with **AWS Systems Manager** icon near RDS: "read-only database browsing through Session
    Manager; no open ports".
12. **IAM roles** shown as small crimson tags on each compute box: API role, worker role, **job role** ("read
    uploads and resources, write results; nothing else").

### Zone 4a: Meta-analysis (right column, top)

A compact panel, teal and amber:
- Left: four small dataset cards that have a green "QC passed" tick, funnelling into one teal box **"Method"**
  with two interchangeable tabs: **GWAMA (default)** and **METAL**, plus a dashed empty tab "consortium methods by
  pull request". Same block metaphor as Zone 2: the method is a block with script, image and parameters.
- Arrow to a teal box **"Post-processing (GWASHub)"** listing: "lead loci by LD clumping against 1000 Genomes",
  "nearest genes", "forest plots", "locus plots", "per-dataset checks (direction, outliers, λGC, LDSC intercept)".
- Output icons: a Manhattan plot thumbnail, a forest plot thumbnail, a locus plot thumbnail, a table.
- Note: "Each meta-analysis runs as one Batch job in its method's image."

### Zone 4b: Security, logging and reproducibility (full-width bottom strip)

Three side-by-side panels.

**Security (crimson)**, a checklist of small badge icons with one line each:
- Invite-only sign-in with MFA for everyone (Cognito).
- Permissions enforced on the server: uploader, reviewer, platform admin; non-members see nothing.
- No public servers: API and database live in private subnets; only CloudFront faces the internet.
- Everything encrypted at rest (KMS) and in transit (TLS only).
- Short-lived signed links for every upload and download.
- Least-privilege IAM roles per service; no passwords or access keys in code, secrets in Secrets Manager.
- Separate AWS accounts for production, development, audit and management (AWS Organizations, Control Tower
  region guardrail).
- Code for new steps reviewed by pull request (code owners) before it can run.

**Logging and monitoring (slate)**:
- **Amazon CloudWatch Logs**: one log group per environment with streams for the API, worker and every Batch job.
  Draw dotted lines from API, worker and Batch to it.
- **AWS CloudTrail** (organisation-wide trail): "who did what, when, in every account".
- **Amazon GuardDuty**: "threat detection".
- **AWS Security Hub** and **IAM Access Analyzer**: "best-practice checks", delegated to the **audit account**.
- Inside the app: each step's log with its verdict, live progress per step, and the run's messages shown to users.

**Reproducibility (amber and teal)**:
- A receipt-shaped card titled **"Every run records"**: pipeline version, the exact steps and their parameters,
  image digests, reference-file versions (with checksums), before/after profile of the data.
- A small catalogue card: "Every reference file has a recorded source, version and licence."
- "Same code runs locally (folder, SQLite, Docker) and on AWS (S3, PostgreSQL, Batch): only configuration changes."

## Small extras

- A legend box in the bottom-right corner explaining the six colours and three arrow styles.
- A tiny "dev / prod" toggle graphic near the AWS account title: "dev is the lean copy: one NAT gateway, smaller
  database, Development build banner on every page".
- Footer, left: "GWASHub · HERMES Consortium". Footer, right: "Architecture as of October 2026".

## Avoid

- Naming or drawing the individual QC steps (no "liftover", "filter", etc. in Zone 2): the point is the block
  concept.
- Dense paragraphs: every annotation is one line, two at most.
- Clutter from crossing arrows: route arrows orthogonally, and let the numbered QC flow (1 to 7) read as one clear
  path from the browser, through upload, jobs and events, back to the browser.
- Logos of companies other than AWS service icons and a generic pull-request glyph; no invented product names.
- Em dashes.

---
