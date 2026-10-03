"""Write a bookmarks file with console links to every GWASHub resource, for importing into a browser.

    python devtools/aws_bookmarks.py dev        # → local/aws-bookmarks-dev.html

Chrome: Bookmarks → Bookmark manager → ⋮ → Import bookmarks. Reads infra/cdk-outputs-<env>.json (written
by devtools/aws_cdk_deploy.sh). Open the "Switch to gwashub-<env>" link first in each browser session.
"""
import html
import json
import os
import sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS = {"dev": "203248996074", "prod": "269626333085"}


def links(env: str) -> list[tuple[str, list[tuple[str, str]]]]:
    with open(os.path.join(ROOT, "infra", f"cdk-outputs-{env}.json")) as f:
        out = json.load(f)
    app, data = out[f"Gwashub-{env}-App"], out[f"Gwashub-{env}-Data"]
    bucket = lambda part: next(v for k, v in data.items() if k.startswith(f"ExportsOutputRef{part}"))  # noqa: E731
    r = "eu-west-2"
    c = f"https://{r}.console.aws.amazon.com"
    acct = ACCOUNTS[env]
    logs = quote(quote(app["OutLogGroup"], safe=""), safe="").replace("%", "$")
    stack = lambda name: f"{c}/cloudformation/home?region={r}#/stacks?filteringText=Gwashub-{env}-{name}"  # noqa: E731
    return [
        ("Start here", [
            (f"GWASHub {env} site", app["OutSiteUrl"]),
            (f"Switch to gwashub-{env} (open first)", f"https://signin.aws.amazon.com/switchrole?account={acct}"
                                                      f"&roleName=OrganizationAccountAccessRole&displayName=gwashub-{env}"),
        ]),
        ("Data", [
            ("S3: uploads", f"https://{r}.console.aws.amazon.com/s3/buckets/{bucket('Uploads')}?region={r}&prefix=uploads/"),
            ("S3: QC and meta-analysis results", f"https://{r}.console.aws.amazon.com/s3/buckets/{bucket('Results')}?region={r}"),
            ("S3: reference resources", f"https://{r}.console.aws.amazon.com/s3/buckets/{bucket('Resources')}?region={r}&prefix=resources/"),
            ("RDS: Postgres database", f"{c}/rds/home?region={r}#databases:"),
            ("Secrets: read-only DB password", f"{c}/secretsmanager/secret?name={quote(data['OutDbReadonlySecret'], safe='')}&region={r}"),
        ]),
        ("Jobs", [
            ("Batch: jobs in the queue", f"{c}/batch/home?region={r}#jobs/list"),
            ("Batch: dashboard", f"{c}/batch/home?region={r}#dashboard"),
            ("Batch: job definitions (one per image)", f"{c}/batch/home?region={r}#job-definition"),
            ("ECR: pipeline images", f"{c}/ecr/private-registry/repositories?region={r}"),
        ]),
        ("Running site", [
            ("ECS: API and worker services", f"{c}/ecs/v2/clusters/{app['OutClusterName']}/services?region={r}"),
            ("CloudWatch: logs (API, worker, Batch)", f"{c}/cloudwatch/home?region={r}#logsV2:log-groups/log-group/{logs}"),
            ("Cognito: users", f"{c}/cognito/v2/idp/user-pools/{app['OutUserPoolId']}/user-management/users?region={r}"),
            ("CloudFront: distribution", "https://us-east-1.console.aws.amazon.com/cloudfront/v4/home#/distributions"),
            ("EC2: instances (Batch machines, DB jump host)", f"{c}/ec2/home?region={r}#Instances:"),
        ]),
        ("Deploys and costs", [
            ("CloudFormation: App stack", stack("App")),
            ("CloudFormation: Data stack", stack("Data")),
            ("CloudFormation: Network stack", stack("Network")),
            ("Billing: costs by service", "https://us-east-1.console.aws.amazon.com/costmanagement/home#/cost-explorer"),
        ]),
    ]


def main(env: str = "dev"):
    rows = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>", '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
            "<TITLE>Bookmarks</TITLE>", "<H1>Bookmarks</H1>", "<DL><p>", f"<DT><H3>GWASHub AWS ({env})</H3>", "<DL><p>"]
    for folder, items in links(env):
        rows += [f"<DT><H3>{html.escape(folder)}</H3>", "<DL><p>"]
        rows += [f'<DT><A HREF="{html.escape(url)}">{html.escape(name)}</A>' for name, url in items]
        rows += ["</DL><p>"]
    rows += ["</DL><p>", "</DL><p>"]
    os.makedirs(os.path.join(ROOT, "local"), exist_ok=True)
    path = os.path.join(ROOT, "local", f"aws-bookmarks-{env}.html")
    with open(path, "w") as f:
        f.write("\n".join(rows) + "\n")
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
