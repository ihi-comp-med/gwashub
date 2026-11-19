"""Data: a customer-managed KMS key, the three file buckets and the Postgres database.

Everything here is kept if the stack is deleted (RETAIN / snapshot), so a mistaken `cdk destroy`
can't lose data.

Hibernating, the database is stopped (devtools/aws_hibernate.sh) and a daily schedule stops it again: AWS starts a
stopped database by itself after 7 days.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_rds as rds
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from . import parts


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, cfg, net, hibernate: bool = False, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.files = parts.file_store(self, cfg)
        self.key, self.uploads, self.results, self.resources = (
            self.files.key, self.files.uploads, self.files.results, self.files.resources)

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
        if hibernate:
            # In this stack, not the App stack: a cross-stack reference to the database would be an export that the
            # Data stack (deployed first) couldn't remove on waking.
            role = iam.Role(self, "KeepDbStoppedRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
            role.add_to_policy(iam.PolicyStatement(actions=["rds:StopDBInstance"], resources=[self.db.instance_arn]))
            scheduler.CfnSchedule(
                self, "KeepDbStopped", name=f"gwashub-{cfg.name}-keep-db-stopped", schedule_expression="rate(1 day)",
                description="GWASHub is hibernating: stop the database if AWS has restarted it",
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                target=scheduler.CfnSchedule.TargetProperty(
                    arn="arn:aws:scheduler:::aws-sdk:rds:stopDBInstance", role_arn=role.role_arn,
                    input=self.to_json_string({"DbInstanceIdentifier": self.db.instance_identifier}),
                    # Stopping a stopped database fails; that's the usual case, so don't retry.
                    retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=0)))
        cdk.CfnOutput(self, "OutDbEndpoint", value=self.db.db_instance_endpoint_address)
        cdk.CfnOutput(self, "OutDbSecret", value=self.db.secret.secret_name)
        cdk.CfnOutput(self, "OutDbReadonlySecret", value=self.readonly_secret.secret_name)
