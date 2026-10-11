# Road map to production

What has to happen before consortium data goes into GWASHub, in order, with how to do each task and what it adds to
the bill. The deployment itself is described in `docs/AWS_FULL.md`, the AWS accounts in `docs/AWS_ACCOUNT_SETUP.md`.

## Where things stand (11 October 2026)

- **Built and validated.** The site is feature-complete for now, and all four validation tests pass on gwashub-dev
  (`docs/VALIDATION_PLAN.md`, the Validation page).
- **dev is live** at https://gwashub.com (account gwashub-dev). It is the only environment deployed.
- **prod is configured, not deployed** (account gwashub-prod, settings in `infra/gwashub_infra/config.py`).
- The site's address is `https://gwashub.com`; `www.gwashub.com` and the CloudFront address redirect there.

## The domain plan

Today the apex serves dev. At go-live:

| Address | Serves | Account |
|---|---|---|
| `gwashub.com` | prod | gwashub-prod |
| `www.gwashub.com` | prod, redirecting to `gwashub.com` (sign-in returns only to one address) | gwashub-prod |
| `dev.gwashub.com` | dev | gwashub-dev (a delegated subdomain zone) |

The domain is registered in the management account; the hosted zone belongs in the account that serves the apex.

## Phases

Effort: **S** under a day, **M** one to three days, **L** a week or more. Cost: approximate London list prices per
month (October 2026), from dev's own bill where it has the item.

### Phase 1: decisions (before building anything else)

| # | Task | Why | How | Effort | Cost |
|---|---|---|---|---|---|
| 1.1 | Information-governance sign-off | Summary statistics are aggregate data, but cohorts and institutions may require a DPIA, a data region and a retention policy | With the institution's data protection office: DPIA if they want one, confirm UK (eu-west-2) storage, agree how long uploads and results are kept and how a cohort's data is deleted | M (mostly waiting) | none |
| 1.2 | Who pays, and credits | prod has fixed costs of about $300 a month | Agree the billing owner; apply for research credits; ask the institution about an AWS agreement and the data egress waiver | S | none |
| 1.3 | Support and ownership | Someone answers users and gets the alarms | Name two people for alarms and support; a shared role mailbox for AWS account emails and alarms | S | none |

### Phase 2: changes in the code (done in this repository, tested on dev)

| # | Task | Why | How | Effort | Cost |
|---|---|---|---|---|---|
| 2.1 | Domains per environment | prod gets the apex; dev moves to `dev.gwashub.com` | `config.py`: `domain="gwashub.com", www=True` for prod; `domain="dev.gwashub.com", www=False` for dev. (`www` and the redirect to the apex are built: `www=True` adds `www.<domain>` to the certificate, CloudFront and DNS, and the website's CloudFront function redirects any other host to the domain) | S | $0.50 a zone |
| 2.2 | WAF on CloudFront | Blocks common attacks and floods before they reach the API | `app_stack.py`: a web ACL (in us-east-1, like the certificate) with the AWS managed rule groups (common, known bad inputs, IP reputation) and a rate limit per IP, attached to the distribution | M | about $12 |
| 2.3 | Malware scanning of uploads | Uploads come from many institutions | GuardDuty Malware Protection for S3 on the uploads bucket; QC refuses a file tagged as infected | M | $5 to $30, by volume scanned |
| 2.4 | S3 access logs and CloudTrail data events | Who read or changed which file | A logging bucket; access logging on the three data buckets; data events for them on the organisation trail | S | $2 to $5 |
| 2.5 | Alarms | Failures must reach a person | SNS topic emailing the people from 1.3; alarms on API 5xx, failed Batch jobs, dead-letter queue messages, database CPU and storage, budget, GuardDuty findings | M | about $3 |
| 2.6 | Content-Security-Policy | Limits what a malicious script could do in the browser | Add a CSP to the CloudFront response headers policy (`app_stack.py`); check every page still works | S | none |
| 2.7 | Read-only root filesystem for the API and worker | Smaller blast radius if a container is compromised | `readonly_root_filesystem=True` in the task definitions, with a scratch volume owned by the app user for temporary files | S | none |
| 2.8 | Isolate consortium steps | Consortium code runs with the same rights as the default steps | A separate Batch compute environment and job role for consortium image groups: no internet, access only to its run's S3 prefix | L | none (same Spot pricing) |
| 2.9 | Cognito email through SES | Cognito's own email sends about 50 invitations a day | Verify the domain in SES, request production access, point the user pool's email at SES with a `no-reply@gwashub.com` sender | S (plus AWS's approval time) | under $1 |
| 2.10 | Prove the dev login is unreachable | `/api/auth/login` must never work in prod | A test in the deploy script (or CI) that calls it on the deployed site and fails the deploy if it answers | S | none |

### Phase 3: accounts and automation

| # | Task | Why | How | Effort | Cost |
|---|---|---|---|---|---|
| 3.1 | Finish the account baseline | Open items in `docs/AWS_ACCOUNT_SETUP.md` | Budgets and Cost Anomaly Detection emailing two people; AWS Config and Security Hub standards; GuardDuty in the other active regions; IAM Identity Center for dev and prod; the CloudTrail bucket moved to gwashub-log-archive | M | $10 to $25 (Config, Security Hub) |
| 3.2 | CI/CD | Deploys from a laptop don't scale or audit well | GitHub Actions with OIDC (no stored keys): tests and the web build on every pull request; deploy dev on merge to `main`; deploy prod on a tagged release after a manual approval | M | none (GitHub's free minutes) |
| 3.3 | Dependency and image scanning | Known vulnerabilities in the images | ECR scan on push is on; add Dependabot (Python, npm, GitHub Actions) and fail CI on critical findings | S | none |

### Phase 4: deploy prod

1. **Sign in** to the management account (`aws login --profile gwashub-mgmt --region eu-west-2`).
2. **Move the DNS.** Create a `gwashub.com` hosted zone in gwashub-prod and copy any records you need into it (none
   yet, besides the ones the deploy makes). Point the registered domain's name servers at it (management account:
   Route 53 → Registered domains → gwashub.com → Edit name servers). In gwashub-dev, create a `dev.gwashub.com` zone,
   then add an NS record for `dev` in the prod zone with its four name servers.
3. **Deploy:** `./devtools/aws_cdk_deploy.sh prod`. The first deploy takes 25 to 35 minutes, then
   `./devtools/aws_cdk_deploy.sh dev` to move dev to its new address.
4. **Reference files:** `python devtools/aws_upload_resources.py prod`, then the `add-resources` line it prints (37 files,
   about 24 GB including dbSNP 157; see "Running it" in `docs/AWS_FULL.md`).
5. **First people and projects:** `./devtools/aws_admin.sh prod invite <email> "<name>" --admin` for the platform
   admins, who then create projects in the website and invite reviewers.
6. **Smoke test:** sign in with MFA, take a Practice tour, upload a practice file, run QC, run a meta-analysis,
   download a result.

### Phase 5: go-live checks

- [ ] Root MFA on every account; admins sign in with MFA; no IAM users or access keys
- [ ] Buckets private, KMS-encrypted, TLS-only, versioned (done); access logs on (2.4); IAM Access Analyzer clean
- [ ] Database private, encrypted, backed up, deletion-protected (done); credential rotation on
- [ ] The dev login unreachable on prod (2.10); the API's permission tests re-run against the deployed stack
- [ ] WAF, rate limit, security headers and CSP on CloudFront (2.2, 2.6)
- [ ] Images scanned and pinned by digest (pinned: done); dependency scanning in CI (3.3)
- [ ] Restore test, once, on purpose: the database to a point in time, and a deleted S3 object
- [ ] Alarms reach the named people (2.5): trigger one on purpose
- [ ] Validation re-run on prod: at least test 1 (GWAMA defaults); all four if prod's images differ from dev's
- [ ] Information-governance sign-off (1.1)
- [ ] An independent penetration test of the deployed site (external; typically £5,000 to £15,000, one-off)

### Phase 6: running it

| How often | What |
|---|---|
| Weekly | Look at failed QC runs and the dead-letter queue; answer support requests |
| Monthly | Cost Explorer by service against the budget; GuardDuty and Security Hub findings; rebuild and deploy the images for security updates |
| Each release | Merge to `main` (dev deploys), check dev, tag a release (prod deploys after approval) |
| Yearly | Restore test; review who has access; renew the domain (auto-renew on) |

## Costs

### dev, measured

Usage before credits, 1 to 10 October 2026 (about seven days billed), from Cost Explorer:

| Item | 7 days | Per month |
|---|---|---|
| NAT gateway | $8.45 | $37 |
| Database (`db.t4g.small`, single-AZ) | $6.03 | $26 |
| API and worker (Fargate, Arm) | $5.73 | $25 |
| Load balancer | $4.47 | $19 |
| QC and meta-analysis compute (Batch on Spot) | about $2.70 | varies with use |
| Public IPv4, database jump host, disks, secrets, keys, S3 | about $3 | $13 |
| **Total** | **$30.33** | **about $120 fixed, plus compute** |

All the validation work so far (about 30 QC runs and 12 meta-analyses on the validation files) cost under $3 of compute.

### prod, estimated

**AWS Pricing Calculator estimate (11 October 2026):**
https://calculator.aws/#/estimate?id=839531ca98d8b7c4c2ce0371716ae55a8fa7c9de. It comes to **$456.95 a month,
$5,483.40 for 12 months**, Europe (London), on-demand prices before credits. It assumes a busy month during a data
freeze: 100 full-size QC runs, 10 meta-analyses and about 500 datasets (2.1 TB) stored. This is the estimate submitted
with the AWS Cloud Credit for Research application.

| Group | Services in the estimate | Per month |
|---|---|---|
| Core platform | VPC (two NAT gateways as one Regional NAT gateway across 2 AZs, 4 public IPv4), RDS PostgreSQL db.t4g.medium Multi-AZ, Fargate ARM (API 2 × 0.5 vCPU / 1 GB, worker 0.25 vCPU / 0.5 GB), Application Load Balancer | $263 |
| Security and audit | WAF (1 web ACL, 1 rule, 3 managed groups), GuardDuty (with S3 malware scanning, 100 GB), Config, Security Hub, CloudTrail S3 data events | $72 |
| Storage and delivery | S3 Standard 2,100 GB, CloudFront (within the free tier), data transfer out 200 GB | $60 |
| QC and meta-analysis compute | EC2 Spot on Batch: r7i.2xlarge 100 h, r7g.2xlarge 55 h, m7i.xlarge 25 h, m7g.large 25 h, each with its share of the 500 GB job disk; the t4g.nano jump host | $52 |
| Operations | CloudWatch, Secrets Manager, KMS, Route 53, ECR | $9 |

The public calculator can't express Spot for part of a month: each Batch row is entered as 1 instance running all
month, with the Spot discount raised so the cost equals the real hours at AWS's historical Spot discount (discount
entered = 100 - (100 - historical) × hours / 730), and its disk scaled to 500 GB × hours / 730. The calculator
subtracts no free tier itself for CloudFront and data transfer: their free allowances were taken off by hand.

The figures below are the earlier, rougher estimate, kept for the item-by-item reasoning.

Fixed, per month, before credits:

| Item | Per month |
|---|---|
| Two NAT gateways and their addresses | $80 |
| Database (`db.t4g.medium`, Multi-AZ, 30-day backups) | $110 |
| Two API tasks and the worker | $40 |
| Load balancer | $22 |
| WAF | $12 |
| Security and logging (GuardDuty, malware scanning, Config, Security Hub, data events, alarms, logs kept a year) | $30 to $60 |
| DNS, secrets, keys, jump host | $10 |
| **Fixed total** | **about $300 to $330** |

Variable:

| Item | Price | Example |
|---|---|---|
| Storage (S3) | $0.024 per GB a month | 500 datasets at about 4 GB each (upload, QC outputs, kept versions): 2 TB, $50 a month |
| A QC run | $0.25 to $0.40 for a full-size file at historical Spot prices (10 to 20 million variants; mostly the harmonisation machine and its disk) | 500 runs: $125 to $200, once |
| A meta-analysis | $0.10 to $0.50 | |
| Downloads | about $0.09 per GB after the first 100 GB a month (often waived for academic accounts) | 1 TB downloaded: $80 |
| Website traffic (CloudFront) | free up to 1 TB a month | |

**Expect about $350 to $460 a month** once a consortium is using it (the calculator estimate above is $457 in a busy month), plus one-off QC compute as cohorts upload. With
dev kept running, add about $120.

One-off costs: the penetration test, the domain (about $14 a year), staff time for phases 1 to 5.

Not included: Cognito Plus (compromised-credential checks, adaptive authentication), charged per active user; the
site uses Cognito Essentials, free up to 10,000 active users a month.

### Ways to spend less (not decided)

| Option | Saves | Trade-off |
|---|---|---|
| Research credits | everything they cover | applications take time |
| fck-nat (a small instance) instead of NAT gateways | about $60 a month in prod, $30 in dev | we maintain it; a single instance is a single point of failure |
| Single-AZ prod database | about $55 a month | an AZ outage stops the site until it's restored |
| Turn dev off out of hours, or delete dev between releases | up to $120 a month | slower to pick up again |
| Smaller dev API task (0.25 vCPU) | about $7 a month | slower responses under load |
| Serverless redesign (Lambda, Aurora Serverless v2, no NAT) | most of the fixed cost | a large rewrite |
