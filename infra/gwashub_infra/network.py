"""VPC: public subnets (NAT only), private subnets (API, worker, Batch, internal load balancer),
isolated subnets (database). S3 traffic stays inside AWS through a gateway endpoint."""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, cfg, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.vpc = ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=cfg.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=20),
                ec2.SubnetConfiguration(name="db", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
            gateway_endpoints={"S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3)},
        )
        # Everything that runs GWASHub code (API, worker, admin tasks, Batch jobs) uses this group;
        # the database accepts connections only from it.
        self.app_sg = ec2.SecurityGroup(self, "AppSg", vpc=self.vpc, description="GWASHub API, worker and jobs",
                                        allow_all_outbound=True)
        # The internal load balancer. Its only inbound rule (CloudFront's VPC origin) is added by the
        # App stack, which looks up CloudFront's managed prefix list.
        self.alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=self.vpc, description="GWASHub internal load balancer (CloudFront only)",
                                        allow_all_outbound=False)
