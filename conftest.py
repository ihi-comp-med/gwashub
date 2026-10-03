import json
import os

import pytest

from devtools.synth import generate


@pytest.fixture(scope="session")
def synth_small(tmp_path_factory):
    """Tiny synthetic genome + 3 datasets (generated once per test session)."""
    out = tmp_path_factory.mktemp("synth")
    manifest = generate(str(out), small=True)
    return {"dir": str(out), "resources": os.path.join(out, "resources"), "manifest": manifest}

