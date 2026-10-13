# Where this file comes from

`METAL_example_single.metal` is the Skin Genetics Consortium's own METAL script, copied **unchanged** (byte for byte)
from their repository. Don't edit it here: change it in SGC's repository, then copy it in again and update this file.
`pipeline/tests/test_sgc.py` checks that the copy still matches the checksum below (unchanged: both checksums are the same).

| File | Repository | Commit | sha256 (SGC's) | sha256 (here) |
|---|---|---|---|---|
| `METAL_example_single.metal` | https://github.com/Katie-Watts/SGC | `1bd3df946afbc8b34d0888aa35acad4b3ac6ada5` | `6763b0b60b2d9ffce77fd6f4f2774b6042ab5eb9d6203e12f319668fb0739f40` | `6763b0b60b2d9ffce77fd6f4f2774b6042ab5eb9d6203e12f319668fb0739f40` |

`gwashub_metal.py` is GWASHub code around it: it writes the datasets in the columns the script reads, sets the
script's PROCESS and OUTFILE lines for the run (as SGC's README says to), runs METAL and reads its table back.
