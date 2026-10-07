# GWASHub on AWS: the full deployment

How the AWS version of GWASHub is built, deployed and run, why it's built that way, and what's left before
production. The AWS accounts themselves are described in `docs/AWS_ACCOUNT_SETUP.md`.

## Why it's built this way

- **Files never pass through the API.** Browsers upload straight to S3 in 64 MB parts with presigned URLs, and
  downloads are presigned links, so multi-GB files don't tie up the API or hit timeouts. S3 objects can be 5 TB.
- **Batch on EC2, not Fargate, for jobs.** A harmonised GWAS takes about 265 bytes per variant in memory, so a
  20-million-variant file is ~5 GB per copy and 20 to 40 GB at peak; EC2 gives more memory and disk per dollar. Spot,
  scaling to zero, so idle costs nothing.
- **Jobs survive deploys.** The API submits Batch jobs and returns; a separate worker records results from Batch
  events (EventBridge → SQS), so restarts lose nothing.
- **One domain.** CloudFront serves the website and forwards `/api/*` to an internal load balancer, so there's no CORS,
  no third-party cookies and no public address for the API.
- **Tokens stay server-side.** The API does the Cognito code exchange and keeps a short-lived session cookie.
- **Separate accounts** for dev and prod (and audit, log archive), so dev mistakes can't touch production data.
- **Region:** eu-west-2 (London).

## What runs where

| Piece | AWS service | Notes |
|---|---|---|
| Website | S3 + CloudFront | Static files from `nuxt generate`. Address: `https://<id>.cloudfront.net` until a domain is added |
| API | ECS Fargate (Graviton) behind an **internal** load balancer | Reached only through CloudFront (`/api/*`, VPC origin). No public address |
| Worker | ECS Fargate | Reads Batch job events from SQS and records results in the database |
| QC runs and meta-analyses | AWS Batch on EC2 Spot, scales to zero: Graviton (m7g/r7g) for arm64 images, queue `gwashub-<env>`; Intel (m7i/r7i) for x86-64 images, queue `gwashub-<env>-x86`. Jobs get a 200 GB (dev) / 500 GB (prod) encrypted volume and a 12 h timeout | One job per *image group* of a run (below); `python -m pipeline.runner.aws.batch_entry s3://.../spec.json --group N` |
| Files | S3: uploads, results, resources buckets | KMS-encrypted, versioned, private, TLS only. Browsers upload straight to S3 in 64 MB parts |
| Metadata | RDS PostgreSQL 16 | Private subnets, encrypted, automated backups, deletion protection. Schema by Alembic (`api/migrations`) |
| Sign-in | Amazon Cognito | Invite-only, password + authenticator app (MFA) for everyone |
| Secrets | Secrets Manager | Database password, session key, Cognito client secret |
| Logs | CloudWatch Logs `/gwashub/<env>/app` | Streams `api/…`, `worker/…`, `batch/…` |

**Images.** The API and worker run `gwashub/app` (ECR), built from `images/app/Dockerfile`. QC steps run in
the pipeline images from `pipeline/images/<owner>/<image>/` (ECR repositories `gwashub-images/<owner>/<image>`):
`gwashub/base` (Python, PLINK 2, bcftools), `gwashub/base-r` (adds R), `gwashub/vcf` (gwas2vcf + bcftools
liftover; x86-64), `gwashub/ldsc` (the authors' LDSC, Python 2.7; x86-64), and `gwashub/gwama` and `gwashub/metal`
(the meta-analysis programs, compiled from pinned, checksummed releases); consortium images build `FROM` them. A
meta-analysis runs as one Batch job in its method's image (`analysis.yaml`). The deploy script builds each image for
the platform in its `image.yaml` (arm64 unless it says `linux/amd64`), and each pipeline image gets its own Batch job
definition pinned by digest, on the queue for its platform. Each run's `result.json` records the image digests it used.
ECR keeps the 5 newest images per repository and drops untagged ones after a day (lifecycle policy, set by the
deploy script); an older image a past run used can be rebuilt from git at that commit.

**Image groups.** Consecutive steps that use the same image run as one Batch job in that image; a new
job starts only where the image changes, and each job depends on the previous one (Batch job
dependencies). The default pipeline is four jobs (`gwashub/base-r`, `gwashub/vcf`, `gwashub/ldsc`, `gwashub/base`).
Each job is sized from its steps' `compute:` in `step.yaml` (the largest vCPU and memory among them), passed to Batch
as overrides. Between jobs, the working file and run state pass through `results/qc/<project>/<dataset>/<run>-<time>/_state/`.

### How a QC run flows

1. The browser asks the API to start an upload; the API creates the dataset (status *uploading*) and an
   S3 multipart upload, then hands out short-lived presigned URLs for each part.
2. The browser PUTs the parts straight to S3 (4 at a time, with retries) and tells the API it's done.
3. The API writes a job spec to `results/qc/<project>/<dataset>/<run>-<time>/spec.json` and submits one Batch job per image group.
4. Each job downloads what it needs (input or hand-over state, the pinned reference files), runs its
   steps, and writes outputs and `progress.json` (live step states) to `results/qc/<project>/<dataset>/<run>-<time>/`; the last
   writes `result.json`.
5. Batch's state changes go through EventBridge to SQS; the worker records the run when its last job
   succeeds (or as failed when any job fails). Every 5 minutes it also asks Batch about unfinished runs.
6. Downloads are presigned S3 links handed out after the API checks permissions.

Code: `api/gwashub_api/storage.py` (files), `jobs.py` (specs, submission, recording results),
`worker.py`, `routers/cognito.py` (sign-in), `pipeline/runner/jobspec.py` (what a job does). Locally the
same code runs with a folder for storage, SQLite and threads for jobs.

## Infrastructure as code

`infra/` is a CDK (Python) app with three stacks per environment:

| Stack | Contains |
|---|---|
| `Gwashub-<env>-Network` | VPC (2 AZs), NAT gateway(s), S3 gateway endpoint, security groups |
| `Gwashub-<env>-Data` | KMS key, the three file buckets, Postgres. Everything here is kept if the stack is deleted |
| `Gwashub-<env>-App` | Cognito, ECS cluster, API and worker services, internal load balancer, Batch, SQS + EventBridge, web bucket, CloudFront |

Sizing per environment is in `infra/gwashub_infra/config.py`, as is the banner shown across the top of
every page ("Development build" in dev, none in prod). **dev** (gwashub-dev, 203248996074) is
the lean setup: one NAT gateway, single-AZ `db.t4g.small`, one API task. **prod** (gwashub-prod,
269626333085) has Multi-AZ `db.t4g.medium`, two NAT gateways, two API tasks and longer backups.

## One-off setup on your Mac

1. **AWS CLI v2.32 or later** (`brew install awscli`; it has `aws login`, which reuses your console sign-in,
   so no access keys are stored), **Docker Desktop**, and for database browsing the **Session Manager
   plugin** (`brew install --cask session-manager-plugin`).
2. If your shell aliases `aws` to something else (this Mac's `~/.zshrc` aliases it to an SSH command),
   rename the alias or type `/opt/homebrew/bin/aws`. The scripts run in bash and aren't affected.
3. Profiles in `~/.aws/config` (each environment's account is entered with the role Organizations created in it):
   ```ini
   [profile gwashub-mgmt]
   region = eu-west-2

   [profile gwashub-dev]
   region = eu-west-2
   role_arn = arn:aws:iam::203248996074:role/OrganizationAccountAccessRole
   source_profile = gwashub-mgmt
   role_session_name = gwashub-deploy

   [profile gwashub-prod]
   region = eu-west-2
   role_arn = arn:aws:iam::269626333085:role/OrganizationAccountAccessRole
   source_profile = gwashub-mgmt
   role_session_name = gwashub-deploy
   ```
4. Sign in (again whenever it expires, after a few hours), with the **browser in the management
   account**, not switched into dev or prod (`aws login` copies the browser's current session):
   ```bash
   aws login --profile gwashub-mgmt --region eu-west-2
   aws sts get-caller-identity --profile gwashub-dev     # should show account 203248996074
   ```

## Deploying

Needs Docker running and the AWS CLI signed in (above).

```bash
./devtools/aws_cdk_deploy.sh dev     # website build, image build + push, cdk deploy of all three stacks
```

The first deploy takes 25–35 minutes (database and CloudFront); later ones 5–10. The site address
is printed at the end and saved in `infra/cdk-outputs-dev.json` (git-ignored; the admin, tunnel and
bookmarks scripts read it). `GWASHUB_IMAGE_TAG=<tag>` redeploys images already in ECR without rebuilding.

**dev today:** https://d3ap1iypuvmxl8.cloudfront.net (gwashub-dev). Projects for testing and validation, the Practice
project, no demo data, and all 37 real reference files (incl. dbSNP 157) registered as shared resources.

## Running it

**In the website** (no AWS access needed):
- **Reviewers** manage their project's members from the Members box on the project page: **Invite**
  (someone new gets a GWASHub account and a Cognito invitation email; someone with an account is just
  added), and per member **Make reviewer/uploader** and **Remove from project**. A project always keeps a reviewer.
- **Platform admins** (users with `is_admin`) create projects with **New project** on the Projects page;
  the creator becomes the first reviewer and the project gets the default pipeline. They can also delete a project
  (Overview → Delete project), which removes its records and all its files; for other reviewer actions they need a
  reviewer role in the project.
- **Everyone** can learn the site with the guided tours (Help menu, top right) in the **Practice project**, which the
  API creates at start-up (every user a reviewer, synthetic files to download; `GWASHUB_PRACTICE=0` turns it off).
- **Bulk upload:** members download the upload script and make an API token on the Account page. The script sends the
  token in an `X-GWASHub-Token` header, not `Authorization`: CloudFront drops `Authorization` from GET requests on the
  uncached `/api/*` behaviour. Files go straight to S3 as in the website.
- Cognito's built-in email allows about 50 invitations a day; switch Cognito to Amazon SES with your own
  sender address before inviting a whole consortium.

**Admin commands** run as one-off ECS tasks with the API's settings (no SSH, no extra plugin). Use them
for platform admins, bulk changes and fixes:

```bash
./devtools/aws_admin.sh dev users
./devtools/aws_admin.sh dev invite someone@uni.ac.uk "Full Name" --institution "University of X" [--admin]
./devtools/aws_admin.sh dev add-member someone@uni.ac.uk hermes3 reviewer      # or uploader
./devtools/aws_admin.sh dev remove-member someone@uni.ac.uk hermes3
./devtools/aws_admin.sh dev add-project lvsys "LV systolic function GWAS" LVSF --phenotype LVEF
./devtools/aws_admin.sh dev seed-demo      # demo projects + synthetic reference files (empty database only)
./devtools/aws_admin.sh dev invite someone@uni.ac.uk "Full Name" --resend     # invitation email again
./devtools/aws_admin.sh dev db-readonly-user  # (re)create the read-only database user for browsing
```

`invite` creates the GWASHub user and the Cognito account. Cognito emails a temporary password; at
first sign-in the person sets their own password and registers an authenticator app. To remove
someone completely, disable them in Cognito (console: Cognito → User pools → gwashub-dev → Users) and
remove their project memberships.

**Real reference files** (tens of GB, too big for the website's upload dialog) go up from your Mac and are then
registered as shared resources, described by their `resources/catalog.yaml` entries:

```bash
python devtools/aws_upload_resources.py dev            # every file in local/resources/BUILT.tsv; skips files already there
./devtools/aws_admin.sh dev add-resources s3://…/resources/shared/_manifests/<time>.json   # printed by the line above
```

Files land at `resources/shared/<key>/<sha256 prefix>/<key>`; a file whose sha256 matches the latest version is not
registered again. The manifest stays in the bucket, so after a database reset the same `add-resources` line
registers them again without uploading anything.

### Browsing the database from PyCharm

The database has no public address, so you open a private tunnel from your Mac and point PyCharm at it.
The tunnel goes through a small jump host (`gwashub-dev-db-jump`, t4g.nano, no open ports) using AWS
Session Manager and your AWS sign-in. You connect as `gwashub_readonly`, which can read everything and
change nothing.

One-off: `brew install --cask session-manager-plugin` (in a normal Terminal; it asks for your Mac password).

**1. Open the tunnel** (in Terminal; leave it running while you use PyCharm):

```bash
/opt/homebrew/bin/aws login --profile gwashub-mgmt --region eu-west-2   # if your sign-in has expired
cd ~/git/gwashub
./devtools/aws_db_tunnel.sh dev        # prints the password, then "Waiting for connections..."
```

**2. Connect PyCharm** (Professional; Community has no Database window, use DBeaver or TablePlus instead):
Database window → **+ → Data Source → PostgreSQL** (plain PostgreSQL, not "AWS JDBC Wrapper").

| Tab | Setting |
|---|---|
| General | Host `localhost`, port **`5433`**, user `gwashub_readonly`, password (from the script), database `gwashub` |
| SSH/SSL | **Use SSL** ticked, mode **Require**; *Use SSH tunnel* unticked (the script is the tunnel) |

Download the driver if offered, **Test Connection**, then expand **gwashub → schemas → public → tables**
(if no tables show, tick the `public` schema in the data source's Schemas setting). The Terminal prints
"Connection accepted" each time PyCharm connects. `user` is a reserved word in Postgres: write `"user"` in SQL.

**Lost the password?** It's in Secrets Manager, `gwashub/dev/db-readonly` (console: Retrieve secret value), or:
`/opt/homebrew/bin/aws secretsmanager get-secret-value --secret-id gwashub/dev/db-readonly --profile gwashub-dev --region eu-west-2 --query SecretString --output text`

**If it doesn't connect:**

| Symptom | Cause and fix |
|---|---|
| PyCharm: "Connection to localhost:5433 refused" | The tunnel isn't running: start it (step 1) and wait for "Waiting for connections..." |
| PyCharm: refused on **5432** | Port must be 5433 |
| Script: "session has expired" / "Unable to locate credentials" | Run the `aws login` line |
| Script: `AccessDenied ... OrganizationAccountAccessRole` after `aws login` asked to overwrite the profile with an account **203248996074** session | `aws login` copies the browser's current session, and the browser was switched into gwashub-dev. Switch the browser **back** to the management account, run `aws login` again and accept the **367722595572** session |
| Script: "Install the Session Manager plugin first" | `brew install --cask session-manager-plugin` |
| PyCharm: "password authentication failed" | Re-copy the password (see above) |
| `aws` runs SSH instead of the AWS CLI | Your shell aliases `aws`; use `/opt/homebrew/bin/aws` (the scripts aren't affected) |

The read-only user is (re)created with `./devtools/aws_admin.sh dev db-readonly-user`.

### Where to look in the console (account gwashub-dev, London)

`.venv/bin/python devtools/aws_bookmarks.py dev` writes `local/aws-bookmarks-dev.html`, a bookmarks file
(import it in Chrome's Bookmark manager) with direct links to everything below. Console links need you
switched into the account: Switch role, account `203248996074`, role `OrganizationAccountAccessRole`.
The AWS Organizations pages only work from the management account.

| To see | Go to |
|---|---|
| Uploaded files | S3 → bucket `gwashub-dev-data-uploads…` → `uploads/<project>/<dataset id>-<time>/` |
| QC outputs | S3 → `gwashub-dev-data-results…` → `qc/<project>/<dataset id>/<run id>-<time>/` (`result.json`, `gwas_qc.tsv`, `steps/`); meta-analyses in `meta/<project>/<id>-<time>/` |
| A user's problem | Ask them for the **reference** at the bottom of the dataset panel or meta-analysis page (copy button): it names the project, dataset, run and the exact folder |
| Metadata (database) | PyCharm / DataGrip / psql through the tunnel above (the RDS console has no data browser for plain Postgres) |
| QC jobs | Batch → Jobs → queue `gwashub-dev` |
| Logs | CloudWatch → Log groups → `/gwashub/dev/app` |
| Users | Cognito → User pools → `gwashub-dev` |

## Before production

Open items, most important first:

- **WAF** on CloudFront (AWS managed rules, a rate limit per IP). Not in the lean dev setup.
- **Custom domain** with an ACM certificate (CloudFront's default certificate allows old TLS versions).
- **Isolating consortium steps**: they run in their own image group, but in the same compute environment with the same
  job role as the default steps. Still to do: a locked-down environment (no internet, only that run's S3 prefix).
  Consortium code is reviewed by pull request before it runs.
- **Malware scanning** of uploads (GuardDuty Malware Protection for S3), S3 access logs, CloudTrail data events for
  the buckets.
- **Alarms**: API 5xx, failed jobs, dead-letter queue messages, budget, GuardDuty findings.
- **CI/CD** with GitHub Actions and OIDC (no stored keys); tests and the web build on every pull request, prod deploys
  after a manual approval. Deploys run from a laptop for now.
- **Cognito email through SES** with our own sender before inviting a consortium (the built-in email allows ~50 a day).
- Read-only root filesystem for the API and worker containers (needs a scratch volume owned by the app user).
- Cognito Plus (compromised-credential checks, adaptive authentication) costs extra per user; Essentials is used.

Checks before real consortium data goes in:

- [ ] Root MFA on every account; admins sign in with MFA; no IAM users or access keys (see `docs/AWS_ACCOUNT_SETUP.md`)
- [ ] Buckets private, KMS-encrypted, TLS-only, versioned (done); access logs; IAM Access Analyzer clean
- [ ] Database private, encrypted, backed up, deletion-protected (done); credential rotation
- [ ] The dev login (`/api/auth/login`, `/api/auth/users`) unreachable in prod; re-run the permission tests against the
      deployed stack
- [ ] WAF, rate limits, security headers and a Content-Security-Policy on CloudFront
- [ ] Images scanned and pinned by digest (pinned: done); dependency scanning in CI
- [ ] Restore test, once, on purpose: the database to a point in time, and a deleted S3 object
- [ ] Information-governance sign-off (DPIA if needed, data region, retention and deletion policy)
- [ ] An independent penetration test of the deployed site

## Costs

- **dev:** about $8.50 for the first five days of October 2026, covered by credits. The fixed part is mostly the NAT
  gateway, the database and the load balancer; Batch costs nothing when idle.
- **prod as designed** (Multi-AZ database, two NAT gateways, two API tasks, WAF, security services): roughly $250 to
  $450 a month at low traffic, plus S3 (~$24 per TB a month) and downloads (~$90 per TB, often waived for academic
  accounts). A QC run on Spot costs cents.
- ECR keeps the 5 newest images per repository; Batch instance types are limited to large to 4xlarge.
- Cheaper options discussed, not decided: research credits; a serverless redesign (Lambda, Aurora Serverless v2
  scaling to zero, no NAT); fck-nat instead of a NAT gateway; switching dev off out of hours; single-AZ prod database.
