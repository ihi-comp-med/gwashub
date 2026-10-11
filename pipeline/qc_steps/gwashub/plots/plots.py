"""Manhattan and QQ plots (PNG) plus a downsampled JSON the web app draws interactively.

Contract
--------
The step's description is in step.yaml in this folder (it has no user-facing parameters; gws and max_points below
can be set under "step" in a pipeline). The contract every QC step follows is in CONTRIBUTING.md, section 2.

    python plots.py --input working.tsv --resources DIR --params params.json --log step_log.json

Inputs
  --input       GWASHub format (chr, pos, p used; rsid when present). Read only.
  --resources   Not used.
  --params      JSON. Only "step" is read: gws (the dashed line, default 5e-8) and max_points (default 6000).
  Environment   GWASHUB_ARTIFACT_DIR: where the plots go (the log's folder when unset).

Outputs
  --input       Unchanged.
  --log         {"step": "plots", "verdict": "pass", "metrics": {"points_plotted"}, ...}. Always passes.
  Artifacts     manhattan.png, qq.png, plot_data.json (the structure is in _lib/plotting.py make_plots)

The drawing is shared with the meta-analysis post-processing (_lib/plotting.py), so a QC plot and a meta-analysis
plot of the same data look the same. Chromosome Y is not drawn; X is drawn after 22.
"""
import os

from _lib.common import artifact_dir, load_params, parse_args, read_working, step_params, write_log
from _lib.plotting import make_plots

DEFAULTS = {"gws": 5e-8, "max_points": 6000}


def main():
    args = parse_args()
    opts = step_params(load_params(args.params), DEFAULTS)
    adir = artifact_dir() or os.path.dirname(args.log)
    _, n = make_plots(read_working(args.input), adir, gws=opts["gws"], max_points=opts["max_points"])
    write_log(args.log, "plots", "pass", {"points_plotted": n}, ["Manhattan and QQ plots generated."], [
        {"type": "image", "path": "manhattan.png", "label": "Manhattan plot"},
        {"type": "image", "path": "qq.png", "label": "QQ plot"},
        {"type": "data", "path": "plot_data.json", "label": "Plot data"},
    ])


if __name__ == "__main__":
    main()
