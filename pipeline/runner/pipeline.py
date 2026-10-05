"""Pipeline specs: an ordered list of entries (step key + optional image / on_fail / params)."""
from dataclasses import dataclass, field

import yaml

from pipeline.runner.catalog import VALID_ON_FAIL, job_compute


@dataclass
class Entry:
    step: str
    on_fail: str = None
    image: str = None
    params: dict = field(default_factory=dict)


@dataclass
class ResolvedEntry:
    index: int
    step: object  # StepDef
    on_fail: str
    image: str
    params: dict

    @property
    def name(self):
        return self.step.key.split("/")[-1]


def entries_from_dicts(items):
    return [Entry(step=i["step"], on_fail=i.get("on_fail"), image=i.get("image"), params=dict(i.get("params") or {}))
            for i in items]


def load_template(path):
    with open(path) as f:
        d = yaml.safe_load(f)
    return entries_from_dicts(d["steps"])


def resolve(entries, catalog, images=None):
    """images: the known image refs (pipeline/images); when given, every step's image must be one of them."""
    if not entries:
        raise ValueError("pipeline has no steps")
    out = []
    for i, e in enumerate(entries):
        if e.step not in catalog:
            raise ValueError(f"unknown step {e.step!r}")
        sd = catalog[e.step]
        on_fail = e.on_fail or sd.default_on_fail
        if on_fail not in VALID_ON_FAIL:
            raise ValueError(f"invalid on_fail {on_fail!r} for {e.step}; must be one of {VALID_ON_FAIL}")
        image = e.image or sd.image
        if images is not None and image not in images:
            raise ValueError(f"unknown image {image!r} for {e.step}")
        out.append(ResolvedEntry(i, sd, on_fail, image, {**sd.params, **e.params}))
    return out


def plan_groups(resolved):
    """Split a pipeline into groups of consecutive steps that use the same image. On AWS each group is
    one Batch job in that image, so the default pipeline (all gwashub/base) is a single job and a new
    job starts only where the image changes. Each group's compute is the largest any of its steps asks for
    (step.yaml `compute:`). Returns [{"image": ..., "steps": [indexes], "compute": {"vcpus", "memory_gb"}}]."""
    groups, members = [], []
    for e in resolved:
        if groups and groups[-1]["image"] == e.image:
            groups[-1]["steps"].append(e.index)
            members[-1].append(e.step)
        else:
            groups.append({"image": e.image, "steps": [e.index]})
            members.append([e.step])
    for g, steps in zip(groups, members):
        g["compute"] = job_compute(steps)
    return groups
