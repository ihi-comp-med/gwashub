"""Create the demo database: users, projects, memberships, shared resources and pipelines (HCM's uses
the consortium step hcm/sarcomere_filter, in R).

    python -m devtools.seed            # local: (re)creates local/app/ from scratch
Requires `python -m devtools.synth` first (shared synthetic resources).

With S3 storage / Postgres (a dev environment on AWS) it only seeds an empty database and uploads the
resources to the resources bucket.
"""
import gzip
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))

from gwashub_api.config import settings  # noqa: E402
from gwashub_api.db import SessionLocal, create_all, reset_engine  # noqa: E402
from gwashub_api.models import Membership, Project, Resource, User  # noqa: E402
from sqlalchemy import select  # noqa: E402
from gwashub_api import services  # noqa: E402
from gwashub_api import storage  # noqa: E402
from gwashub_api.storage import sha256_file  # noqa: E402

USERS = [
    ("demo@gwashub.org", "Demo User", "University of Bristol", True),
    ("ama@example.org", "Ama Kwarteng", "UCL", False),
    ("jonas@example.org", "Jonas Lindqvist", "Karolinska Institutet", False),
    ("mei@example.org", "Mei Ping", "Fudan University", False),
    ("tom@example.org", "Tom Barker", "University of Oxford", False),
    ("noor@example.org", "Noor Iqbal", "Aga Khan University", False),
]
PROJECTS = [
    ("hermes3", "HERMES 3 heart failure GWAS meta-analysis", "HERMES 3",
     "Multi-ancestry genome-wide association meta-analysis of heart failure. Contributing cohorts upload ancestry- and "
     "sex-stratified summary statistics, which are QC'd with a common pipeline before meta-analysis.",
     ["All-cause HF", "HFrEF", "HFpEF"],
     {"demo@gwashub.org": "reviewer", "ama@example.org": "reviewer", "jonas@example.org": "uploader", "mei@example.org": "uploader"}),
    ("hcm", "HCM Genetics Consortium", "HCM",
     "Case-control GWAS of hypertrophic cardiomyopathy across international registries.",
     ["HCM", "Sarcomere-negative HCM"],
     {"tom@example.org": "reviewer", "demo@gwashub.org": "uploader", "noor@example.org": "uploader"}),
]
SHARED = [
    ("hg19ToHg38.over.chain.gz", "liftOver chain GRCh37 → GRCh38 (SYNTHETIC)", "Any",
     "Synthetic chain for local development; production uses the UCSC file."),
    ("ref_panel_grch38.tsv.gz", "Reference panel: alleles + per-ancestry frequencies (SYNTHETIC)", "GRCh38",
     "Synthetic panel for local development; production uses 1000 Genomes / gnomAD."),
]

def main():
    s = settings()
    shared_dir = s.shared_resources_dir
    if not os.path.exists(os.path.join(shared_dir, "ref_panel_grch38.tsv.gz")):
        raise SystemExit(f"Run `python -m devtools.synth` first (missing {shared_dir})")
    local = s.storage == "local"  # files on this machine: keep resources in place
    if s.is_sqlite:
        if os.path.exists(s.data_dir):
            shutil.rmtree(s.data_dir)
        reset_engine()
    create_all()
    db = SessionLocal()
    if not s.is_sqlite and db.scalar(select(User).limit(1)):
        raise SystemExit("The database already has users; not seeding")

    def store(path, key):
        """Local runs use the files where they are; on AWS they're copied to the resources bucket."""
        if local:
            return path
        loc = storage.location("resources", key)
        storage.put_file(path, loc)
        return loc

    users = {}
    for email, name, inst, admin in USERS:
        u = User(email=email, name=name, institution=inst, is_admin=admin)
        db.add(u)
        users[email] = u
    db.flush()

    for key, title, build, desc in SHARED:
        path = os.path.join(shared_dir, key)
        db.add(Resource(project_id=None, key=key, title=title, description=desc, build=build, size=os.path.getsize(path),
                        sha256=sha256_file(path), version=1, source="Synthetic (devtools/synth.py)",
                        storage_path=store(path, f"resources/shared/{key}/v1/{key}")))

    for pid, name, short, desc, phenos, members in PROJECTS:
        p = Project(id=pid, name=name, short=short, description=desc, phenotypes=phenos)
        db.add(p)
        for email, role in members.items():
            db.add(Membership(user=users[email], project=p, role=role))
    db.flush()

    # HCM: a project resource (BED of synthetic "sarcomere" regions around two HCM loci) and a custom R step.
    import pandas as pd
    truth = pd.read_csv(os.path.join(os.path.dirname(shared_dir), "truth_causal.tsv"), sep="\t", dtype={"chr": str})
    hcm = truth[truth["phenotype"] == "HCM"].head(2)
    bed_dir = os.path.join(s.storage_dir if local else s.data_dir, "resources", "hcm", "sarcomere_genes_grch38.bed", "v1")
    os.makedirs(bed_dir, exist_ok=True)
    bed = os.path.join(bed_dir, "sarcomere_genes_grch38.bed")
    with open(bed, "w") as f:
        for i, r in enumerate(hcm.itertuples()):
            f.write(f"chr{r.chr}\t{int(r.pos38) - 500000}\t{int(r.pos38) + 500000}\tSARC{i + 1}\n")
    db.add(Resource(project_id="hcm", key="sarcomere_genes_grch38.bed", title="Core sarcomere regions ±500 kb (SYNTHETIC)",
                    description="Regions removed by the sarcomere-variant filter.", build="GRCh38", size=os.path.getsize(bed),
                    sha256=sha256_file(bed), version=1, source="Uploaded", added_by=users["tom@example.org"].id,
                    storage_path=store(bed, "resources/hcm/sarcomere_genes_grch38.bed/v1/sarcomere_genes_grch38.bed")))
    tpl = services.default_template()
    services.save_pipeline(db, "hermes3", tpl, "Created from the default template", users["ama@example.org"].id)
    services.save_pipeline(db, "hcm", tpl, "Created from the default template", users["tom@example.org"].id)
    hcm_steps = tpl[:6] + [{"step": "hcm/sarcomere_filter"}] + tpl[6:]
    services.save_pipeline(db, "hcm", hcm_steps, "Added the HCM sarcomere-variant filter (R)", users["tom@example.org"].id)
    db.commit()
    db.close()
    print(f"Seeded {s.data_dir}: {len(USERS)} users, {len(PROJECTS)} projects. Sign in as demo@gwashub.org.")


if __name__ == "__main__":
    main()
