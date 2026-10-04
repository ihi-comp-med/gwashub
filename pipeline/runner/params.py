"""Typed parameters for QC steps (step.yaml) and meta-analysis methods (analysis.yaml).

A `params:` block is a list of fields the website draws as a form and the API validates:

    params:
      - {key: model, label: Model, type: select, options: [fixed, random], default: fixed}
      - {key: maf_min, label: Minimum MAF, type: number, min: 0, max: 0.5, default: 0.01, help: ...}

Types: select, boolean, number, integer, text. Optional: min, max, options, help, group.
The older form, a plain mapping of key → default (`params: {warn_lambda: 1.1}`), still works: types are
inferred from the defaults.
"""
TYPES = ("select", "boolean", "number", "integer", "text")


def _infer(key, default):
    t = "boolean" if isinstance(default, bool) else "integer" if isinstance(default, int) else \
        "number" if isinstance(default, float) else "text"
    return {"key": key, "label": key.replace("_", " ").capitalize(), "type": t, "default": default}


def normalise(raw) -> list[dict]:
    """The params block as a list of field dicts (accepts the list form or the older mapping form)."""
    if not raw:
        return []
    if isinstance(raw, dict):
        return [_infer(k, v) for k, v in raw.items()]
    out = []
    for f in raw:
        if not isinstance(f, dict) or "key" not in f:
            raise ValueError(f"each parameter needs a key: {f!r}")
        t = f.get("type") or _infer(f["key"], f.get("default"))["type"]
        if t not in TYPES:
            raise ValueError(f"parameter {f['key']}: type must be one of {', '.join(TYPES)}")
        if t == "select" and not f.get("options"):
            raise ValueError(f"parameter {f['key']}: a select needs options")
        out.append({**f, "type": t, "label": f.get("label") or f["key"].replace("_", " ").capitalize()})
    return out


def defaults(schema: list[dict]) -> dict:
    return {f["key"]: f.get("default") for f in schema}


def validate(values: dict | None, schema: list[dict], where: str = "") -> dict:
    """Checked values for these fields (defaults for anything not given). Raises ValueError listing problems;
    unknown keys are errors so typos don't go unnoticed."""
    values = values or {}
    known = {f["key"]: f for f in schema}
    errors = [f"unknown parameter '{k}'" for k in values if k not in known]
    out = {}
    for key, f in known.items():
        v = values.get(key, f.get("default"))
        if v is None:
            out[key] = None
            continue
        t, label = f["type"], f["label"]
        try:
            if t == "boolean":
                if not isinstance(v, bool):
                    raise ValueError("must be true or false")
            elif t in ("number", "integer"):
                if isinstance(v, bool):
                    raise ValueError("must be a number")
                v = float(v)
                if t == "integer":
                    if v != int(v):
                        raise ValueError("must be a whole number")
                    v = int(v)
                if ("min" in f and v < f["min"]) or ("max" in f and v > f["max"]):
                    raise ValueError(f"must be between {f.get('min', '-∞')} and {f.get('max', '∞')}")
            elif t == "select":
                if v not in f["options"]:
                    raise ValueError(f"must be one of {', '.join(map(str, f['options']))}")
            else:
                v = str(v)
                if len(v) > 500:
                    raise ValueError("is too long")
        except (TypeError, ValueError) as e:
            errors.append(f"{label}: {e}")
            continue
        out[key] = v
    if errors:
        raise ValueError((f"{where}: " if where else "") + "; ".join(errors))
    return out
