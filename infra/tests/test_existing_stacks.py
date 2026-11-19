"""The three-stack environments (dev, prod) still build: Network, Data and App each turn into a template.

Guards the refactor that moved the reusable parts into gwashub_infra/parts.py (the synthesized templates were
compared before and after, so the existing deployments don't change).
"""
import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from gwashub_infra.app_stack import AppStack
from gwashub_infra.config import ENVIRONMENTS
from gwashub_infra.data import DataStack
from gwashub_infra.network import NetworkStack

INFRA = Path(__file__).resolve().parents[1]

PIPELINE_IMAGES = {
    "gwashub/base": {"digest": "sha256:" + "0" * 64, "platform": "linux/arm64"},
    "gwashub/vcf": {"digest": "sha256:" + "1" * 64, "platform": "linux/amd64"},
}


@pytest.mark.parametrize("name", ["dev", "prod"])
def test_three_stack_environments_synthesize(name):
    cfg = ENVIRONMENTS[name]
    app = cdk.App(context=json.loads((INFRA / "cdk.context.json").read_text()))
    env = cdk.Environment(account=cfg.account, region=cfg.region)
    net = NetworkStack(app, f"Gwashub-{name}-Network", cfg=cfg, env=env)
    data = DataStack(app, f"Gwashub-{name}-Data", cfg=cfg, net=net, env=env)
    app_stack = AppStack(app, f"Gwashub-{name}-App", cfg=cfg, net=net, data=data, image_tag="test",
                         pipeline_images=PIPELINE_IMAGES, env=env)
    for stack in (net, data, app_stack):
        Template.from_stack(stack)
