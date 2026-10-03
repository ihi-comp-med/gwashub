"""Step and image catalog, read from the repository.

    pipeline/qc_steps/<owner>/<step>/step.yaml     → step key "<owner>/<step>"   (gwashub = the default steps)
    pipeline/images/<owner>/<image>/Dockerfile     → image "<owner>/<image>"

Consortia add steps and images by pull request (see CONTRIBUTING.md); folders starting with "_" and
the shared `templates/` and `fixtures/` folders are not owners.
"""
import os
from dataclasses import dataclass, field

import yaml

PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pipeline/
REPO_ROOT = os.path.dirname(PIPELINE_DIR)
QC_STEPS_DIR = os.path.join(PIPELINE_DIR, "qc_steps")
IMAGES_DIR = os.path.join(PIPELINE_DIR, "images")
NOT_OWNERS = ("templates", "fixtures")
VALID_ON_FAIL = ("block", "flag", "continue")


@dataclass
class StepDef:
    key: str
    title: str
    description: str
    workdir: str  # directory the command runs relative to (script lives here)
    command: list  # e.g. ["python", "row_check.py"]
    image: str  # "<owner>/<image>", see pipeline/images
    default_on_fail: str = "flag"
    resources: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)  # {"repo", "path"}
    pythonpath: list = field(default_factory=list)
    always_run: bool = False  # run even after the pipeline was blocked (e.g. the report)

    @property
    def owner(self):
        return self.key.split("/", 1)[0]

    @property
    def script(self):
        return os.path.join(self.workdir, self.command[-1])


def _owners(root):
    if not os.path.isdir(root):
        return []
    return [o for o in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, o)) and not o.startswith(("_", ".")) and o not in NOT_OWNERS]


def load_steps(qc_steps_dir=QC_STEPS_DIR):
    """key -> StepDef for every step of every owner."""
    steps = {}
    for owner in _owners(qc_steps_dir):
        for name in sorted(os.listdir(os.path.join(qc_steps_dir, owner))):
            path = os.path.join(qc_steps_dir, owner, name, "step.yaml")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                d = yaml.safe_load(f)
            key = f"{owner}/{d['name']}"
            workdir = os.path.join(qc_steps_dir, owner, name)
            steps[key] = StepDef(
                key=key, title=d["title"], description=d.get("description", ""), workdir=workdir,
                command=list(d["command"]), image=d["image"], default_on_fail=d.get("default_on_fail", "flag"),
                resources=list(d.get("resources") or []), params=dict(d.get("params") or {}),
                pythonpath=[qc_steps_dir], always_run=bool(d.get("always_run")),
                source={"repo": "gwashub", "path": os.path.relpath(os.path.join(workdir, d["command"][-1]), REPO_ROOT)},
            )
    return steps


def load_images(images_dir=IMAGES_DIR):
    """"<owner>/<image>" -> {"ref", "title", "description", "base", "dockerfile"} for every image folder.
    title/description come from an optional image.yaml; base is the Dockerfile's first FROM."""
    images = {}
    for owner in _owners(images_dir):
        for name in sorted(os.listdir(os.path.join(images_dir, owner))):
            folder = os.path.join(images_dir, owner, name)
            dockerfile = os.path.join(folder, "Dockerfile")
            if not os.path.exists(dockerfile):
                continue
            meta = {}
            if os.path.exists(os.path.join(folder, "image.yaml")):
                with open(os.path.join(folder, "image.yaml")) as f:
                    meta = yaml.safe_load(f) or {}
            with open(dockerfile) as f:
                base = next((line.split()[1] for line in f if line.strip().upper().startswith("FROM ")), "")
            ref = f"{owner}/{name}"
            images[ref] = {"ref": ref, "title": meta.get("title", ref), "description": meta.get("description", ""),
                           "base": base, "dockerfile": os.path.relpath(dockerfile, REPO_ROOT)}
    return images


def build_order(images: dict) -> list[str]:
    """Images in an order where each comes after the GWASHub image it is built FROM."""
    done, order = set(), []

    def visit(ref, seen=()):
        if ref in done or ref not in images:
            return
        if ref in seen:
            raise ValueError(f"image {ref} depends on itself")
        parent = images[ref]["base"].split(":")[0].replace("gwashub-images/", "")
        visit(parent, (*seen, ref))
        done.add(ref)
        order.append(ref)
    for ref in sorted(images):
        visit(ref)
    return order
