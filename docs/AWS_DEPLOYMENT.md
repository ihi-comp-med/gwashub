# Deploying GWASHub on AWS

A step-by-step guide to running GWASHub securely on your own AWS account, sized for many GB of
GWAS summary statistics. It covers what to set up in AWS, what has to change in the code, and in
what order.

Written 2026-10-03 against the code in this repo. Prices are approximate (eu-west-2, October 2026):
check them with the [AWS Pricing Calculator](https://calculator.aws/) before committing.

> **As built (2026-10-03):** this is the design document; **`docs/AWS_FULL.md`** describes what is deployed
> and how to run it. Code changes 1–6 in §14 are done; 7–10 are open. Differences from this design:
> - **Lean dev sizing** (single-AZ database, one NAT gateway, no interface endpoints, no WAF yet); prod
>   settings are in `infra/gwashub_infra/config.py` but not deployed.
> - **No public load balancer:** CloudFront reaches an *internal* load balancer through a VPC origin, and
>   the site uses CloudFront's default domain until a custom domain is added.
> - **Sign-in:** the API does the Cognito code exchange itself and keeps a short-lived session cookie;
>   tokens never reach the browser. MFA is required for everyone.
> - **Per-step images (§6 Stage B):** consecutive steps sharing an image run as one Batch job and jobs are
>   chained with Batch job dependencies, instead of Step Functions. Isolating consortium steps in a
>   locked-down environment is still to do.
> - **Custom steps** come from `pipeline/qc_steps/<consortium>/` by pull request, not from arbitrary git
>   repositories, so nothing is cloned at run time (`repo_fetch.py` was removed).
> - Images live in `pipeline/images/<owner>/<image>/` (not `images/base`); `images/app` is the site image.

---

## 0. The short version

| Concern | Choice |
|---|---|
| Region | **eu-west-2 (London)**, unless your data agreements say otherwise |
| Accounts | AWS Organizations: separate **prod**, **dev** and **log-archive** accounts |
| People | IAM Identity Center (SSO) with MFA; nobody uses the root user or long-lived keys |
| Files | **S3**, encrypted with your own KMS key, never public, uploaded **directly from the browser** in parallel chunks |
| QC jobs | **AWS Batch on EC2** (memory-optimised instances, Spot with on-demand fallback) |
| API | Container on **ECS Fargate** behind an **Application Load Balancer** with **WAF** |
| Database | **RDS PostgreSQL**, Multi-AZ in prod, encrypted, private |
| Website | Static files on **S3 + CloudFront**; `/api/*` routed to the load balancer on the same domain |
| Sign-in | **Amazon Cognito**, invite-only, MFA required for reviewers |
| Everything | Defined as code with **AWS CDK**, deployed from GitHub Actions with no stored AWS keys |
| Cost | Roughly **$250–450 a month** for a production setup at low traffic, plus storage (§12) |

Two code changes matter most for scale. Files must **never pass through the API server**: browsers
upload to and download from S3 directly. And the QC steps, which currently load whole files into
memory, need memory-sized machines, and eventually chunked processing (§6).

---

## 1. Before you start (decisions and paperwork)

1. **Data governance.** GWAS summary statistics are usually not personal data, but consortium data
   often comes with data-access agreements. Ask your institution's information-governance team:
   - whether a **DPIA** (data protection impact assessment) is needed;
   - which **region** the data may live in (London keeps it in the UK);
   - the required **retention and deletion** policy;
   - whether any contributors' agreements forbid cloud storage or particular providers.
2. **Who pays.** Use an institutional AWS account if possible: universities often have enterprise
   agreements, the **AWS Global Data Egress Waiver** (free downloads up to a limit), and research
   credits. Ask your research-computing or IT team before opening a personal account.
3. **A domain name**, e.g. `gwashub.<your-domain>`, managed in Route 53 or delegated to it.
4. **Expected volume.** Rough numbers drive sizing. For example: 200 datasets × 1–3 GB gzipped
   (10–40 million variants each) is about 0.5 TB raw, plus QC outputs of similar size.
5. **Owners.** At least two named admins, so access never depends on one person.

---

## 2. Accounts and security baseline (day 1–2)

**New to AWS? Follow `docs/AWS_ACCOUNT_SETUP.md` instead of this section.** It does the same thing step
by step, using AWS Control Tower to automate most of it. The list below is the summary for people who
know AWS.

Do this before putting anything in the account.

1. **Create an AWS Organization** from a new management account with a role email address (e.g.
   `aws-gwashub@<domain>`, not a personal inbox). Turn on **MFA for the root user**, store its
   credentials offline, and never use it day to day.
2. **Create member accounts:** `gwashub-prod`, `gwashub-dev`, `gwashub-logs`. Dev mistakes then
   can't touch production data, and logs can't be deleted from the account they describe.
3. **IAM Identity Center (SSO):** create users for the admins with MFA required. Give them
   `AdministratorAccess` in dev and a narrower role in prod (or admin plus approval). No IAM users,
   no access keys.
4. **Service control policies** (guardrails that apply even to admins):
   - deny all regions except eu-west-2 (and us-east-1 for global services such as CloudFront certificates);
   - deny leaving the organization, and disabling CloudTrail, GuardDuty or Config;
   - deny making S3 buckets public (`s3:PutBucketPublicAccessBlock` changes).
5. **Organization-wide logging and detection:**
   - **CloudTrail** organization trail to an S3 bucket in `gwashub-logs` (object lock on), with
     **data events** for the GWASHub data buckets, so every read and write of a file is recorded.
   - **GuardDuty**, including S3 protection and **Malware Protection for S3** on the upload bucket.
   - **Security Hub** with the AWS Foundational Security Best Practices and CIS standards.
   - **AWS Config** and **IAM Access Analyzer** (which flags anything shared outside the account).
6. **Budgets:** monthly budget alerts at 50/80/100%, plus **Cost Anomaly Detection** emailing both admins.
7. **GitHub → AWS without keys:** create an IAM OIDC identity provider for GitHub Actions, and a
   deploy role that only the `main` branch of your repo can assume.

---

## 3. Infrastructure as code

Create `infra/` in this repo with **AWS CDK (Python)**, which matches the codebase. One stack per area:

| Stack | Contains |
|---|---|
| `Network` | VPC across 2–3 availability zones; public subnets (load balancer only), private subnets (everything else), VPC endpoints |
| `Data` | KMS key, S3 buckets, RDS PostgreSQL |
| `Auth` | Cognito user pool and app client |
| `Compute` | ECR repositories, Batch compute environments and job queues, SQS queue, EventBridge rules |
| `Api` | ECS cluster and service, load balancer, WAF, task roles |
| `Web` | S3 bucket for the site, CloudFront distribution, certificates, DNS |
| `Monitoring` | CloudWatch dashboards and alarms, SNS topic for alerts |

Bootstrap CDK in dev and prod (`cdk bootstrap`), deploy dev first, and only deploy prod from CI.

---

## 4. Network

- **VPC** with private subnets for ECS, Batch and RDS. Only the load balancer sits in public subnets.
- **VPC endpoints**, so AWS traffic never crosses the internet (and NAT data charges stay low): S3
  (gateway, free), ECR (`api`, `dkr`), CloudWatch Logs, Secrets Manager, STS, SQS, Batch, KMS.
- **One NAT gateway** per availability zone in prod (one in total in dev) for the remaining outbound
  traffic: cloning custom-step repositories from GitHub and pulling Docker Hub images.
  - Use an **ECR pull-through cache** for Docker Hub, so images are copied into your account once and scanned.
  - Restrict outbound traffic with security groups, and optionally AWS Network Firewall, to GitHub and Docker Hub only.
- **Security groups:** the load balancer accepts 443 from CloudFront only (AWS managed prefix
  list); the API accepts traffic only from the load balancer; RDS accepts only the API and the worker.

---

## 5. Storage for many GB (S3)

### Buckets

| Bucket | Holds | Lifecycle |
|---|---|---|
| `gwashub-prod-uploads` | raw uploads, by project and dataset | to Glacier Instant Retrieval after 90 days (cheaper, still instant to read) |
| `gwashub-prod-results` | QC outputs, meta-analysis results | intermediates deleted after 30 days; final outputs kept |
| `gwashub-prod-resources` | reference files (chain files, panels), versioned | none |
| `gwashub-prod-web` | the built website | none |

On every data bucket:
- **Block Public Access** (also enforced by the service control policy).
- **SSE-KMS** with a customer-managed key and **S3 Bucket Keys** on (this cuts KMS costs for large files).
- A bucket policy that **denies non-TLS requests** and any access not from your account's roles.
- **Versioning** on, so accidental deletes and overwrites can be undone, and **access logging** to the logs account.
- Object Lock (governance mode) on the resources bucket, if you want reference files to be truly immutable.

### Uploads: straight from the browser to S3

The local version streams uploads through the API (`POST /api/projects/{id}/datasets`). At multi-GB
sizes this ties up API workers and fails on timeouts. Replace it with **S3 multipart upload via
presigned URLs**:

1. The browser asks the API to start an upload. The API checks permissions and the metadata,
   creates the dataset record (status `uploading`), calls `CreateMultipartUpload` on
   `uploads/<project>/<dataset>/<file>` with SSE-KMS, and returns an upload ID.
2. The browser splits the file into **64–128 MB parts** and, in batches, asks the API for presigned
   `UploadPart` URLs (valid 15 minutes). It uploads 4–6 parts in parallel straight to S3 with a
   **SHA-256 checksum per part**, and retries failed parts.
3. The browser calls "complete"; the API calls `CompleteMultipartUpload`, and the dataset moves to `queued`.
4. An S3 event, after GuardDuty's malware scan, starts QC.
5. A lifecycle rule **aborts incomplete multipart uploads after 7 days**, so abandoned uploads don't cost money.

S3 objects can be up to 5 TB, so file size stops being a constraint. The bucket's CORS rule allows
`PUT` only from your site's domain.

**Downloads** (raw file, QC'd file, plots, reports) become **presigned GET URLs** (valid 5–15
minutes, with a `Content-Disposition` header for the filename). The API checks permissions and then
redirects. The `FileResponse` calls in `api/gwashub_api/routers/datasets.py` and `meta.py` change to this.

---

## 6. QC and meta-analysis jobs (AWS Batch)

### Why EC2 rather than Fargate

Measured on this pipeline: a harmonised GWAS file takes about **265 bytes per variant** in pandas.
A 20-million-variant file is therefore about **5 GB per copy** in memory, and pandas operations
typically peak at 2–4× that. The first steps read every column as text, which is heavier still.
Realistic peak use for a large file is **20–40 GB**. Fargate tops out at 120 GB of memory and
200 GB of disk; Batch on EC2 gives more room and is cheaper per GB-hour.

- **Managed compute environment**, memory-optimised instances (`r7i.xlarge` to `r7i.4xlarge`,
  32–128 GB of memory), **Spot first** with an on-demand fallback queue, minimum 0 vCPUs (costs
  nothing when idle).
- **EBS gp3 scratch** sized from the input (e.g. 5× the compressed size), or EFS if steps run as separate jobs.
- **Job role** (what the job may access): read its own input object and the reference files; write
  only `results/<run-id>/`. No database, no Secrets Manager, no other buckets.
- Logs go to CloudWatch with 90-day retention. Set job timeouts (e.g. 2 hours per step).

### Running a pipeline: two stages

**Stage A, fastest to production: one Batch job per QC run.**
- Bake all default steps into `gwashub/base` (the Dockerfile in `images/base` needs only `pipeline/`
  and its requirements added).
- Add a small entrypoint that downloads the input and pinned resources from S3, calls the existing
  `pipeline.runner.executor.run_pipeline`, and uploads `results/<run-id>/`. Much of the S3 code is already in
  `pipeline/runner/aws/s3io.py`.
- Custom steps run in the same container and clone their repo at the pinned commit (`pipeline/runner/aws/repo_fetch.py`).
- This works today for Python steps, and for R steps if R is added to the image.

**Stage B, per-step images: AWS Step Functions.**
- The plan's design (PLAN §2.2): a state machine runs *stage → step 1 → … → publish*, each step as a
  Batch job in its own image, sharing a scratch volume (EFS) or exchanging files via S3.
- Build this once projects need custom steps with their own images, and **run custom steps in a
  separate, locked-down compute environment**: no internet access, no AWS permissions beyond
  that run's folder, strict CPU, memory and time limits. Custom steps are code written by project
  members, so treat them as untrusted.

### Job completion and the API

The local version runs jobs in API threads. Replace that with:
1. The API submits the Batch job (or starts a Step Functions execution) and records its ID.
2. **EventBridge** sends Batch job state changes to an **SQS queue**.
3. A small **worker service** (the same container image, a different command) reads the queue,
   reads `result.json` from S3 and updates the database.

Jobs then survive API restarts and deploys, and retries are automatic.

### Making the steps scale

pandas reads the whole file into memory. That's fine on big instances up to roughly 30–50 million
variants. Beyond that, or to save money:
- Move the heavy steps (`row_check`, `harmonise_columns`, `value_ranges`, `reference_check`) to
  **Polars** (lazy, streaming) or **DuckDB**. Both process files larger than memory and are
  5–20× faster. The step contract doesn't change, so steps can be migrated one at a time with their
  existing tests.
- Load the reference panel per chromosome rather than all at once.
- Run the **meta-analysis per chromosome as a Batch array job** (22 parallel jobs, then a merge),
  because the combined input grows with datasets × variants.

### Real reference data

Replace the synthetic files in `local/synthetic/resources/` with real ones, uploaded to the resources bucket
and registered as shared resources:
- `hg19ToHg38.over.chain.gz` from UCSC;
- `ref_panel_grch38.tsv.gz` built from 1000 Genomes (or gnomAD) with per-ancestry allele
  frequencies, in the same columns as the synthetic panel (`chr pos rsid ref alt af_EUR …`).
  Expect 1–3 GB; build it with a one-off Batch job and keep the script in the repo.

---

## 7. The API (ECS Fargate)

- Build the API as a container image (add a `Dockerfile` in `api/`), push it to ECR with **scan on
  push**, run as a **non-root user** with a **read-only root filesystem**.
- **ECS service**: 2+ tasks across availability zones (e.g. 0.5 vCPU / 1 GB each, since files no
  longer pass through it), with autoscaling on CPU and request count.
- **Application Load Balancer**: HTTPS only (ACM certificate), HTTP redirects to HTTPS, health
  check on `/api/health`.
- **AWS WAF** on the load balancer: AWS managed rule groups (common, known bad inputs, IP
  reputation), plus a rate limit per IP (e.g. 1,000 requests per 5 minutes).
- **Task role** (least privilege): presign on the GWASHub buckets' prefixes only, submit Batch jobs
  to the GWASHub queues, send to its SQS queue, read its own secrets.
- **Config through environment variables and Secrets Manager.** The settings in
  `api/gwashub_api/config.py` already read from the environment. Add the bucket names, KMS key ID,
  queue names and Cognito settings the same way.

### Must change before production

- **Remove the passwordless dev login.** `POST /api/auth/login` and `GET /api/auth/users` in
  `api/gwashub_api/main.py` must not exist in prod. Gate them behind `GWASHUB_ENV=local`, and have
  CI fail if they're reachable.
- Replace the HMAC session cookie with **Cognito token validation** (§9).
- Replace the `add_missing_columns` dev migration in `db.py` with **Alembic** migrations.
- Set CORS to your domain only, which CloudFront same-origin routing makes simple.

---

## 8. Database (RDS PostgreSQL)

- **PostgreSQL 16+**, `db.t4g.medium` to start; **Multi-AZ in prod**, single-AZ in dev.
- Private subnets only; the security group allows the API and worker only.
- Encrypted with your KMS key; **automated backups for 14–35 days**, point-in-time recovery,
  **deletion protection** on, and a final snapshot on delete.
- Credentials in **Secrets Manager with automatic rotation**, or IAM database authentication.
- The SQLAlchemy models work on PostgreSQL. Generate the first Alembic migration from them; JSON
  columns become `JSONB`.

The database holds metadata only (datasets, runs, users, pipeline versions), so it stays small even with terabytes in S3.

---

## 9. Sign-in (Amazon Cognito)

- One **user pool**, **self sign-up off**: admins invite people, matching how consortia work.
- **MFA required** for reviewers and admins (authenticator app), optional or required for uploaders.
- **Advanced security features** on (compromised-credential checks, adaptive authentication).
- Optional: sign in with your **institution's account** (SAML or OIDC through UK Access Federation
  or Microsoft Entra ID), or ORCID via OIDC. Cognito supports both.
- The website uses the Cognito hosted UI (or Amplify Auth) for sign-in. The API validates the
  **ID/access token** (JWKS signature, issuer, audience, expiry) in place of `auth.verify()`, and
  maps the Cognito `sub` to the `user` table. Project roles stay in the GWASHub database, so the
  permission code (`require_member`, `can_open_dataset`) is unchanged.
- Offboarding: disabling a user in Cognito removes access everywhere. Remove their project memberships too.

---

## 10. The website (S3 + CloudFront)

- `npm run generate` in `web/` produces static files; upload them to the private web bucket.
- **CloudFront** with origin access control, so the bucket is readable only by CloudFront, and:
  - default behaviour → the web bucket;
  - `/api/*` → the load balancer, caching disabled, all headers, cookies and query strings forwarded.
  This keeps the site and the API on one domain, so no CORS and no third-party cookies.
- A **response headers policy**: HSTS, `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options: DENY`, and a **Content-Security-Policy** limited to your domain, Google Fonts
  (or self-host the font) and the S3 bucket hostname (for direct uploads).
- Attach the same WAF (or a CloudFront-scoped one).

---

## 11. Deployment pipeline (GitHub Actions)

On every pull request:
1. `pytest` (all 57 tests), and the web build (`nuxt generate`).
2. Dependency and image scanning (e.g. `pip-audit`, `npm audit`, Trivy).

On merge to `main`:
1. Build and push the images (API, `gwashub/base`, `gwashub/liftover`) to ECR, tagged by commit.
2. `cdk deploy` to **dev**, then smoke tests: sign-in, a small upload, a QC run, a meta-analysis
   (the synthetic data in `devtools/` is ideal for this).

Releases (git tag): `cdk deploy` to **prod** after a manual approval in GitHub Environments.

Pin images by **digest** in the Batch job definitions, so a run always records exactly what it executed.

---

## 12. Cost (rough monthly estimate, eu-west-2, low traffic)

| Item | Approx. per month |
|---|---|
| ECS Fargate API, 2 small tasks | $30–40 |
| Application Load Balancer | $20–25 |
| WAF (web ACL + managed rules) | $10–20 |
| RDS PostgreSQL db.t4g.medium, Multi-AZ | $110–140 |
| NAT gateways (2) + data | $70–90 |
| VPC interface endpoints (~7 × 2 AZs) | $100–110 (fewer AZs or endpoints in dev) |
| GuardDuty, Security Hub, Config, CloudTrail data events | $20–60 (grows with S3 activity) |
| CloudFront, Route 53, Secrets Manager, KMS, CloudWatch | $10–30 |
| **Baseline** | **≈ $250–450** |
| S3 Standard storage | ≈ $24 per TB |
| S3 Glacier Instant Retrieval (old raw uploads) | ≈ $5 per TB |
| QC run on Spot `r7i.2xlarge` (~$0.15–0.25/h), 10–30 min | cents per dataset |
| Downloads to the internet | ≈ $90 per TB (often waived for academic accounts) |

Ways to cut cost: one NAT gateway and single-AZ RDS in dev; fewer interface endpoints in dev;
lifecycle rules; Spot for every Batch job; turning dev off out of hours.

---

## 13. Security checklist before go-live

- [ ] Root user MFA; admins via SSO with MFA; no IAM users or access keys anywhere
- [ ] Service control policies in place; CloudTrail (with S3 data events), GuardDuty (with S3
      malware scanning), Security Hub and Config on in every account
- [ ] All buckets private, KMS-encrypted, TLS-only, versioned, access-logged; Access Analyzer clean
- [ ] RDS private, encrypted, backed up, deletion-protected; credentials rotated
- [ ] Dev login endpoints absent in prod; Cognito invite-only; MFA required for reviewers
- [ ] Every API route enforces project membership server-side (already true, and covered by the API tests); re-test against the deployed stack
- [ ] Presigned URLs short-lived; downloads checked against permissions before signing
- [ ] Batch job roles limited to their own run's S3 prefix; custom steps isolated (§6)
- [ ] WAF on; rate limits; security headers and CSP on CloudFront
- [ ] Images scanned, non-root, pinned by digest; dependencies scanned in CI
- [ ] Alarms: 5xx rate, failed jobs, queue age, budget, GuardDuty findings → email or Slack
- [ ] Restore test: restore RDS to a point in time and recover a deleted S3 object, once, on purpose
- [ ] DPIA / information-governance sign-off; retention and deletion policy written down
- [ ] Independent **penetration test** of the deployed site before real consortium data goes in

---

## 14. Code changes, in order

| # | Change | Where |
|---|---|---|
| 1 | Storage abstraction with an S3 backend; presigned GET for downloads | `api/gwashub_api/storage.py`, `routers/datasets.py`, `routers/meta.py` |
| 2 | Multipart presigned upload endpoints (start / part URLs / complete / abort) and a chunked uploader in the website | `routers/datasets.py`, `web/app/pages/projects/[pid]/upload.vue` |
| 3 | PostgreSQL + Alembic; drop `add_missing_columns` | `api/gwashub_api/db.py`, new `api/migrations/` |
| 4 | Cognito token validation; remove the dev login in prod | `api/gwashub_api/auth.py`, `main.py`, `web/app/composables/useAuth.ts` |
| 5 | Jobs via Batch + EventBridge/SQS + worker, instead of threads | `api/gwashub_api/jobs.py`, new `api/gwashub_api/worker.py` |
| 6 | Batch entrypoint: stage from S3 → `run_pipeline` → publish to S3 | `pipeline/runner/aws/` (new `batch_entry.py`), `images/base/Dockerfile` |
| 7 | Real reference resources (UCSC chain, 1000G/gnomAD panel) | one-off build script + resources bucket |
| 8 | Heavy steps to Polars/DuckDB; meta-analysis per chromosome | `pipeline/qc_steps/*`, `pipeline/analyses/meta_analysis/` |
| 9 | Server-side resource import from URL (as a Batch job) | `routers/pipeline.py` |
| 10 | Stage B: Step Functions per-step images + isolated custom-step environment | `pipeline/runner/`, `infra/` |

Items 1–7 are needed for production. 8 matters once files exceed about 30 million variants or costs
need trimming. 9–10 can follow.

---

## 15. Suggested schedule

| Week | Work |
|---|---|
| 1 | Paperwork (§1); accounts and security baseline (§2); CDK skeleton; Network and Data stacks in dev |
| 2 | Code changes 1–4; API on ECS behind the load balancer; website on CloudFront; Cognito sign-in |
| 3 | Code changes 5–6; Batch environment; end-to-end in dev with the synthetic datasets |
| 4 | Real reference data (7); a pilot with 2–3 real cohorts' files; tune instance sizes and alarms |
| 5 | Prod deployment; restore test; penetration test; information-governance sign-off |
| 6 | Invite the consortium |

---

## 16. "Or similar": alternatives to AWS

- **Google Cloud:** Cloud Storage, Cloud Run (API), Cloud Batch, Cloud SQL and Identity Platform map
  one-to-one onto the design above; the code changes are the same, apart from the SDK.
- **Microsoft Azure:** Blob Storage, Container Apps, Azure Batch, Azure Database for PostgreSQL and
  Entra ID. A good fit if your university already uses Entra ID for sign-in.
- **Institutional research computing or a trusted research environment:** worth asking first. They
  may already provide secure storage, a governance framework and compute at no direct cost. The
  runner's local executor already works on a single large server; Slurm could replace Batch.
- **Not recommended:** a single virtual machine running everything. It's quick to set up, but
  backups, scaling, patching and security all fall on you, and one compromise exposes everything.
