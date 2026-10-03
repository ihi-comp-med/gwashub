# GWASHub on AWS: the full deployment

How the AWS version of GWASHub is built, deployed and run. The design and its reasoning are in
`docs/AWS_DEPLOYMENT.md`; this file is the practical guide. It replaces the interim single server
(`docs/AWS_MINIMAL.md`).

## What runs where

| Piece | AWS service | Notes |
|---|---|---|
| Website | S3 + CloudFront | Static files from `nuxt generate`. Address: `https://<id>.cloudfront.net` until a domain is added |
| API | ECS Fargate (Graviton) behind an **internal** load balancer | Reached only through CloudFront (`/api/*`, VPC origin). No public address |
| Worker | ECS Fargate | Reads Batch job events from SQS and records results in the database |
| QC runs and meta-analyses | AWS Batch on EC2 Graviton (m7g/r7g, Spot), scales to zero | One job per *image group* of a run (below); `python -m pipeline.runner.aws.batch_entry s3://.../spec.json --group N` |
| Files | S3: uploads, results, resources buckets | KMS-encrypted, versioned, private, TLS only. Browsers upload straight to S3 in 64 MB parts |
| Metadata | RDS PostgreSQL 16 | Private subnets, encrypted, automated backups, deletion protection. Schema by Alembic (`api/migrations`) |
| Sign-in | Amazon Cognito | Invite-only, password + authenticator app (MFA) for everyone |
| Secrets | Secrets Manager | Database password, session key, Cognito client secret |
| Logs | CloudWatch Logs `/gwashub/<env>/app` | Streams `api/…`, `worker/…`, `batch/…` |

**Images.** The API and worker run `gwashub/app` (ECR), built from `images/app/Dockerfile`. QC steps run in
the pipeline images from `pipeline/images/<owner>/<image>/` (ECR repositories `gwashub-images/<owner>/<image>`):
`gwashub/base` (Python, the default steps) and `gwashub/base-r` (adds R); consortium images build `FROM`
them. Everything is built for arm64 by the deploy script, and each pipeline image gets its own Batch job
definition pinned by digest. Each run's `result.json` records the image digests it used.

**Image groups.** Consecutive steps that use the same image run as one Batch job in that image; a new
job starts only where the image changes, and each job depends on the previous one (Batch job
dependencies). The default pipeline is one job; HCM's (an R step in the middle) is three. Between jobs,
the working file and run state pass through `results/qc/<run>/_state/`.

### How a QC run flows

1. The browser asks the API to start an upload; the API creates the dataset (status *uploading*) and an
   S3 multipart upload, then hands out short-lived presigned URLs for each part.
2. The browser PUTs the parts straight to S3 (4 at a time, with retries) and tells the API it's done.
3. The API writes a job spec to `results/qc/<run>/spec.json` and submits one Batch job per image group.
4. Each job downloads what it needs (input or hand-over state, the pinned reference files), runs its
   steps, and writes outputs and `progress.json` (live step states) to `results/qc/<run>/`; the last
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

## Deploying

Needs Docker running, and the AWS CLI signed in (`aws login --profile gwashub-mgmt`, see
`docs/AWS_MINIMAL.md` for the profile setup; prod needs a `gwashub-prod` profile like the dev one).

```bash
./devtools/aws_cdk_deploy.sh dev     # website build, image build + push, cdk deploy of all three stacks
```

The first deploy takes 25–35 minutes (database and CloudFront); later ones 5–10. The site address
is printed at the end and saved in `infra/cdk-outputs-dev.json` (git-ignored; the admin, tunnel and
bookmarks scripts read it). `GWASHUB_IMAGE_TAG=<tag>` redeploys images already in ECR without rebuilding.

**dev today:** https://d3ap1iypuvmxl8.cloudfront.net (gwashub-dev). The demo projects were seeded with
`seed-demo`; the HCM pipeline (an R step in the middle) runs as three Batch jobs.

## Running it

**In the website** (no AWS access needed):
- **Reviewers** manage their project's members from the Members box on the project page: **Invite**
  (someone new gets a GWASHub account and a Cognito invitation email; someone with an account is just
  added), and per member **Make reviewer/uploader** and **Remove from project**. A project always keeps a reviewer.
- **Platform admins** (users with `is_admin`) create projects with **New project** on the Projects page;
  the creator becomes the first reviewer and the project gets the default pipeline.
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

### Browsing the database from PyCharm

```bash
./devtools/aws_db_tunnel.sh dev        # keeps localhost:5433 → the database open until Ctrl+C; prints the password
```

In PyCharm (Professional): Database → + → Data Source → PostgreSQL, host `localhost`, port `5433`,
database `gwashub`, user `gwashub_readonly`, SSL mode `require`. The tunnel goes through a small jump
host (`gwashub-dev-db-jump`, t4g.nano, no open ports) with Session Manager, so it needs
`brew install --cask session-manager-plugin` and your AWS sign-in. The `gwashub_readonly` user can only
read; it's (re)created with `./devtools/aws_admin.sh dev db-readonly-user`, password in Secrets Manager
`gwashub/dev/db-readonly`.

### Where to look in the console (account gwashub-dev, London)

`.venv/bin/python devtools/aws_bookmarks.py dev` writes `local/aws-bookmarks-dev.html`, a bookmarks file
(import it in Chrome's Bookmark manager) with direct links to everything below. Console links need you
switched into the account: Switch role, account `203248996074`, role `OrganizationAccountAccessRole`.
The AWS Organizations pages only work from the management account.

| To see | Go to |
|---|---|
| Uploaded files | S3 → bucket `gwashub-dev-data-uploads…` → `uploads/<project>/<dataset id>/` |
| QC outputs | S3 → `gwashub-dev-data-results…` → `qc/<run id>/` (`result.json`, `gwas_qc.tsv`, `steps/`) |
| Metadata (database) | PyCharm / DataGrip / psql through the tunnel above (the RDS console has no data browser for plain Postgres) |
| QC jobs | Batch → Jobs → queue `gwashub-dev` |
| Logs | CloudWatch → Log groups → `/gwashub/dev/app` |
| Users | Cognito → User pools → `gwashub-dev` |

## Not done yet (in order of importance)

- **WAF** on CloudFront (managed rules + rate limiting). Not in the lean dev setup; add before prod.
- **Custom domain** with an ACM certificate (CloudFront's default certificate allows old TLS versions).
- **Isolating consortium steps**: they run in their own image group, but in the same compute environment
  with the same job role as the default steps. A locked-down environment (no internet, only that run's
  S3 prefix) is still to do. Consortium code is reviewed by pull request before it runs.
- **Malware scanning** of uploads (GuardDuty Malware Protection for S3), S3 access logs and CloudTrail
  data events for the buckets.
- **Alarms**: API 5xx, failed jobs, dead-letter queue messages, budget.
- **Real reference data** (UCSC chain file, 1000 Genomes / gnomAD panel): the dev environment uses the synthetic ones.
- **CI/CD** with GitHub Actions and OIDC; deploys run from a laptop for now.
- **Cognito Plus** (compromised-credential checks, adaptive authentication) costs extra per user; Essentials is used.
- Read-only root filesystem for the API and worker containers (needs a scratch volume owned by the app user).
- Batch jobs get 2 vCPU / 8 GB. Large real files will need more; set per job from the file size.
