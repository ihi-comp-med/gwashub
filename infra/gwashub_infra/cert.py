"""The site's TLS certificate. CloudFront only takes certificates from us-east-1, so this stack lives
there; the App stack reads it through a cross-region reference."""
import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct


class CertStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, cfg, **kwargs):
        super().__init__(scope, id, **kwargs)
        zone = route53.HostedZone.from_lookup(self, "Zone", domain_name=cfg.domain)
        self.certificate = acm.Certificate(self, "Site", domain_name=cfg.domain,
                                           validation=acm.CertificateValidation.from_dns(zone))
