"""The running service: Cognito sign-in, the API and worker on ECS Fargate behind an internal load
balancer, QC jobs on AWS Batch, the job-events queue, and the website on S3 + CloudFront.

CloudFront is the only way in: `/api/*` reaches the internal load balancer through a CloudFront VPC
origin (the load balancer has no public address), everything else is the static site.
"""
import json
import os

import aws_cdk as cdk
from aws_cdk import aws_batch as batch
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as r53targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sqs as sqs
from aws_cdk import custom_resources as cr
from constructs import Construct

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB_BUILD = os.path.join(REPO_ROOT, "web", ".output", "public")

# Rewrite site paths without a file extension to the single-page app shell (only for the website
# behaviour; /api/* is untouched, so API 404s stay 404s).
SPA_REWRITE = """
function handler(event) {
  var req = event.request;
  var last = req.uri.split('/').pop();
  if (last.indexOf('.') === -1) { req.uri = '/200.html'; }
  return req;
}
"""


class AppStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, cfg, net, data, image_tag: str, pipeline_images: dict, **kwargs):
        """pipeline_images: {"gwashub/base": {"digest": "sha256:...", "platform": "linux/arm64"}, ...} (or just the
        digest), the images pushed by the deploy script to ECR repositories gwashub-images/<owner>/<image>."""
        super().__init__(scope, id, **kwargs)
        vpc, n = net.vpc, cfg.name
        repo = ecr.Repository.from_repository_name(self, "Repo", "gwashub/app")
        image = ecs.ContainerImage.from_ecr_repository(repo, image_tag)
        log_group = logs.LogGroup(self, "Logs", log_group_name=f"/gwashub/{n}/app",
                                  retention=_retention(cfg.log_days),
                                  removal_policy=cdk.RemovalPolicy.RETAIN)
        session_secret = sm.Secret(self, "SessionSecret", secret_name=f"gwashub/{n}/session",
                                   description="GWASHub session-cookie signing key",
                                   generate_secret_string=sm.SecretStringGenerator(password_length=64, exclude_punctuation=True))

        # ---------- load balancer (internal) + website + CloudFront ----------
        alb_sg = net.alb_sg
        cf_prefix_list = cr.AwsCustomResource(
            self, "CloudFrontPrefixList",
            on_update=cr.AwsSdkCall(service="EC2", action="describeManagedPrefixLists",
                                    parameters={"Filters": [{"Name": "prefix-list-name",
                                                             "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]},
                                    physical_resource_id=cr.PhysicalResourceId.of("cloudfront-origin-facing"),
                                    output_paths=["PrefixLists.0.PrefixListId"]),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
            install_latest_aws_sdk=False)
        ec2.CfnSecurityGroupIngress(self, "AlbFromCloudFront", group_id=alb_sg.security_group_id, ip_protocol="tcp",
                                    from_port=80, to_port=80, description="CloudFront VPC origin",
                                    source_prefix_list_id=cf_prefix_list.get_response_field("PrefixLists.0.PrefixListId"))
        alb = elbv2.ApplicationLoadBalancer(self, "Alb", vpc=vpc, internet_facing=False, security_group=alb_sg,
                                            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                                            drop_invalid_header_fields=True, idle_timeout=cdk.Duration.seconds(120))
        listener = alb.add_listener("Http", port=80, open=False,
                                    default_action=elbv2.ListenerAction.fixed_response(404, content_type="text/plain",
                                                                                       message_body="Not found"))

        web_bucket = s3.Bucket(self, "Web", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                               encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=cdk.RemovalPolicy.DESTROY,
                               auto_delete_objects=True, object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED)
        headers = cf.ResponseHeadersPolicy(
            self, "Headers", response_headers_policy_name=f"gwashub-{n}-security",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.days(365), include_subdomains=True, override=True),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(frame_option=cf.HeadersFrameOption.DENY, override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override=True)))
        # With a domain: its certificate and a modern TLS policy (CloudFront's default certificate allows old versions).
        # CloudFront only takes certificates from us-east-1. The organisation's region guardrail allows ACM and Route 53
        # there but not CloudFormation, so this stack requests and DNS-validates the certificate through ACM's
        # us-east-1 API (DnsValidatedCertificate: deprecated, but the construct that does this).
        tls, zone = {}, None
        if cfg.domain:
            zone = route53.HostedZone.from_lookup(self, "Zone", domain_name=cfg.domain)
            certificate = acm.DnsValidatedCertificate(self, "SiteCert", domain_name=cfg.domain, hosted_zone=zone,
                                                      region="us-east-1")
            tls = dict(domain_names=[cfg.domain], certificate=certificate,
                       minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021)
        spa = cf.Function(self, "SpaRewrite", code=cf.FunctionCode.from_inline(SPA_REWRITE), runtime=cf.FunctionRuntime.JS_2_0)
        dist = cf.Distribution(
            self, "Site", comment=f"GWASHub {n}", price_class=cf.PriceClass.PRICE_CLASS_100, default_root_object="index.html",
            http_version=cf.HttpVersion.HTTP2_AND_3, **tls,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS, response_headers_policy=headers,
                function_associations=[cf.FunctionAssociation(function=spa, event_type=cf.FunctionEventType.VIEWER_REQUEST)]),
            additional_behaviors={"/api/*": cf.BehaviorOptions(
                origin=origins.VpcOrigin.with_application_load_balancer(
                    alb, http_port=80, protocol_policy=cf.OriginProtocolPolicy.HTTP_ONLY,
                    read_timeout=cdk.Duration.seconds(60)),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY, allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                response_headers_policy=headers)})
        if zone:
            alias = route53.RecordTarget.from_alias(r53targets.CloudFrontTarget(dist))
            route53.ARecord(self, "SiteA", zone=zone, record_name=cfg.domain, target=alias)
            route53.AaaaRecord(self, "SiteAaaa", zone=zone, record_name=cfg.domain, target=alias)
        site_url = f"https://{cfg.domain}" if cfg.domain else f"https://{dist.distribution_domain_name}"
        if os.path.isdir(WEB_BUILD):
            s3deploy.BucketDeployment(self, "WebFiles", sources=[s3deploy.Source.asset(WEB_BUILD)], destination_bucket=web_bucket,
                                      distribution=dist, distribution_paths=["/*"], prune=True, memory_limit=512)

        # Browsers PUT upload parts straight to the uploads bucket, only from the site's own address.
        cr.AwsCustomResource(
            self, "UploadsCors",
            on_update=cr.AwsSdkCall(service="S3", action="putBucketCors", parameters={
                "Bucket": data.uploads.bucket_name,
                "CORSConfiguration": {"CORSRules": [{"AllowedOrigins": [site_url], "AllowedMethods": ["PUT"],
                                                     "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3000}]}},
                physical_resource_id=cr.PhysicalResourceId.of(f"{data.uploads.bucket_name}-cors")),
            policy=cr.AwsCustomResourcePolicy.from_statements([iam.PolicyStatement(
                actions=["s3:PutBucketCORS"], resources=[data.uploads.bucket_arn])]),
            install_latest_aws_sdk=False)

        # ---------- sign-in ----------
        pool = cognito.UserPool(
            self, "Users", user_pool_name=f"gwashub-{n}", self_sign_up_enabled=False,
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
        cognito.CfnManagedLoginBranding(self, "LoginBranding", user_pool_id=pool.user_pool_id,
                                        client_id=client.user_pool_client_id, use_cognito_provided_values=False, settings=branding)
        client_secret = sm.Secret(self, "CognitoClientSecret", secret_name=f"gwashub/{n}/cognito-client",
                                  secret_string_value=client.user_pool_client_secret)

        # ---------- jobs: Batch + events queue ----------
        dlq = sqs.Queue(self, "EventsDlq", retention_period=cdk.Duration.days(14), encryption=sqs.QueueEncryption.SQS_MANAGED)
        events_queue = sqs.Queue(self, "Events", visibility_timeout=cdk.Duration.minutes(2),
                                 encryption=sqs.QueueEncryption.SQS_MANAGED,
                                 dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq))
        job_role = iam.Role(self, "JobRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                            description="GWASHub Batch jobs: read inputs and reference files, write results")
        data.uploads.grant_read(job_role)
        data.resources.grant_read(job_role)
        data.results.grant_read_write(job_role)
        # Jobs need room for reference files (FASTA, dbSNP) and intermediate VCFs: a larger root volume than the
        # ECS AMI's default.
        job_lt = ec2.LaunchTemplate(self, "JobLaunchTemplate", block_devices=[ec2.BlockDevice(
            device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(cfg.batch_volume_gb, encrypted=True,
                                                                      volume_type=ec2.EbsDeviceVolumeType.GP3))])

        def compute_env(cid, families):
            return batch.ManagedEc2EcsComputeEnvironment(
                self, cid, vpc=vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[net.app_sg], spot=cfg.batch_spot, minv_cpus=0, maxv_cpus=cfg.batch_max_vcpus,
                use_optimal_instance_classes=False, launch_template=job_lt,
                # Sizes up to 4xlarge (16 vCPU, 128 GiB): enough for any job (pipeline/runner/catalog.py MAX_COMPUTE),
                # and Spot can't hand a small job a much larger machine.
                instance_types=[ec2.InstanceType(f"{fam}.{size}") for fam in families for size in ("large", "xlarge", "2xlarge", "4xlarge")],
                allocation_strategy=batch.AllocationStrategy.SPOT_PRICE_CAPACITY_OPTIMIZED if cfg.batch_spot
                else batch.AllocationStrategy.BEST_FIT_PROGRESSIVE,
                images=[batch.EcsMachineImage(image_type=batch.EcsMachineImageType.ECS_AL2023)],
                update_to_latest_image_version=True)

        # Graviton for the ARM64 images (the default), x86-64 for images whose image.yaml says linux/amd64
        # (tools with no ARM64 builds, e.g. gwas2vcf and LDSC).
        compute = compute_env("Compute", ["m7g", "r7g"])
        job_queue = batch.JobQueue(self, "JobQueue", job_queue_name=f"gwashub-{n}",
                                   compute_environments=[batch.OrderedComputeEnvironment(compute_environment=compute, order=1)])
        compute_x86 = compute_env("ComputeX86", ["m7i", "r7i"])
        job_queue_x86 = batch.JobQueue(self, "JobQueueX86", job_queue_name=f"gwashub-{n}-x86",
                                       compute_environments=[batch.OrderedComputeEnvironment(compute_environment=compute_x86, order=1)])
        pipeline_images = {ref: v if isinstance(v, dict) else {"digest": v} for ref, v in pipeline_images.items()}
        queue_of = {ref: job_queue_x86 if v.get("platform") == "linux/amd64" else job_queue for ref, v in pipeline_images.items()}
        # One job definition per pipeline image (pipeline/images/<owner>/<image>), pinned by digest. A QC
        # run is one job per group of consecutive steps sharing an image; a meta-analysis uses gwashub/base.
        job_defs = {}
        for ref, img in sorted(pipeline_images.items()):
            digest = img["digest"]
            slug = ref.replace("/", "-")
            img_repo = ecr.Repository.from_repository_name(self, f"ImgRepo-{slug}", f"gwashub-images/{ref}")
            job_defs[ref] = batch.EcsJobDefinition(
                self, f"JobDef-{slug}", job_definition_name=f"gwashub-{n}-{slug}", timeout=cdk.Duration.hours(12), retry_attempts=3,
                retry_strategies=[batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.SPOT_INSTANCE_RECLAIMED),
                                  batch.RetryStrategy.of(batch.Action.RETRY, batch.Reason.CANNOT_PULL_CONTAINER),
                                  batch.RetryStrategy.of(batch.Action.EXIT, batch.Reason.custom(on_exit_code="*"))],
                container=batch.EcsEc2ContainerDefinition(
                    self, f"JobContainer-{slug}", image=ecs.ContainerImage.from_ecr_repository(img_repo, digest),
                    cpu=2, memory=cdk.Size.gibibytes(8), job_role=job_role,
                    command=["python", "-m", "pipeline.runner.aws.batch_entry"],
                    environment={"AWS_REGION": self.region, "MPLCONFIGDIR": "/tmp/mpl", "HOME": "/tmp"},
                    logging=ecs.LogDriver.aws_logs(stream_prefix=f"batch-{slug}", log_group=log_group)))
        images_env = self.to_json_string({ref: {"uri": f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/gwashub-images/{ref}@{img['digest']}",
                                                "job_definition": job_defs[ref].job_definition_arn,
                                                "job_queue": queue_of[ref].job_queue_arn}
                                          for ref, img in pipeline_images.items()})
        rule = events.Rule(self, "JobEvents", event_pattern=events.EventPattern(
            source=["aws.batch"], detail_type=["Batch Job State Change"],
            detail={"jobQueue": [job_queue.job_queue_arn, job_queue_x86.job_queue_arn], "status": ["RUNNABLE", "STARTING", "RUNNING", "SUCCEEDED", "FAILED"]}))
        rule.add_target(targets.SqsQueue(events_queue))

        # ---------- API + worker on Fargate ----------
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, cluster_name=f"gwashub-{n}")
        env = {
            "GWASHUB_AUTH": "cognito", "GWASHUB_STORAGE": "s3", "GWASHUB_JOBS": "batch", "AWS_REGION": self.region,
            "GWASHUB_UPLOADS_BUCKET": data.uploads.bucket_name, "GWASHUB_RESULTS_BUCKET": data.results.bucket_name,
            "GWASHUB_RESOURCES_BUCKET": data.resources.bucket_name,
            "GWASHUB_BATCH_QUEUE": job_queue.job_queue_arn, "GWASHUB_IMAGES": images_env,
            "GWASHUB_EVENTS_QUEUE_URL": events_queue.queue_url,
            "GWASHUB_COGNITO_DOMAIN": domain.base_url(), "GWASHUB_COGNITO_USER_POOL_ID": pool.user_pool_id,
            "GWASHUB_COGNITO_CLIENT_ID": client.user_pool_client_id,
            "GWASHUB_PUBLIC_URL": site_url, "GWASHUB_CORS": site_url,
            "GWASHUB_DATA_DIR": "/tmp/gwashub", "MPLCONFIGDIR": "/tmp/mpl", "HOME": "/tmp",
            "GWASHUB_DB_READONLY_SECRET": data.readonly_secret.secret_name,
            "GWASHUB_BANNER": cfg.banner,
        }
        db_secret = data.db.secret
        secrets = {
            "GWASHUB_SECRET": ecs.Secret.from_secrets_manager(session_secret),
            "GWASHUB_COGNITO_CLIENT_SECRET": ecs.Secret.from_secrets_manager(client_secret),
            "GWASHUB_DB_HOST": ecs.Secret.from_secrets_manager(db_secret, "host"),
            "GWASHUB_DB_PORT": ecs.Secret.from_secrets_manager(db_secret, "port"),
            "GWASHUB_DB_NAME": ecs.Secret.from_secrets_manager(db_secret, "dbname"),
            "GWASHUB_DB_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "GWASHUB_DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
        }

        def task(name: str, cpu: int, mem: int, command: list[str], port: int | None = None) -> ecs.FargateTaskDefinition:
            td = ecs.FargateTaskDefinition(self, f"{name}Task", cpu=cpu, memory_limit_mib=mem, family=f"gwashub-{n}-{name.lower()}",
                                           runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64,
                                                                                operating_system_family=ecs.OperatingSystemFamily.LINUX))
            # Runs as the image's non-root user. (A read-only root filesystem needs a writable scratch
            # volume, which Fargate mounts owned by root; left for later hardening.)
            td.add_container(name.lower(), image=image, command=command, environment=env, secrets=secrets,
                             logging=ecs.LogDriver.aws_logs(stream_prefix=name.lower(), log_group=log_group),
                             port_mappings=[ecs.PortMapping(container_port=port)] if port else None)
            return td

        api_td = task("Api", 512, 1024, ["uvicorn", "gwashub_api.main:app", "--app-dir", "api", "--host", "0.0.0.0", "--port", "8000",
                                         "--proxy-headers", "--forwarded-allow-ips", "*"], port=8000)
        worker_td = task("Worker", 256, 512, ["python", "-m", "gwashub_api.worker"])
        for td in (api_td, worker_td):
            data.uploads.grant_read_write(td.task_role)
            data.uploads.grant_delete(td.task_role)
            data.results.grant_read_write(td.task_role)
            data.results.grant_delete(td.task_role)
            data.resources.grant_read_write(td.task_role)
            data.resources.grant_delete(td.task_role)  # deleting a project removes its resources
            td.task_role.add_to_principal_policy(iam.PolicyStatement(
                actions=["batch:SubmitJob", "batch:TagResource"],
                resources=[job_queue.job_queue_arn, job_queue_x86.job_queue_arn,
                           *(_unversioned(jd.job_definition_arn) for jd in job_defs.values())]))
            td.task_role.add_to_principal_policy(iam.PolicyStatement(actions=["batch:DescribeJobs"], resources=["*"]))
        events_queue.grant_consume_messages(worker_td.task_role)
        # The API task definition also runs admin commands (devtools/aws_admin.sh), which invite users.
        api_td.task_role.add_to_principal_policy(iam.PolicyStatement(actions=["cognito-idp:AdminCreateUser"], resources=[pool.user_pool_arn]))
        data.readonly_secret.grant_read(api_td.task_role)  # admin command db-readonly-user

        # Jump host for database tunnels (devtools/aws_db_tunnel.sh): no inbound ports, no SSH; only
        # reachable through Session Manager with the caller's AWS sign-in.
        jump = ec2.Instance(
            self, "DbJump", vpc=vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            instance_type=ec2.InstanceType("t4g.nano"), security_group=net.app_sg, require_imdsv2=True,
            machine_image=ec2.MachineImage.latest_amazon_linux2023(cpu_type=ec2.AmazonLinuxCpuType.ARM_64),
            ssm_session_permissions=True, instance_name=f"gwashub-{n}-db-jump",
            block_devices=[ec2.BlockDevice(device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(8, encrypted=True))])

        api = ecs.FargateService(self, "ApiService", cluster=cluster, task_definition=api_td, desired_count=cfg.api_tasks,
                                 security_groups=[net.app_sg], assign_public_ip=False, min_healthy_percent=100,
                                 circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
                                 vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS))
        listener.add_targets("Api", port=8000, protocol=elbv2.ApplicationProtocol.HTTP, targets=[api], priority=10,
                             conditions=[elbv2.ListenerCondition.path_patterns(["/api/*"])],
                             health_check=elbv2.HealthCheck(path="/api/health", healthy_http_codes="200",
                                                            interval=cdk.Duration.seconds(15)),
                             deregistration_delay=cdk.Duration.seconds(20))
        ecs.FargateService(self, "WorkerService", cluster=cluster, task_definition=worker_td, desired_count=1,
                           security_groups=[net.app_sg], assign_public_ip=False, min_healthy_percent=0,
                           circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
                           vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS))

        for k, v in {"DbJumpInstance": jump.instance_id, "SiteUrl": site_url, "ClusterName": cluster.cluster_name, "ApiTaskFamily": api_td.family,
                     "PrivateSubnets": ",".join(s.subnet_id for s in vpc.private_subnets), "AppSecurityGroup": net.app_sg.security_group_id,
                     "UserPoolId": pool.user_pool_id, "LogGroup": log_group.log_group_name, "JobQueue": job_queue.job_queue_name}.items():
            cdk.CfnOutput(self, f"Out{k}", value=v)


def _retention(days: int) -> logs.RetentionDays:
    return {90: logs.RetentionDays.THREE_MONTHS, 365: logs.RetentionDays.ONE_YEAR}.get(days, logs.RetentionDays.THREE_MONTHS)


def _unversioned(job_def_arn: str) -> str:
    """batch:SubmitJob is authorised against the job definition with or without its revision."""
    return cdk.Fn.join("", [cdk.Fn.select(0, cdk.Fn.split(":job-definition/", job_def_arn)), ":job-definition/*"])
