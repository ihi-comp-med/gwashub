#!/usr/bin/env python3
"""GWASHub on AWS (see docs/AWS_FULL.md). Deployed with devtools/aws_cdk_deploy.sh.

    npx aws-cdk deploy --all -c env=dev -c image_tag=<tag>

One set of three stacks per environment: Network (VPC), Data (KMS, S3, Postgres) and App (sign-in,
API, worker, Batch, website).

`-c hibernate=1` (devtools/aws_hibernate.sh) removes everything that costs money while idle and keeps the data:
see "Hibernating" in docs/AWS_FULL.md.

An environment named in SHARED_HOSTS (gwashub_infra/config.py) is the other kind of target, GWASHub on a host, a MySQL
server and a web proxy that already exist. It is one stack, `Gwashub-<name>-Shared`, and needs only
`-c env=<name> -c pipeline_images=<json>` (no image tag: the host runs a release, not a container image).
"""
import json

import aws_cdk as cdk

from gwashub_infra.app_stack import AppStack
from gwashub_infra.config import ENVIRONMENTS, SHARED_HOSTS
from gwashub_infra.data import DataStack
from gwashub_infra.network import NetworkStack
from gwashub_infra.shared_host_stack import SharedHostStack

app = cdk.App()
name = app.node.try_get_context("env") or "dev"
if name not in ENVIRONMENTS and name not in SHARED_HOSTS:
    raise SystemExit(f"Unknown environment {name!r}. Three-stack environments: {', '.join(ENVIRONMENTS)}. "
                     f"Shared-host environments: {', '.join(SHARED_HOSTS) or 'none'}.")
image_tag = app.node.try_get_context("image_tag")
images = app.node.try_get_context("pipeline_images")  # JSON {"gwashub/base": "sha256:...", ...}
if name in SHARED_HOSTS:
    if not images:
        raise SystemExit("Pass -c pipeline_images=<json> (devtools/shared_host_deploy.sh does this)")
    host = SHARED_HOSTS[name]
    SharedHostStack(app, f"Gwashub-{name}-Shared", cfg=host, pipeline_images=json.loads(images) if isinstance(images, str) else images,
                    env=cdk.Environment(account=host.account, region=host.region))
else:
    cfg = ENVIRONMENTS[name]
    if not image_tag or not images:
        raise SystemExit("Pass -c image_tag=<tag> -c pipeline_images=<json> (devtools/aws_cdk_deploy.sh does this)")
    pipeline_images = json.loads(images) if isinstance(images, str) else images
    hibernate = str(app.node.try_get_context("hibernate") or "").lower() in ("1", "true", "yes")
    env = cdk.Environment(account=cfg.account, region=cfg.region)

    net = NetworkStack(app, f"Gwashub-{name}-Network", cfg=cfg, hibernate=hibernate, env=env)
    data = DataStack(app, f"Gwashub-{name}-Data", cfg=cfg, net=net, hibernate=hibernate, env=env)
    AppStack(app, f"Gwashub-{name}-App", cfg=cfg, net=net, data=data, image_tag=image_tag, pipeline_images=pipeline_images,
             hibernate=hibernate, env=env)
for stack in app.node.children:
    cdk.Tags.of(stack).add("project", "gwashub")
    cdk.Tags.of(stack).add("environment", name)
app.synth()
