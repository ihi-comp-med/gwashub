import gzip

import pytest

import gwashub_upload as g


def write(path, text):
    path.write_text(text)
    return str(path)


def test_manifest_must_be_xlsx(tmp_path):
    with pytest.raises(g.Fail, match="give the .xlsx manifest"):
        g.read_manifest(None, "p", write(tmp_path / "m.tsv", "file\n"))


def test_headers_like_the_site_reads_them(tmp_path):
    assert g.read_header(write(tmp_path / "t.tsv", "CHR\tBP\tA1\n1\t2\tA\n")) == ["CHR", "BP", "A1"]
    assert g.read_header(write(tmp_path / "c.csv", "CHR,BP,A1\n1,2,A\n")) == ["CHR", "BP", "A1"]
    assert g.read_header(write(tmp_path / "s.txt", "CHR  BP A1\n")) == ["CHR", "BP", "A1"]
    with gzip.open(tmp_path / "z.tsv.gz", "wt") as f:
        f.write("chromosome\tbase_pair_location\n1\t2\n")
    assert g.read_header(str(tmp_path / "z.tsv.gz")) == ["chromosome", "base_pair_location"]


def test_rows_for_the_check(tmp_path):
    """Files are found relative to the manifest's folder (or by absolute path); problems reading them are reported."""
    write(tmp_path / "a.tsv", "CHR\tBP\n1\t2\n")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub" / "b.tsv", "chrom,pos,x\n1,2,3\n")
    m = str(tmp_path / "manifest.xlsx")
    rows = [(2, {"file": "a.tsv", "sha256": "0" * 64}), (3, {"file": "missing.tsv"}),
            (4, {"file": str(tmp_path / "sub" / "b.tsv")}), (5, {"file": ""})]
    out = g.check_rows(m, rows, verify_sha=True)
    assert out[0]["header"] == ["CHR", "BP"] and out[0]["file_error"] == "its sha256 doesn't match the manifest's"
    assert out[1]["file_error"] == "not found" and out[1]["header"] is None
    assert out[2]["header"] == ["chrom", "pos", "x"] and out[2]["file_error"] is None
    assert out[3]["file_error"] == "no file given"
