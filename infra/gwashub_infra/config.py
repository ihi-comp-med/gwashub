"""Per-environment settings. "dev" is the lean setup; "prod" turns on the production-grade options
described in docs/AWS_DEPLOYMENT.md (Multi-AZ database, more NAT gateways and tasks, longer backups)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvConfig:
    name: str
    account: str
    region: str
    nat_gateways: int
    db_instance: str  # e.g. t4g.small
    db_multi_az: bool
    db_backup_days: int
    api_tasks: int
    batch_max_vcpus: int
    batch_spot: bool
    batch_volume_gb: int  # root volume of Batch instances (reference files + intermediate VCFs)
    log_days: int
    noncurrent_version_days: int  # how long deleted/overwritten files stay recoverable
    banner: str  # strip at the top of every page; empty for none


ENVIRONMENTS = {
    "dev": EnvConfig(name="dev", account="203248996074", region="eu-west-2", nat_gateways=1, db_instance="t4g.small",
                     db_multi_az=False, db_backup_days=7, api_tasks=1, batch_max_vcpus=32, batch_spot=True, batch_volume_gb=200,
                     log_days=90, noncurrent_version_days=30, banner="Development build"),
    "prod": EnvConfig(name="prod", account="269626333085", region="eu-west-2", nat_gateways=2, db_instance="t4g.medium",
                      db_multi_az=True, db_backup_days=30, api_tasks=2, batch_max_vcpus=128, batch_spot=True, batch_volume_gb=500,
                      log_days=365, noncurrent_version_days=90, banner=""),
}
