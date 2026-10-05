"""Step and image catalog, read from the repository.

    pipeline/qc_steps/<owner>/<step>/step.yaml                       → step key "<owner>/<step>" (gwashub = defaults)
    pipeline/analyses/meta_analysis/<owner>/<method>/analysis.yaml   → meta-analysis method "<owner>/<method>"
    pipeline/images/<owner>/<image>/Dockerfile                       → image "<owner>/<image>"

Consortia add steps and images by pull request (see CONTRIBUTING.md); folders starting with "_" and
the shared `templates/` folder are not owners.
"""
import os
from dataclasses import dataclass, field

import yaml

from pipeline.runner import params as P

PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pipeline/
REPO_ROOT = os.path.dirname(PIPELINE_DIR)
QC_STEPS_DIR = os.path.join(PIPELINE_DIR, "qc_steps")
IMAGES_DIR = os.path.join(PIPELINE_DIR, "images")
META_ANALYSIS_DIR = os.path.join(PIPELINE_DIR, "analyses", "meta_analysis")
NOT_OWNERS = ("templates",)
VALID_ON_FAIL = ("block", "flag", "continue")
DEFAULT_PLATFORM = "linux/arm64"  # AWS Graviton; an image.yaml may say linux/amd64
# What a Batch job gets unless its steps ask for more (step.yaml `compute:`); a job takes the largest request
# among its steps.
DEFAULT_COMPUTE = {"vcpus": 2, "memory_gb": 8}
# The most one job can ask for: the largest Batch instances are 4xlarge (16 vCPU, 128 GiB; infra/gwashub_infra/
# app_stack.py), less what ECS keeps for itself. A bigger request would never start.
MAX_COMPUTE = {"vcpus": 16, "memory_gb": 120}


@dataclass
class StepDef:
    key: str
    title: str
    description: str
    workdir: str  # directory the command runs relative to (script lives here)
    command: list  # e.g. ["Rscript", "parse.R"]
    image: str  # "<owner>/<image>", see pipeline/images
    default_on_fail: str = "flag"
    resources: list = field(default_factory=list)  # required: the run fails without them
    optional_resources: list = field(default_factory=list)  # staged when available; the step copes without them
    params: dict = field(default_factory=dict)  # key -> default
    param_schema: list = field(default_factory=list)  # typed fields (pipeline/runner/params.py)
    source: dict = field(default_factory=dict)  # {"repo", "path"}
    pythonpath: list = field(default_factory=list)
    always_run: bool = False  # run even after the pipeline was blocked (e.g. the report)
    compute: dict = field(default_factory=dict)  # {"vcpus", "memory_gb"} this step needs on AWS Batch

    @property
    def owner(self):
        return self.key.split("/", 1)[0]

    @property
    def all_resources(self):
        return [*self.resources, *self.optional_resources]

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
            schema = P.normalise(d.get("params"))
            steps[key] = StepDef(
                key=key, title=d["title"], description=d.get("description", ""), workdir=workdir,
                command=list(d["command"]), image=d["image"], default_on_fail=d.get("default_on_fail", "flag"),
                resources=list(d.get("resources") or []), optional_resources=list(d.get("optional_resources") or []),
                params=P.defaults(schema), param_schema=schema,
                pythonpath=[qc_steps_dir], always_run=bool(d.get("always_run")), compute=_compute(d.get("compute"), path),
                source={"repo": "gwashub", "path": os.path.relpath(os.path.join(workdir, d["command"][-1]), REPO_ROOT)},
            )
    return steps


def _compute(c, path):
    c = dict(c or {})
    unknown = set(c) - set(DEFAULT_COMPUTE)
    if unknown or any(not isinstance(v, (int, float)) or v <= 0 for v in c.values()):
        raise ValueError(f"{path}: compute takes positive vcpus and memory_gb only")
    over = [k for k, v in c.items() if v > MAX_COMPUTE[k]]
    if over:
        raise ValueError(f"{path}: compute asks for more than one job can have ({', '.join(f'{k} {MAX_COMPUTE[k]}' for k in over)})")
    return c


def job_compute(steps):
    """Compute for one Batch job running these StepDefs: the largest request of each kind."""
    out = dict(DEFAULT_COMPUTE)
    for sd in steps:
        for k, v in (sd.compute or {}).items():
            out[k] = max(out[k], v)
    return out


def load_images(images_dir=IMAGES_DIR):
    """"<owner>/<image>" -> {"ref", "title", "description", "base", "platform", "dockerfile"} for every image folder.
    title/description/platform come from an optional image.yaml; base is the Dockerfile's first FROM."""
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
                froms = [line.split()[1] for line in f if line.strip().upper().startswith("FROM ")]
            ref = f"{owner}/{name}"
            # A multi-stage Dockerfile builds in a throwaway stage; the image is FROM its last stage's base.
            images[ref] = {"ref": ref, "title": meta.get("title", ref), "description": meta.get("description", ""),
                           "base": froms[-1] if froms else "", "froms": froms, "platform": meta.get("platform", DEFAULT_PLATFORM),
                           "dockerfile": os.path.relpath(dockerfile, REPO_ROOT)}
    return images


def build_order(images: dict) -> list[str]:
    """Images in an order where each comes after the GWASHub image it is built FROM."""
    done, order = set(), []

    def visit(ref, seen=()):
        if ref in done or ref not in images:
            return
        if ref in seen:
            raise ValueError(f"image {ref} depends on itself")
        for parent in images[ref].get("froms") or [images[ref]["base"]]:
            visit(parent.split(":")[0], (*seen, ref))
        done.add(ref)
        order.append(ref)
    for ref in sorted(images):
        visit(ref)
    return order


@dataclass
class AnalysisDef:
    """A meta-analysis method: pipeline/analyses/meta_analysis/<owner>/<method>/analysis.yaml."""
    key: str
    title: str
    description: str
    workdir: str
    command: list
    image: str
    param_schema: list = field(default_factory=list)
    reference: str = ""  # citation shown with the method
    source: dict = field(default_factory=dict)

    @property
    def owner(self):
        return self.key.split("/", 1)[0]


def load_analyses(root=META_ANALYSIS_DIR):
    """key -> AnalysisDef for every meta-analysis method of every owner."""
    out = {}
    for owner in _owners(root):
        for name in sorted(os.listdir(os.path.join(root, owner))):
            path = os.path.join(root, owner, name, "analysis.yaml")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                d = yaml.safe_load(f)
            key = f"{owner}/{d['name']}"
            workdir = os.path.join(root, owner, name)
            out[key] = AnalysisDef(
                key=key, title=d["title"], description=d.get("description", ""), workdir=workdir,
                command=list(d["command"]), image=d["image"], param_schema=P.normalise(d.get("params")),
                reference=d.get("reference", ""),
                source={"repo": "gwashub", "path": os.path.relpath(os.path.join(workdir, d["command"][-1]), REPO_ROOT)})
    return out
