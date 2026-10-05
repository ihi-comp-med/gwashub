import json
import os

import pytest

from devtools.synth import generate

# Steps run in their images (devtools/build_images.sh) when Docker has them; tests that need an image skip otherwise
# (_lib/testing.needs_image). GWASHUB_STEP_RUNNER=host forces this machine's interpreters.
DEFAULT_IMAGES = ("gwashub/base", "gwashub/base-r", "gwashub/vcf", "gwashub/ldsc")
if "GWASHUB_STEP_RUNNER" not in os.environ:
    from _lib.testing import images_built
    if images_built(*DEFAULT_IMAGES):
        os.environ["GWASHUB_STEP_RUNNER"] = "docker"


@pytest.fixture(scope="session")
def synth_small(tmp_path_factory):
    """Tiny synthetic genome + 3 datasets (generated once per test session)."""
    out = tmp_path_factory.mktemp("synth")
    manifest = generate(str(out), small=True)
    return {"dir": str(out), "resources": os.path.join(out, "resources"), "manifest": manifest}

