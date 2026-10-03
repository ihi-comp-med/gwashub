# Pilot site on AWS (interim single server)

> **Superseded (2026-10-03).** The full deployment (`docs/AWS_FULL.md`) replaced this server. It is
> still running in gwashub-dev (stack `gwashub`, instance `gwashub`, about $60 a month) until you remove it:
> `aws cloudformation delete-stack --stack-name gwashub --profile gwashub-dev --region eu-west-2`
> (its data volume is kept as a snapshot; delete that and the `gwashub/app` ECR images it used separately if
> you don't need them). The **"One-off setup on your Mac"** section below still applies to the full deployment.

The smallest setup that puts GWASHub online for the team, with synthetic data. It is a stopgap:
`docs/AWS_DEPLOYMENT.md` describes the real design (S3, Postgres, Cognito, Fargate, Batch) that
replaces it. Do not upload real cohort data to this server.

## What gets created

One CloudFormation stack (`infra/minimal.yaml`, stack name `gwashub`) in **gwashub-dev
(203248996074), London (eu-west-2)**:

| Resource | Notes |
|---|---|
| VPC with one public subnet | No NAT, no private subnets |
| EC2 `t4g.large` (Graviton, Amazon Linux 2023) | Runs two containers: `app` and `caddy` |
| Elastic IP | Site address is `https://<ip-with-dashes>.sslip.io` unless a domain is given |
| Encrypted 100 GiB gp3 volume at `/data` | SQLite DB, uploads, QC outputs, Caddy certificates. Snapshot kept if the stack is deleted |
| Security group | Ports 80 and 443 only. No SSH: admin access is SSM Session Manager |
| Secrets Manager secret | Session-cookie signing key (`GWASHUB_SECRET`) |
| SSM parameter `/gwashub/image` | Which app image the server runs |
| CloudWatch log group `/gwashub/app` | Container logs, 90 days |
| ECR repository `gwashub/app` | Created by the deploy script, immutable tags, scan on push |

Rough cost: about $50 a month for the instance and $8 for the volume, plus a few dollars of everything else.

The `app` container (`images/app/Dockerfile`) holds the API, the QC runner and the built website,
all on port 8000 behind Caddy, which gets a Let's Encrypt certificate. On first boot it generates the
synthetic data and seeds the demo projects (`images/app/entrypoint.sh`). Sign-in is by password
(`GWASHUB_AUTH=password`); the dev user picker and `/api/auth/users` return 404.

## One-off setup on your Mac

1. `~/.zshrc` line 52 aliases `aws` to an SSH command. The deploy script runs in bash, so it isn't
   affected, but rename the alias (e.g. `aws-bitnami`) so `aws` works in your own terminal.
2. Sign the CLI in with your console session (no stored keys):
   ```bash
   /opt/homebrew/bin/aws login --profile gwashub-mgmt --region eu-west-2
   ```
3. Add the gwashub-dev profile to `~/.aws/config` (it assumes the role Organizations created in the account):
   ```ini
   [profile gwashub-dev]
   region = eu-west-2
   role_arn = arn:aws:iam::203248996074:role/OrganizationAccountAccessRole
   source_profile = gwashub-mgmt
   role_session_name = gwashub-deploy
   ```
4. Check: `aws sts get-caller-identity --profile gwashub-dev` shows account 203248996074.

## Deploy and update

Docker must be running. From the repo root:

```bash
./devtools/aws_deploy.sh                                   # profile gwashub-dev, eu-west-2
GWASHUB_DOMAIN=gwashub.example.org ./devtools/aws_deploy.sh # once a DNS A record points at the Elastic IP
```

The script builds the image for arm64, pushes it to ECR with a timestamp tag, creates or updates the stack,
and on an existing server runs `gwashub-update` through SSM to pull the new image. The first deploy takes
about 10 minutes; the site answers about 5 minutes after the stack finishes.

## Operating it

```bash
aws ssm start-session --target <InstanceId> --profile gwashub-dev   # shell on the server (needs the Session Manager plugin)
sudo docker exec -it app python -m gwashub_api.admin users
sudo docker exec -it app python -m gwashub_api.admin set-password demo@gwashub.org
sudo docker exec -it app python -m gwashub_api.admin add-user EMAIL "Full Name" --institution X
sudo docker exec -it app python -m gwashub_api.admin add-member EMAIL hermes3 uploader
sudo /usr/local/bin/gwashub-update                                  # re-pull and restart
```

Logs: CloudWatch, log group `/gwashub/app` (streams `app` and `caddy`).

Where the data is: all on the `gwashub-data` volume, mounted at `/data/gwashub` on the server
(`/data` inside the app container). None of it is browsable in the AWS console; use a Session Manager shell.

| What | Path on the server |
|---|---|
| Uploaded files | `/data/gwashub/app/storage/uploads/<project>/<dataset id>/` |
| QC and meta-analysis outputs | `/data/gwashub/app/runs/qc/<run id>/`, `/data/gwashub/app/runs/meta/<id>/` |
| Database (users, projects, dataset metadata, reviews, audit trail) | `/data/gwashub/app/gwashub.db` (SQLite) |

## Deferred (come back to these)

- **No backups** apart from the snapshot taken when the stack is deleted. Add a daily Data Lifecycle
  Manager snapshot policy for the `gwashub-data` volume before anyone relies on the data.
- **Sessions:** the cookie is a signed user id with no expiry, so signing out doesn't revoke it and
  rotating `GWASHUB_SECRET` signs everyone out. There's no rate limit on `/api/auth/login`. Both go away with Cognito.
- **sslip.io address:** fine for a pilot. Use a real domain (`GWASHUB_DOMAIN`) before sharing outside the team.
- **Single server:** SQLite, jobs in threads, everything on one disk. If the instance fails, the
  site is down until it's replaced. The full design moves to S3, RDS Postgres, Fargate and Batch.
- **No WAF and no CloudFront.**
- **FastAPI's `/docs` page is public.** It only describes the API, but hide it in production.
- **Security Hub** standards are off (they need AWS Config). GuardDuty in the management account is
  still to be switched on in Stockholm.
- No CI: deploys run from a laptop. GitHub OIDC deploys come with the full design.
