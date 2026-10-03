"""Data: a customer-managed KMS key, the three file buckets and the Postgres database.

Everything here is kept if the stack is deleted (RETAIN / snapshot), so a mistaken `cdk destroy`
can't lose data.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, cfg, net, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.key = kms.Key(self, "Key", alias=f"alias/gwashub-{cfg.name}", enable_key_rotation=True,
                           description="GWASHub files and database", removal_policy=cdk.RemovalPolicy.RETAIN)

        def bucket(name: str, extra_rules=()):
            return s3.Bucket(
                self, name, encryption=s3.BucketEncryption.KMS, encryption_key=self.key, bucket_key_enabled=True,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True, versioned=True,
                object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED, removal_policy=cdk.RemovalPolicy.RETAIN,
                lifecycle_rules=[
                    s3.LifecycleRule(id="abort-incomplete-uploads", abort_incomplete_multipart_upload_after=cdk.Duration.days(7)),
                    s3.LifecycleRule(id="expire-old-versions", noncurrent_version_expiration=cdk.Duration.days(cfg.noncurrent_version_days)),
                    *extra_rules,
                ])

        # Raw uploads: cheaper storage class once they're 90 days old (still instant to read).
        self.uploads = bucket("Uploads", [s3.LifecycleRule(id="older-uploads-to-glacier-ir", transitions=[
            s3.Transition(storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL, transition_after=cdk.Duration.days(90))])])
        self.results = bucket("Results")
        self.resources = bucket("Resources")

        self.db_sg = ec2.SecurityGroup(self, "DbSg", vpc=net.vpc, description="GWASHub Postgres", allow_all_outbound=False)
        self.db_sg.add_ingress_rule(net.app_sg, ec2.Port.tcp(5432), "GWASHub API, worker and admin tasks")
        params = rds.ParameterGroup(self, "DbParams", engine=rds.DatabaseInstanceEngine.postgres(
            version=rds.PostgresEngineVersion.VER_16), parameters={"rds.force_ssl": "1"})
        self.db = rds.DatabaseInstance(
            self, "Db", engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=ec2.InstanceType(cfg.db_instance), vpc=net.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED), security_groups=[self.db_sg],
            # The password secret uses Secrets Manager's own key: granting ECS access to a secret under our
            # key would tie this stack's key policy to roles in the App stack.
            credentials=rds.Credentials.from_generated_secret("gwashub", secret_name=f"gwashub/{cfg.name}/db"),
            database_name="gwashub", parameter_group=params, multi_az=cfg.db_multi_az,
            allocated_storage=20, max_allocated_storage=200, storage_type=rds.StorageType.GP3,
            storage_encrypted=True, storage_encryption_key=self.key,
            backup_retention=cdk.Duration.days(cfg.db_backup_days), deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.SNAPSHOT, auto_minor_version_upgrade=True,
            cloudwatch_logs_exports=["postgresql"], copy_tags_to_snapshot=True,
            enable_performance_insights=False)
        # Password for the read-only database user (for browsing from PyCharm etc.); the user itself
        # is created by `devtools/aws_admin.sh <env> db-readonly-user`.
        self.readonly_secret = sm.Secret(self, "DbReadonly", secret_name=f"gwashub/{cfg.name}/db-readonly",
                                         description="GWASHub read-only database user",
                                         generate_secret_string=sm.SecretStringGenerator(password_length=32, exclude_punctuation=True))
        cdk.CfnOutput(self, "OutDbEndpoint", value=self.db.db_instance_endpoint_address)
        cdk.CfnOutput(self, "OutDbSecret", value=self.db.secret.secret_name)
        cdk.CfnOutput(self, "OutDbReadonlySecret", value=self.readonly_secret.secret_name)
