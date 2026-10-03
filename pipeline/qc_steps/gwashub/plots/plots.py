"""Manhattan and QQ plots (PNG) plus a downsampled JSON the web app draws interactively."""
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
