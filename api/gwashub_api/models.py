from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gwashub_api.db import Base


def now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    institution: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    cognito_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)  # set at first Cognito sign-in


class Project(Base):
    __tablename__ = "project"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String(300))
    short: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")
    phenotypes: Mapped[list] = mapped_column(JSON, default=list)  # codes, e.g. "HF"
    phenotype_descriptions: Mapped[dict] = mapped_column(JSON, default=dict)  # code -> description
    # code -> "binary" (case/control) or "quantitative"; a phenotype not listed is binary (services.trait_type).
    phenotype_types: Mapped[dict] = mapped_column(JSON, default=dict)
    # Which metadata fields (metadata_schema.py) are shown and which are required at upload; null = defaults.
    metadata_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The project's own metadata fields: [{key, label, level (cohort | dataset), type, options, hidden}]
    # (metadata_schema.custom_sections). Keys never change; deleting a field hides it and keeps its values.
    metadata_custom: Mapped[list] = mapped_column(JSON, default=list)
    # Upload options: which ancestries and sex strata may be chosen when uploading.
    ancestries: Mapped[list] = mapped_column(JSON, default=lambda: ["EUR", "AFR", "EAS", "SAS", "AMR", "Multi"])
    sexes: Mapped[list] = mapped_column(JSON, default=lambda: ["Combined", "Female", "Male"])
    meta_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    members: Mapped[list["Membership"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", "project_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    role: Mapped[str] = mapped_column(String(20))  # uploader | reviewer
    user: Mapped[User] = relationship()
    project: Mapped[Project] = relationship(back_populates="members")


class Resource(Base):
    __tablename__ = "resource"
    __table_args__ = (UniqueConstraint("project_id", "key", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("project.id"), nullable=True)  # None = shared
    key: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    build: Mapped[str] = mapped_column(String(20), default="Any")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(Text, default="Uploaded")
    storage_path: Mapped[str] = mapped_column(Text)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Pipeline(Base):
    """A named QC pipeline of a project, with its own versions. One is the project default: datasets follow it unless a
    reviewer pins them to another (Dataset.pipeline_id)."""
    __tablename__ = "pipeline"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PipelineVersion(Base):
    """One saved version of a pipeline. Version numbers are unique within the project (across its pipelines), so
    "pipeline version N" names one thing."""
    __tablename__ = "pipeline_version"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    pipeline_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline.id"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer)
    steps: Mapped[list] = mapped_column(JSON)  # [{step, image, on_fail, params}]
    resolved: Mapped[list] = mapped_column(JSON, default=list)  # pinned sources/resources at save time
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    pipeline: Mapped["Pipeline | None"] = relationship()


class Cohort(Base):
    """A study that contributes datasets to one project (projects are isolated, so the same study in two
    projects is two cohorts). Holds the cohort-level metadata its datasets inherit."""
    __tablename__ = "cohort"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str] = mapped_column(String(200))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # cohort-level fields of metadata_schema.py
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiToken(Base):
    """A personal token for the upload script: acts as its user, in one project, for uploading only (auth.upload_caller).
    Only a hash of the token is stored; the token itself is shown once, when it is made."""
    __tablename__ = "api_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex of the token
    prefix: Mapped[str] = mapped_column(String(12))  # the token's first characters, to recognise it in lists
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[User] = relationship()


class Dataset(Base):
    __tablename__ = "dataset"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    cohort: Mapped[str] = mapped_column(String(200))  # the cohort's name (kept in step with Cohort.name)
    cohort_id: Mapped[int | None] = mapped_column(ForeignKey("cohort.id"), nullable=True, index=True)
    phenotype: Mapped[str] = mapped_column(String(200))
    ancestry: Mapped[str] = mapped_column(String(20))
    sex: Mapped[str] = mapped_column(String(20))
    build: Mapped[str] = mapped_column(String(20))
    effect_type: Mapped[str] = mapped_column(String(10), default="beta")
    # Case/control phenotypes: cases and controls, and n_total their sum. Quantitative phenotypes: n_total only.
    n_cases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_controls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The QC pipeline a reviewer pinned it to; None = follows the project's default pipeline.
    pipeline_id: Mapped[int | None] = mapped_column(ForeignKey("pipeline.id"), nullable=True)
    column_mapping: Mapped[dict] = mapped_column(JSON)
    # Dataset-level metadata plus any overrides of cohort-level fields (see metadata_schema.effective).
    study_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    edits: Mapped[list] = mapped_column(JSON, default=list)  # [{by, at, changes: {field: [old, new]}}]
    file_name: Mapped[str] = mapped_column(String(300))
    file_size: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)  # location: local path or s3:// URI (see storage.py)
    upload_id: Mapped[str] = mapped_column(String(300), default="")  # multipart upload in progress
    uploaded_with_token: Mapped[int | None] = mapped_column(ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")  # as reported by the upload script (duplicate checks)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # uploading|pending (QC not started)|processing|passed|review|failed
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {decision, note, by, at}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    runs: Mapped[list["QCRun"]] = relationship(back_populates="dataset", order_by="QCRun.id", cascade="all, delete-orphan")
    uploader: Mapped[User] = relationship()
    cohort_ref: Mapped["Cohort | None"] = relationship()
    project: Mapped[Project] = relationship()
    pipeline: Mapped["Pipeline | None"] = relationship()


class QCRun(Base):
    __tablename__ = "qc_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset.id"))
    pipeline_version_id: Mapped[int] = mapped_column(ForeignKey("pipeline_version.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|blocked|failed
    overall_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)
    progress: Mapped[list] = mapped_column(JSON, default=list)  # step states while running
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_dir: Mapped[str] = mapped_column(Text, default="")  # output location
    batch_job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # The latest AWS Batch state of the job that is working on it: {group, status (RUNNABLE | STARTING | RUNNING),
    # attempt, max_attempts, previous (why earlier attempts ended), at}. Shown while it runs (jobs.batch_note).
    batch_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dataset: Mapped[Dataset] = relationship(back_populates="runs")
    pipeline_version: Mapped[PipelineVersion] = relationship()


class MetaAnalysis(Base):
    __tablename__ = "meta_analysis"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"))
    name: Mapped[str] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(200), default="gwashub/gwama", server_default="gwashub/gwama")  # pipeline/analyses/meta_analysis/<owner>/<method>
    settings: Mapped[dict] = mapped_column(JSON)  # {method, params, common, include_statuses}
    dataset_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_dir: Mapped[str] = mapped_column(Text, default="")
    batch_job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # The latest AWS Batch state of the job that is working on it: {group, status (RUNNABLE | STARTING | RUNNING),
    # attempt, max_attempts, previous (why earlier attempts ended), at}. Shown while it runs (jobs.batch_note).
    batch_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lambda_gc: Mapped[float | None] = mapped_column(Float, nullable=True)
