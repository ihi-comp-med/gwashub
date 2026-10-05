"""resources/catalog.yaml records every reference file a step uses, and each entry's build script exists."""
import os

import yaml

from pipeline.runner.catalog import REPO_ROOT, load_steps

RESOURCES = os.path.join(REPO_ROOT, "resources")


def catalog():
    with open(os.path.join(RESOURCES, "catalog.yaml")) as f:
        return yaml.safe_load(f)["resources"]


def test_every_step_resource_is_catalogued():
    keys = [k for entry in catalog() for k in entry["keys"]]
    assert len(keys) == len(set(keys)), "a key is listed twice"
    declared = {k: sd.key for sd in load_steps().values() for k in sd.all_resources}
    missing = {k: step for k, step in declared.items() if k not in keys}
    assert not missing, f"resources used by steps but not in resources/catalog.yaml: {missing}"


def test_catalogue_entries_are_complete():
    for entry in catalog():
        for field in ("keys", "title", "build", "used_by", "source", "version", "script"):
            assert entry.get(field), (entry.get("keys"), field)
        assert entry["build"] in ("GRCh37", "GRCh38", "Any"), (entry["keys"], entry["build"])  # Resource.build, the website's options
        if entry["script"] != "none":  # e.g. files only the synthetic data has
            script = os.path.join(RESOURCES, "build", entry["script"])
            assert os.path.exists(script) and os.access(script, os.X_OK), script
