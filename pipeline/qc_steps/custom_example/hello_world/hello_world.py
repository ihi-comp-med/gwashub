"""Example consortium step: hello world.

Follows the step contract (CONTRIBUTING.md): reads --input/--params, writes a step log with a verdict,
puts extra files in $GWASHUB_ARTIFACT_DIR, exits 0. It doesn't change --input.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, Polygon  # noqa: E402

from _lib.common import artifact_dir, load_params, parse_args, step_params, write_log  # noqa: E402


def draw_cat(text: str, path: str):
    """A cartoon cat with a speech bubble."""
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=120)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 9)
    ax.set_aspect("equal")
    ax.axis("off")
    fur, dark, pink = "#f2a541", "#3a2a1a", "#f4a6b8"
    ax.add_patch(Ellipse((3.6, 2.2), 4.2, 3.2, color=fur))                        # body
    ax.plot([5.4, 6.6, 6.9], [1.6, 2.4, 3.6], color=fur, lw=10, solid_capstyle="round")  # tail
    ax.add_patch(Circle((3.6, 4.8), 1.9, color=fur))                              # head
    for x in (2.2, 5.0):                                                          # ears
        side = -1 if x < 3.6 else 1
        ax.add_patch(Polygon([[x - 0.6, 5.9], [x + 0.6, 5.9], [x + side * 0.3, 7.4]], color=fur))
        ax.add_patch(Polygon([[x - 0.3, 6.0], [x + 0.3, 6.0], [x + side * 0.2, 7.0]], color=pink))
    for x in (2.9, 4.3):                                                          # eyes
        ax.add_patch(Ellipse((x, 5.1), 0.55, 0.75, color="white"))
        ax.add_patch(Ellipse((x + 0.05, 5.05), 0.25, 0.5, color=dark))
    ax.add_patch(Polygon([[3.4, 4.45], [3.8, 4.45], [3.6, 4.2]], color=pink))     # nose
    ax.plot([3.6, 3.6], [4.2, 4.0], color=dark, lw=1.5)                           # smile
    ax.plot([3.2, 3.4, 3.6, 3.8, 4.0], [4.05, 3.82, 4.0, 3.82, 4.05], color=dark, lw=1.5)
    for dy in (-0.15, 0.15):                                                      # whiskers
        ax.plot([1.6, 2.9], [4.2 + dy * 2, 4.15 + dy], color=dark, lw=1)
        ax.plot([4.3, 5.6], [4.15 + dy, 4.2 + dy * 2], color=dark, lw=1)
    for x in (2.6, 4.6):                                                          # paws
        ax.add_patch(Ellipse((x, 0.75), 1.0, 0.6, color=fur, ec="#d98a2b"))
    ax.add_patch(Polygon([[6.9, 6.2], [7.8, 5.9], [5.6, 5.2]], fc="white", ec=dark, lw=2, joinstyle="round"))  # bubble tail
    ax.add_patch(FancyBboxPatch((6.3, 5.6), 5.2, 2.2, boxstyle="round,pad=0.25", fc="white", ec=dark, lw=2))  # bubble
    ax.text(8.9, 6.7, text, ha="center", va="center", fontsize=20, fontweight="bold", color=dark)
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    args = parse_args()
    params = load_params(args.params)
    greeting = str(step_params(params, {"greeting": "hello world"})["greeting"])
    print(greeting, flush=True)  # appears under "Console output" for the step
    out = artifact_dir() or os.path.dirname(os.path.abspath(args.log))  # the runner always sets the artifact dir
    draw_cat(greeting, os.path.join(out, "hello_cat.png"))
    write_log(args.log, "hello_world", "pass", metrics={"greeting_length": len(greeting)},
              messages=[f"The step says: {greeting}"],
              artifacts=[{"type": "image", "path": "hello_cat.png", "label": "A cat saying hello"}])


if __name__ == "__main__":
    main()
