#!/usr/bin/env python3
"""GWASHub on AWS (see docs/AWS_DEPLOYMENT.md). Deployed with devtools/aws_cdk_deploy.sh.

    npx aws-cdk deploy --all -c env=dev -c image_tag=<tag>

One set of three stacks per environment: Network (VPC), Data (KMS, S3, Postgres) and App (sign-in,
API, worker, Batch, website).
"""
import json

import aws_cdk as cdk

from gwashub_infra.app_stack import AppStack
from gwashub_infra.config import ENVIRONMENTS
from gwashub_infra.data import DataStack
from gwashub_infra.network import NetworkStack

app = cdk.App()
name = app.node.try_get_context("env") or "dev"
cfg = ENVIRONMENTS[name]
image_tag = app.node.try_get_context("image_tag")
images = app.node.try_get_context("pipeline_images")  # JSON {"gwashub/base": "sha256:...", ...}
if not image_tag or not images:
    raise SystemExit("Pass -c image_tag=<tag> -c pipeline_images=<json> (devtools/aws_cdk_deploy.sh does this)")
pipeline_images = json.loads(images) if isinstance(images, str) else images
env = cdk.Environment(account=cfg.account, region=cfg.region)

net = NetworkStack(app, f"Gwashub-{name}-Network", cfg=cfg, env=env)
data = DataStack(app, f"Gwashub-{name}-Data", cfg=cfg, net=net, env=env)
AppStack(app, f"Gwashub-{name}-App", cfg=cfg, net=net, data=data, image_tag=image_tag, pipeline_images=pipeline_images, env=env)
for stack in app.node.children:
    cdk.Tags.of(stack).add("project", "gwashub")
    cdk.Tags.of(stack).add("environment", name)
app.synth()
