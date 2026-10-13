"""sgc/check_build: the build declared at upload against the data (real GRCh37 SNPs, conftest.py)."""
from _lib.testing import needs_image, run_step

IMAGE = "sgc/mungesumstats"


@needs_image(IMAGE)
def test_check_build_passes_the_right_build_and_fails_the_wrong_one(tmp_path, grch37_file):
    for d in ("ok", "wrong", "bases"):
        (tmp_path / d).mkdir()
    # With rsIDs: MungeSumstats' own inference.
    log, _ = run_step("sgc/check_build", grch37_file, {"build": "GRCh37"}, tmp_path / "ok")
    assert log["verdict"] == "pass" and log["metrics"]["inferred"] == "GRCh37" and "MungeSumstats" in log["metrics"]["method"]
    log, _ = run_step("sgc/check_build", grch37_file, {"build": "GRCh38"}, tmp_path / "wrong")
    assert log["verdict"] == "fail" and "not GRCh38 as declared" in log["messages"][0]
    # Without rsIDs: reference bases of both builds.
    no_rsid = grch37_file.drop(columns="rsid")
    log, _ = run_step("sgc/check_build", no_rsid, {"build": "GRCh38"}, tmp_path / "bases")
    m = log["metrics"]
    assert log["verdict"] == "fail" and m["inferred"] == "GRCh37" and m["match_GRCh37"] > 0.95 > 0.7 > m["match_GRCh38"]
