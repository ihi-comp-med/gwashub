"""Settings from environment variables (no literals for anything deployment-specific)."""
import json
import os
from functools import lru_cache
from urllib.parse import quote

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUTH_MODES = ("dev", "cognito")


def _db_url(data_dir: str) -> str:
    if os.environ.get("GWASHUB_DB_URL"):
        return os.environ["GWASHUB_DB_URL"]
    host = os.environ.get("GWASHUB_DB_HOST")
    if host:  # AWS: the parts come from the RDS secret (injected by ECS)
        user, pw = quote(os.environ["GWASHUB_DB_USER"], safe=""), quote(os.environ["GWASHUB_DB_PASSWORD"], safe="")
        port, name = os.environ.get("GWASHUB_DB_PORT", "5432"), os.environ.get("GWASHUB_DB_NAME", "gwashub")
        return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{name}?sslmode=require"
    return f"sqlite:///{os.path.join(data_dir, 'gwashub.db')}"


class Settings:
    def __init__(self):
        env = os.environ.get
        self.data_dir = os.path.abspath(env("GWASHUB_DATA_DIR", os.path.join(REPO_ROOT, "local", "app")))
        self.db_url = _db_url(self.data_dir)
        self.storage_dir = os.path.join(self.data_dir, "storage")
        self.shared_resources_dir = os.path.abspath(env("GWASHUB_SHARED_RESOURCES", os.path.join(REPO_ROOT, "local", "synthetic", "resources")))
        self.qc_steps_dir = os.path.join(REPO_ROOT, "pipeline", "qc_steps")
        self.images_dir = os.path.join(REPO_ROOT, "pipeline", "images")
        self.secret = env("GWASHUB_SECRET", "dev-only-secret-change-me")
        self.max_workers = int(env("GWASHUB_WORKERS", "3"))
        self.cors_origins = env("GWASHUB_CORS", "http://localhost:3000").split(",")
        self.inline_jobs = env("GWASHUB_INLINE_JOBS") == "1"  # tests: run jobs synchronously
        # "dev": pick any user, no password (local only). "cognito": Amazon Cognito hosted sign-in (AWS).
        # Dev endpoints exist only in "dev".
        self.auth_mode = env("GWASHUB_AUTH", "dev")
        self.session_hours = float(env("GWASHUB_SESSION_HOURS", "12"))
        # Built website (nuxt generate output) to serve from the API; unset in local dev (Nuxt serves it)
        # and on AWS (CloudFront serves it).
        self.web_dir = env("GWASHUB_WEB_DIR", "")
        # Public address of the site, used to build sign-in redirects (e.g. https://d123.cloudfront.net).
        self.public_url = env("GWASHUB_PUBLIC_URL", "http://localhost:3000").rstrip("/")
        # Strip across the top of every page saying which environment this is; empty in production.
        self.banner = env("GWASHUB_BANNER", "Local development build: synthetic data")
        # Where the website points people who want to contribute a step (CONTRIBUTING.md on GitHub).
        self.contributing_url = env("GWASHUB_CONTRIBUTING_URL", "")

        # Files: "local" (folders under data_dir) or "s3" (three buckets).
        self.storage = env("GWASHUB_STORAGE", "local")
        self.aws_region = env("AWS_REGION", env("AWS_DEFAULT_REGION", "eu-west-2"))
        self.buckets = {"uploads": env("GWASHUB_UPLOADS_BUCKET", ""), "results": env("GWASHUB_RESULTS_BUCKET", ""),
                        "resources": env("GWASHUB_RESOURCES_BUCKET", "")}
        self.presign_seconds = int(env("GWASHUB_PRESIGN_SECONDS", "900"))

        # Jobs: "local" (threads in this process) or "batch" (AWS Batch; results arrive via SQS + worker).
        self.jobs = env("GWASHUB_JOBS", "local")
        self.batch_queue = env("GWASHUB_BATCH_QUEUE", "")
        # Pipeline images on AWS: {"gwashub/base": {"uri": "<ecr uri>@sha256:...", "job_definition": "<arn>"}, ...}
        self.images = json.loads(env("GWASHUB_IMAGES", "{}") or "{}")
        self.events_queue_url = env("GWASHUB_EVENTS_QUEUE_URL", "")

        # Cognito (auth_mode "cognito")
        self.cognito_domain = env("GWASHUB_COGNITO_DOMAIN", "").rstrip("/")  # https://<prefix>.auth.<region>.amazoncognito.com
        self.cognito_user_pool_id = env("GWASHUB_COGNITO_USER_POOL_ID", "")
        self.cognito_client_id = env("GWASHUB_COGNITO_CLIENT_ID", "")
        self.cognito_client_secret = env("GWASHUB_COGNITO_CLIENT_SECRET", "")

        if self.auth_mode not in AUTH_MODES:
            raise RuntimeError(f"GWASHUB_AUTH must be one of {', '.join(AUTH_MODES)}, not {self.auth_mode!r}")
        if self.auth_mode != "dev" and (self.secret == "dev-only-secret-change-me" or len(self.secret) < 32):
            raise RuntimeError("Set GWASHUB_SECRET to a random string of at least 32 characters")
        if self.storage not in ("local", "s3"):
            raise RuntimeError("GWASHUB_STORAGE must be 'local' or 's3'")
        if self.storage == "s3" and not all(self.buckets.values()):
            raise RuntimeError("GWASHUB_STORAGE=s3 needs GWASHUB_UPLOADS_BUCKET, GWASHUB_RESULTS_BUCKET and GWASHUB_RESOURCES_BUCKET")
        if self.jobs not in ("local", "batch"):
            raise RuntimeError("GWASHUB_JOBS must be 'local' or 'batch'")
        if self.jobs == "batch" and (self.storage != "s3" or not self.batch_queue or "gwashub/base" not in self.images):
            raise RuntimeError("GWASHUB_JOBS=batch needs S3 storage, GWASHUB_BATCH_QUEUE and GWASHUB_IMAGES (with gwashub/base)")
        if self.auth_mode == "cognito" and not all((self.cognito_domain, self.cognito_user_pool_id, self.cognito_client_id,
                                                    self.cognito_client_secret)):
            raise RuntimeError("GWASHUB_AUTH=cognito needs the GWASHUB_COGNITO_* settings")

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")


@lru_cache
def settings():
    return Settings()
