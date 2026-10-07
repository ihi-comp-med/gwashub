"""Every element a guided tour points at (web/app/utils/tours.ts) must be marked in the website (data-tour="…"), and
every page it opens must exist, so tours can't quietly break when a page changes."""
import glob
import os
import re

WEB = os.path.join(os.path.dirname(__file__), "..", "..", "web", "app")


def test_tour_targets_exist_in_the_website():
    tours = open(os.path.join(WEB, "utils", "tours.ts")).read()
    targets = set(re.findall(r"\bel: '([a-z0-9-]+)'", tours))
    pages = set(re.findall(r"\bpage: (?:`\$\{P\}([^`]*)`|P\b|'([^']+)')", tours))
    assert len(targets) > 20 and len(re.findall(r"\bid: '", tours)) == 6
    marked = set()
    for f in glob.glob(os.path.join(WEB, "**", "*.vue"), recursive=True):
        marked |= set(re.findall(r'data-tour="([a-z0-9-]+)"', open(f).read()))
    assert not targets - marked, f"tour steps point at elements no page marks: {sorted(targets - marked)}"
    for sub, plain in pages:
        path = plain or f"/projects/[pid]{sub}"
        page = os.path.join(WEB, "pages", path.strip("/") + ".vue")
        index = os.path.join(WEB, "pages", path.strip("/"), "index.vue")
        assert os.path.exists(page) or os.path.exists(index), f"tour page {path} doesn't exist"
