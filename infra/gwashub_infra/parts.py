"""Parts shared by the stacks: the file store (KMS key and three buckets), Cognito sign-in, Batch jobs and the
job-events queue, the uploads bucket's CORS rule, and two small helpers.

The three-stack environments (Network, Data, App) and the single-stack shared-host deployment build the same parts
from here. Each function takes the stack as `scope`, so construct IDs, and with them CloudFormation logical IDs, are
the ones each stack has always had. A part reads only a few settings from `cfg` (listed on each function), so any
config class that has them will do; everything else a part needs (the site address, the network, the log group) is
passed in.
"""
import json
import os
from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_batch as batch
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sqs as sqs
from aws_cdk import custom_resources as cr
from constructs import Construct


@dataclass
class FileStore:
    key: kms.Key
    uploads: s3.Bucket
    results: s3.Bucket
    resources: s3.Bucket


def file_store(scope: Construct, cfg) -> FileStore:
    """A customer-managed KMS key and the three file buckets (uploads, results, resources), all kept if the stack is
    deleted. Reads cfg.name and cfg.noncurrent_version_days."""
    key = kms.Key(scope, "Key", alias=f"alias/gwashub-{cfg.name}", enable_key_rotation=True,
                  description="GWASHub files and database", removal_policy=cdk.RemovalPolicy.RETAIN)

    def bucket(name: str, extra_rules=()):
        return s3.Bucket(
            scope, name, encryption=s3.BucketEncryption.KMS, encryption_key=key, bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True, versioned=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(id="abort-incomplete-uploads", abort_incomplete_multipart_upload_after=cdk.Duration.days(7)),
                s3.LifecycleRule(id="expire-old-versions", noncurrent_version_expiration=cdk.Duration.days(cfg.noncurrent_version_days)),
                *extra_rules,
            ])

    # Raw uploads: cheaper storage class once they're 90 days old (still instant to read).
    uploads = bucket("Uploads", [s3.LifecycleRule(id="older-uploads-to-glacier-ir", transitions=[
        s3.Transition(storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL, transition_after=cdk.Duration.days(90))])])
    results = bucket("Results")
    resources = bucket("Resources")
    return FileStore(key=key, uploads=uploads, results=results, resources=resources)


@dataclass
class SignIn:
    pool: cognito.UserPool
    domain: cognito.UserPoolDomain
    client: cognito.UserPoolClient
    client_secret: sm.Secret


def sign_in(scope: Construct, cfg, site_url: str) -> SignIn:
    """Cognito sign-in: a user pool (invitation only, MFA required), its hosted domain, the web client with its
    callback and logout addresses under site_url, the login page branding, and the client secret in Secrets Manager.
    Reads cfg.name and cfg.account."""
    n = cfg.name
    pool = cognito.UserPool(
        scope, "Users", user_pool_name=f"gwashub-{n}", self_sign_up_enabled=False,
        sign_in_aliases=cognito.SignInAliases(email=True), sign_in_case_sensitive=False,
        auto_verify=cognito.AutoVerifiedAttrs(email=True),
        standard_attributes=cognito.StandardAttributes(email=cognito.StandardAttribute(required=True, mutable=True),
                                                       fullname=cognito.StandardAttribute(required=False, mutable=True)),
        mfa=cognito.Mfa.REQUIRED, mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
        password_policy=cognito.PasswordPolicy(min_length=12, require_lowercase=True, require_uppercase=False,
                                               require_digits=False, require_symbols=False,
                                               temp_password_validity=cdk.Duration.days(7)),
        account_recovery=cognito.AccountRecovery.EMAIL_ONLY, feature_plan=cognito.FeaturePlan.ESSENTIALS,
        deletion_protection=True, removal_policy=cdk.RemovalPolicy.RETAIN,
        user_invitation=cognito.UserInvitationConfig(
            email_subject="Your GWASHub account",
            email_body=(f"You've been invited to GWASHub, the HERMES Consortium's GWAS portal.<br><br>"
                        f"Go to {site_url} and choose Sign in.<br>Email: {{username}}<br>Temporary password: {{####}}<br><br>"
                        "You'll set your own password and an authenticator app (such as Google Authenticator or "
                        "Microsoft Authenticator) the first time you sign in. The temporary password expires in 7 days.")))
    domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"gwashub-{n}-{cfg.account}"),
                             managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN)
    client = pool.add_client(
        "Web", user_pool_client_name=f"gwashub-{n}-web", generate_secret=True, prevent_user_existence_errors=True,
        o_auth=cognito.OAuthSettings(flows=cognito.OAuthFlows(authorization_code_grant=True),
                                     scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                                     callback_urls=[f"{site_url}/api/auth/callback"], logout_urls=[f"{site_url}/login"]),
        supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        id_token_validity=cdk.Duration.minutes(60), access_token_validity=cdk.Duration.minutes(60),
        refresh_token_validity=cdk.Duration.hours(12), enable_token_revocation=True)
    # Cognito's managed-login style with HERMES blue in place of AWS blue (login_branding.json: the
    # default settings from DescribeManagedLoginBrandingByClient with the colours swapped).
    with open(os.path.join(os.path.dirname(__file__), "login_branding.json")) as f:
        branding = json.load(f)
    cognito.CfnManagedLoginBranding(scope, "LoginBranding", user_pool_id=pool.user_pool_id,
                                    client_id=client.user_pool_client_id, use_cognito_provided_values=False, settings=branding)
    client_secret = sm.Secret(scope, "CognitoClientSecret", secret_name=f"gwashub/{n}/cognito-client",
                              secret_string_value=client.user_pool_client_secret)
    return SignIn(pool=pool, domain=domain, client=client, client_secret=client_secret)


@dataclass
class Jobs:
    job_role: iam.Role
    queues: list[batch.JobQueue]
    job_queue: batch.JobQueue
    job_defs: dict[str, batch.EcsJobDefinition]
    images_env: str  # JSON for the GWASHUB_IMAGES setting: each pipeline image's URI, job definition and queues
    events_queue: sqs.Queue


def batch_jobs(scope: Construct, cfg, *, vpc: ec2.IVpc, subnets: ec2.SubnetSelection,
               security_groups: list[ec2.ISecurityGroup], files: FileStore, log_group: logs.LogGroup,
               pipeline_images: dict) -> Jobs:
    """QC jobs and meta-analyses on AWS Batch, and the queue that carries the jobs' state changes back to the worker.
    Reads cfg.name, cfg.batch_volume_gb, cfg.batch_spot, cfg.batch_max_vcpus and cfg.meta_on_demand."""
    stack = cdk.Stack.of(scope)
    n = cfg.name
    dlq = sqs.Queue(scope, "EventsDlq", retention_period=cdk.Duration.days(14), encryption=sqs.QueueEncryption.SQS_MANAGED)
    events_queue = sqs.Queue(scope, "Events", visibility_timeout=cdk.Duration.minutes(2),
                             encryption=sqs.QueueEncryption.SQS_MANAGED,
                             dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq))
    job_role = iam.Role(scope, "JobRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                        description="GWASHub Batch jobs: read inputs and reference files, write results")
    files.uploads.grant_read(job_role)
    files.resources.grant_read(job_role)
    files.results.grant_read_write(job_role)
    # Jobs need room for reference files (FASTA, dbSNP) and intermediate VCFs: a larger root volume than the
    # ECS AMI's default.
    job_lt = ec2.LaunchTemplate(scope, "JobLaunchTemplate", block_devices=[ec2.BlockDevice(
        device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(cfg.batch_volume_gb, encrypted=True,
                                                                  volume_type=ec2.EbsDeviceVolumeType.GP3))])

    def compute_env(cid, families, spot=cfg.batch_spot):
        return batch.ManagedEc2EcsComputeEnvironment(
            scope, cid, vpc=vpc, vpc_subnets=subnets,
            security_groups=security_groups, spot=spot, minv_cpus=0, maxv_cpus=cfg.batch_max_vcpus,
            use_optimal_instance_classes=False, launch_template=job_lt,
            # Sizes up to 4xlarge (16 vCPU, 128 GiB): enough for any job (pipeline/runner/catalog.py MAX_COMPUTE),
            # and Spot can't hand a small job a much larger machine.
            instance_types=[ec2.InstanceType(f"{fam}.{size}") for fam in families for size in ("large", "xlarge", "2xlarge", "4xlarge")],
            allocation_strategy=batch.AllocationStrategy.SPOT_PRICE_CAPACITY_OPTIMIZED if spot
            else batch.AllocationStrategy.BEST_FIT_PROGRESSIVE,
            images=[batch.EcsMachineImage(image_type=batch.EcsMachineImageType.ECS_AL2023)],
            update_to_latest_image_version=True)

    # Graviton for the ARM64 images (the default), x86-64 for images whose image.yaml says linux/amd64
    # (tools with no ARM64 builds, e.g. gwas2vcf and LDSC).
    compute = compute_env("Compute", ["m7g", "r7g"])
    job_queue = batch.JobQueue(scope, "JobQueue", job_queue_name=f"gwashub-{n}",
                               compute_environments=[batch.OrderedComputeEnvironment(compute_environment=compute, order=1)])
    compute_x86 = compute_env("ComputeX86", ["m7i", "r7i"])
    job_queue_x86 = batch.JobQueue(scope, "JobQueueX86", job_queue_name=f"gwashub-{n}-x86",
                                   compute_environments=[batch.OrderedComputeEnvironment(compute_environment=compute_x86, order=1)])
    queues = [job_queue, job_queue_x86]
    # Meta-analyses (jobs.start_meta) on On-Demand machines, which AWS never takes back: a Spot reclaim restarts a
    # meta-analysis from the beginning, hours in. Scales to zero like the others, so it costs nothing when idle.
    meta_queue = meta_queue_x86 = None
    if cfg.meta_on_demand:
        meta_queue = batch.JobQueue(scope, "MetaJobQueue", job_queue_name=f"gwashub-{n}-ondemand", compute_environments=[
            batch.OrderedComputeEnvironment(compute_environment=compute_env("ComputeOnDemand", ["m7g", "r7g"], spot=False), order=1)])
        meta_queue_x86 = batch.JobQueue(scope, "MetaJobQueueX86", job_queue_name=f"gwashub-{n}-ondemand-x86", compute_environments=[
            batch.OrderedComputeEnvironment(compute_environment=compute_env("ComputeOnDemandX86", ["m7i", "r7i"], spot=False), order=1)])
        queues += [meta_queue, meta_queue_x86]
    pipeline_images = {ref: v if isinstance(v, dict) else {"digest": v} for ref, v in pipeline_images.items()}
    x86 = {ref for ref, v in pipeline_images.items() if v.get("platform") == "linux/amd64"}
    queue_of = {ref: job_queue_x86 if ref in x86 else job_queue for ref in pipeline_images}
    meta_queue_of = {ref: meta_queue_x86 if ref in x86 else meta_queue for ref in pipeline_images}
    # One job definition per pipeline image (pipeline/images/<owner>/<image>), pinned by digest. A QC
    # run is one job per group of consecutive steps sharing an image; a meta-analysis uses gwashub/base.
    job_defs = {}
    for ref, img in sorted(pipeline_images.items()):
        digest = img["digest"]
        slug = ref.replace("/", "-")
        img_repo = ecr.Repository.from_repository_name(scope, f"ImgRepo-{slug}", f"gwashub-images/{ref}")
        job_defs[ref] = batch.EcsJobDefinition(
            scope, f"JobDef-{slug}", job_definition_name=f"gwashub-{n}-{slug}", timeout=cdk.Duration.hours(12), retry_attempts=3,
            retry_strategies=[batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.SPOT_INSTANCE_RECLAIMED),
                              batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.CANNOT_PULL_CONTAINER),
                              batch.RetryStrategy.of(batch.Action.EXIT, batch.Reason.custom(on_exit_code="*"))],
            container=batch.EcsEc2ContainerDefinition(
                scope, f"JobContainer-{slug}", image=ecs.ContainerImage.from_ecr_repository(img_repo, digest),
                cpu=2, memory=cdk.Size.gibibytes(8), job_role=job_role,
                command=["python", "-m", "pipeline.runner.aws.batch_entry"],
                environment={"AWS_REGION": stack.region, "MPLCONFIGDIR": "/tmp/mpl", "HOME": "/tmp"},
                logging=ecs.LogDriver.aws_logs(stream_prefix=f"batch-{slug}", log_group=log_group)))
    images_env = stack.to_json_string({ref: {"uri": f"{stack.account}.dkr.ecr.{stack.region}.amazonaws.com/gwashub-images/{ref}@{img['digest']}",
                                             "job_definition": job_defs[ref].job_definition_arn,
                                             "job_queue": queue_of[ref].job_queue_arn,
                                             **({"meta_job_queue": meta_queue_of[ref].job_queue_arn} if meta_queue else {})}
                                       for ref, img in pipeline_images.items()})
    rule = events.Rule(scope, "JobEvents", event_pattern=events.EventPattern(
        source=["aws.batch"], detail_type=["Batch Job State Change"],
        detail={"jobQueue": [q.job_queue_arn for q in queues], "status": ["RUNNABLE", "STARTING", "RUNNING", "SUCCEEDED", "FAILED"]}))
    rule.add_target(targets.SqsQueue(events_queue))
    return Jobs(job_role=job_role, queues=queues, job_queue=job_queue, job_defs=job_defs, images_env=images_env,
                events_queue=events_queue)


def uploads_cors(scope: Construct, uploads: s3.IBucket, site_url: str) -> None:
    """Browsers PUT upload parts straight to the uploads bucket, only from the site's own address."""
    cr.AwsCustomResource(
        scope, "UploadsCors",
        on_update=cr.AwsSdkCall(service="S3", action="putBucketCors", parameters={
            "Bucket": uploads.bucket_name,
            "CORSConfiguration": {"CORSRules": [{"AllowedOrigins": [site_url], "AllowedMethods": ["PUT"],
                                                 "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000}]}},
            physical_resource_id=cr.PhysicalResourceId.of(f"{uploads.bucket_name}-cors")),
        policy=cr.AwsCustomResourcePolicy.from_statements([iam.PolicyStatement(
            actions=["s3:PutBucketCORS"], resources=[uploads.bucket_arn])]),
        install_latest_aws_sdk=False)


def retention(days: int) -> logs.RetentionDays:
    return {90: logs.RetentionDays.THREE_MONTHS, 365: logs.RetentionDays.ONE_YEAR}.get(days, logs.RetentionDays.THREE_MONTHS)


def unversioned(job_def_arn: str) -> str:
    """batch:SubmitJob is authorised against the job definition with or without its revision."""
    return cdk.Fn.join("", [cdk.Fn.select(0, cdk.Fn.split(":job-definition/", job_def_arn)), ":job-definition/*"])
