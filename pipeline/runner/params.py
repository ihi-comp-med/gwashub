"""Typed parameters for QC steps (step.yaml) and meta-analysis methods (analysis.yaml).

A `params:` block is a list of fields the website draws as a form and the API validates:

    params:
      - {key: model, label: Model, type: select, options: [fixed, random], default: fixed}
      - {key: maf_min, label: Minimum MAF, type: number, min: 0, max: 0.5, default: 0.01, help: ...}

Types: select, boolean, number, integer, text, range. Optional: min, max, options, help, group.

A range is a filter with its own on/off switch and a minimum and/or maximum, each switched on or off separately
(`sides`: which limits it has, [min], [max] or [min, max]; min/max bound the values). Its value is
{on, min_on, min, max_on, max}; a switched-off limit keeps its number, so switching it on again restores it:

      - {key: info, label: INFO, type: range, sides: [min, max], min: 0, max: 1,
         default: {"on": false, min_on: true, min: 0.8, max_on: false, max: 1}}

Quote "on" in YAML: unquoted, YAML 1.1 reads the key as the boolean true, and range_value refuses it.
The older form, a plain mapping of key → default (`params: {warn_lambda: 1.1}`), still works: types are
inferred from the defaults.
"""
TYPES = ("select", "boolean", "number", "integer", "text", "range")
SIDES = ("min", "max")


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
        if t == "range":
            sides = f.get("sides") or list(SIDES)
            if not sides or any(sd not in SIDES for sd in sides):
                raise ValueError(f"parameter {f['key']}: sides must be min and/or max")
            f = {**f, "sides": sides, "default": range_value(f.get("default"), sides)}
        out.append({**f, "type": t, "label": f.get("label") or f["key"].replace("_", " ").capitalize()})
    return out


def range_value(v, sides) -> dict:
    """A range value with every key present (a limit the field doesn't have is off)."""
    v = dict(v or {})
    if any(isinstance(k, bool) for k in v):  # an unquoted `on:` in YAML is the key True, and would silently read as off
        raise ValueError('a range default needs "on" in quotes in YAML (unquoted, on is read as true)')
    out = {"on": bool(v.get("on", False))}
    for sd in SIDES:
        out[f"{sd}_on"] = bool(v.get(f"{sd}_on", False)) and sd in sides
        out[sd] = v.get(sd)
    return out


def _check_range(v, f) -> dict:
    if not isinstance(v, dict):
        raise ValueError("must be a range ({on, min_on, min, max_on, max})")
    bad = set(v) - {"on", "min_on", "min", "max_on", "max"}
    if bad:
        raise ValueError(f"unknown part {', '.join(sorted(bad))}")
    merged = range_value({**f["default"], **v}, f["sides"])
    if any(not isinstance(v.get(k, False), bool) for k in ("on", "min_on", "max_on")):
        raise ValueError("on, min_on and max_on must be true or false")
    for sd in SIDES:
        if not merged[f"{sd}_on"]:
            continue
        x = merged[sd]
        if x is None or isinstance(x, bool):
            raise ValueError(f"the {sd}imum is switched on, so give it a value")
        x = merged[sd] = float(x)
        if ("min" in f and x < f["min"]) or ("max" in f and x > f["max"]):
            raise ValueError(f"the {sd}imum must be between {f.get('min', '-∞')} and {f.get('max', '∞')}")
    if merged["on"] and merged["min_on"] and merged["max_on"] and merged["min"] > merged["max"]:
        raise ValueError("the minimum is above the maximum")
    return merged


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
            elif t == "range":
                v = _check_range(v, f)
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
