"""Create the demo database: users, projects, memberships, shared resources and pipelines (the default
template for both projects).

    python -m devtools.seed            # local: (re)creates local/app/ from scratch
Requires `python -m devtools.synth` first (shared synthetic resources).

With S3 storage / Postgres (a dev environment on AWS) it only seeds an empty database and uploads the
resources to the resources bucket.
"""
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
SYNTH = "Synthetic file for local development"
# (key, title, build, description): the references the default pipeline uses, under their production file names.
SHARED = [
    ("hg19ToHg38.over.chain.gz", "liftOver chain GRCh37 → GRCh38 (SYNTHETIC)", "Any", f"{SYNTH}; production uses the UCSC file."),
    *[(f"{name}{ext}", f"{label} reference genome{' index' if ext else ''} (SYNTHETIC)", build,
       f"{SYNTH}; production uses {prod}.")
      for name, label, build, prod in (("human_g1k_v37.fasta", "GRCh37", "GRCh37", "the 1000 Genomes GRCh37 FASTA"),
                                       ("Homo_sapiens_assembly38_nochr.fasta", "GRCh38", "GRCh38", "the Broad GRCh38 FASTA without chr prefixes"))
      for ext in ("", ".fai", ".gzi")],
    ("dbSNP157_b38_clean.vcf.gz", "dbSNP 157 rsIDs, GRCh38 (SYNTHETIC)", "GRCh38", f"{SYNTH}; production uses dbSNP build 157."),
    ("dbSNP157_b38_clean.vcf.gz.tbi", "dbSNP 157 index (SYNTHETIC)", "GRCh38", f"{SYNTH}."),
    ("all_afreq_b38.tsv.gz", "1000 Genomes allele frequencies by super-population (SYNTHETIC)", "GRCh38",
     f"{SYNTH}; production uses 1000 Genomes phase 3 (AFR, AMR, EAS, EUR, SAS)."),
    ("genes_b38.tsv.gz", "Genes, GRCh38 (SYNTHETIC)", "GRCh38", f"{SYNTH}; production uses GENCODE 50 (resources/build/genes_gencode.sh)."),
    ("w_hm3.snplist", "HapMap 3 SNP list for LDSC (SYNTHETIC)", "Any", f"{SYNTH}; production uses the LDSC w_hm3.snplist."),
    *[(f"1kg_b38_{pop}{ext}", f"1000 Genomes LD panel, {pop} (SYNTHETIC)", "GRCh38",
       f"{SYNTH}; production uses resources/build/kg1000_b38.sh.")
      for pop in ("AFR", "AMR", "EAS", "EUR", "SAS") for ext in (".pgen", ".pvar.zst", ".psam")],
    *[(f"UKBB.{pop}.l2{ext}", f"LD scores, {pop}{' (M_5_50)' if ext == '.M_5_50' else ''} (SYNTHETIC)", "GRCh38",
       f"{SYNTH}; production uses the Pan-UK Biobank LD scores.")
      for pop in ("AFR", "AMR", "CSA", "EAS", "EUR", "MID") for ext in (".ldscore.gz", ".M_5_50")],
]

def main():
    s = settings()
    shared_dir = s.shared_resources_dir
    if not os.path.exists(os.path.join(shared_dir, "Homo_sapiens_assembly38_nochr.fasta")):
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
        if not os.path.exists(path):  # e.g. the LD panels, which synth.py builds only when Docker is available
            continue
        db.add(Resource(project_id=None, key=key, title=title, description=desc, build=build, size=os.path.getsize(path),
                        sha256=sha256_file(path), version=1, source="Synthetic (devtools/synth.py)",
                        storage_path=store(path, f"resources/shared/{key}/v1/{key}")))

    for pid, name, short, desc, phenos, members in PROJECTS:
        p = Project(id=pid, name=name, short=short, description=desc, phenotypes=phenos)
        db.add(p)
        for email, role in members.items():
            db.add(Membership(user=users[email], project=p, role=role))
    db.flush()

    tpl = services.default_template()
    services.save_pipeline(db, "hermes3", tpl, "Created from the default template", users["ama@example.org"].id)
    services.save_pipeline(db, "hcm", tpl, "Created from the default template", users["tom@example.org"].id)
    db.commit()
    db.close()
    print(f"Seeded {s.data_dir}: {len(USERS)} users, {len(PROJECTS)} projects. Sign in as demo@gwashub.org.")


if __name__ == "__main__":
    main()
