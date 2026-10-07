# GWASHub's AWS accounts

What the AWS Organization looks like, what's still open, and the steps to rebuild it. Deploying GWASHub into these
accounts is in `docs/AWS_FULL.md`.

## What exists (set up 2026-10-03)

An AWS Organization, built by hand (not Control Tower) after activating AWS's "advanced features":

| Account | ID | OU | Use |
|---|---|---|---|
| Management | 367722595572 | Root | Billing, Organizations, the CloudTrail trail; sign in here, then switch into the others |
| gwashub-dev | 203248996074 | Workloads | The dev deployment |
| gwashub-prod | 269626333085 | Workloads | Production (configured, not deployed) |
| gwashub-audit | | Security | Delegated admin for GuardDuty, Security Hub, IAM Access Analyzer |
| gwashub-log-archive | | Security | For logs (the trail's bucket is still in the management account) |

- Root users have MFA; no access keys.
- An organisation-wide CloudTrail trail (`gwashub-org-trail`, London, log file validation).
- GuardDuty, IAM Access Analyzer and Security Hub delegated to gwashub-audit.
- A region guardrail (the AWS-written SCP with `"Sid": "RegionFloor"`) that includes eu-west-2 and us-east-1.
- dev and prod are reached by switching role (`OrganizationAccountAccessRole`) from the management account; the CLI
  does the same through the profiles in `docs/AWS_FULL.md` → "One-off setup on your Mac".

## Still open

- [ ] Security Hub standards (they need AWS Config switched on)
- [ ] GuardDuty in the management account's other active regions
- [ ] Budget alerts and Cost Anomaly Detection, emailing at least two people (Billing → Budgets; AWS's spend limit went
      when advanced features were activated, so these are the safety net)
- [ ] IAM Identity Center access for gwashub-dev and gwashub-prod (group `gwashub-admins` with AdministratorAccess;
      audit and log archive read-only), so people sign in to the portal instead of switching role
- [ ] A second admin with MFA, so access never depends on one person
- [ ] Close the unused account the setup wizard created ("GWASHub", 783951396765) and delete its Sandbox OU
- [ ] Move the CloudTrail bucket into gwashub-log-archive
- [ ] GitHub OIDC provider and a deploy role only `main` can assume (with CI/CD)
- [ ] Keep notes in a shared, secure place: account IDs, admins, where root credentials and MFA devices are

## Rebuilding it from scratch

1. **Ask first:** your institution may have an AWS agreement, credits or a data egress waiver; check the data
   region and any data-access agreements.
2. **Email addresses:** one per account, on a shared role mailbox (e.g. `aws-gwashub+dev@…`), not a personal inbox.
3. **Management account:** sign up (paid plan), set the root password, turn on MFA (two devices), let IAM users see
   billing, and set a budget alert straight away.
4. **Organization:** activate advanced features (or use Control Tower on a fresh account). Create OUs **Security**
   and **Workloads**; create the four accounts above (keep the role name `OrganizationAccountAccessRole`), move them
   into their OUs and secure each root user.
5. **Guardrail:** in Organizations → Policies → SCPs, add `eu-west-2` to the allowed regions (keep us-east-1 and the
   Identity Center region). Copy the JSON before editing.
6. **Audit log:** in London, CloudTrail → create trail for all accounts, new S3 bucket, log file validation, SSE-KMS.
7. **Detection:** GuardDuty, Security Hub and IAM Access Analyzer (organization zone of trust), delegated to the audit
   account.
8. **Sign-in:** Identity Center with MFA at every sign-in; the group and permission sets above.
9. **CLI:** AWS CLI v2.32+ and `aws login` (no stored keys), as in `docs/AWS_FULL.md`.

Before workloads, this costs a few dollars a month (CloudTrail storage, GuardDuty, Security Hub).

## If something goes wrong

- **Lost root MFA:** "Troubleshoot MFA" on the sign-in page; AWS verifies by email and phone (hence the shared mailbox
  and two MFA devices).
- **Unexpected charges:** Billing and Cost Management → Cost Explorer, grouped by service.
- **`AccessDenied` on `OrganizationAccountAccessRole` from the CLI:** `aws login` copied a session from inside
  gwashub-dev; switch the browser back to the management account and log in again.
