# Setting up the AWS accounts for GWASHub (beginner's guide)

This guide takes you from no AWS account to a secure set of accounts that GWASHub can be deployed
into. It assumes no AWS experience. Allow **2–3 hours**, plus about an hour of waiting while AWS
builds things. You can stop between parts.

When you finish you will have:

- a **management account** used only for billing and administration;
- separate **dev** and **prod** accounts for GWASHub;
- **log archive** and **audit** accounts that record and watch everything;
- a personal sign-in with **two-factor authentication**, which you use instead of passwords for each account;
- spending alerts, audit logging, threat detection, and a rule that keeps everything in London.

The next guide, `AWS_DEPLOYMENT.md`, covers putting GWASHub into these accounts.

> **Status (2026-10-03):** done for GWASHub without Control Tower: an AWS Organization with OUs
> **Security** (gwashub-audit, gwashub-log-archive) and **Workloads** (gwashub-dev, gwashub-prod); root MFA;
> an organisation-wide CloudTrail trail; GuardDuty, IAM Access Analyzer and Security Hub delegated to
> gwashub-audit; a region guardrail that includes London. Still open: Security Hub standards (need AWS
> Config), GuardDuty in the management account outside London, budget alerts, and Identity Center access
> assignments for gwashub-dev and gwashub-prod (today they're reached by switching role,
> `OrganizationAccountAccessRole`). An unused account created by the setup wizard ("GWASHub",
> 783951396765, in a Sandbox OU) is to be closed and the Sandbox OU deleted.

> The AWS console's layout changes from time to time. Where this guide says "open X", use the
> **search bar at the top of the console** and type the service name (e.g. "Control Tower").

---

## Words you'll meet

| Term | Meaning |
|---|---|
| **Account** | A separate container for AWS resources, with its own bill and security boundary. We'll have five. |
| **Root user** | The email and password an account was created with. All-powerful; used almost never. |
| **Organization** | A group of accounts managed together, with one combined bill. |
| **OU (organizational unit)** | A folder of accounts inside the organization, so rules can apply to a whole folder. |
| **Region** | A geographic location of AWS data centres. We use **eu-west-2 (London)**. |
| **IAM Identity Center** | AWS's single sign-on. You get one login (with MFA) that opens any account you're allowed into. |
| **Permission set** | A named bundle of permissions ("Administrator", "Read-only") you grant someone in an account. |
| **MFA** | Multi-factor authentication: a code from an app on your phone, or a security key, as well as your password. |
| **Control Tower** | AWS's guided setup for a secure multi-account organization ("landing zone"). |
| **CloudTrail** | A record of every action taken in your accounts: who did what, when. |
| **GuardDuty** | Threat detection: watches for suspicious activity and alerts you. |
| **Security Hub** | A dashboard that checks your setup against security best practice. |
| **CLI** | The command-line tool for AWS, used later for deployment. |

---

## Part 0: Check with your institution first (15 minutes)

Many universities buy AWS centrally, through Jisc or an enterprise agreement. Ask your IT or
research-computing team:

1. *"Do we have an AWS organization I can have accounts in?"* If yes, they may set up most of this
   guide for you (Parts 2–4), and you get institutional pricing and the **data egress waiver**
   (free downloads up to a limit). Give them this guide and ask for: dev and prod accounts in
   eu-west-2, administrator access for you via single sign-on, and CloudTrail, GuardDuty and Security
   Hub turned on.
2. *"Who handles data protection impact assessments (DPIAs) for research data in the cloud?"* Start
   that conversation now; it can take weeks.
3. *"Is there a purchase order or research grant code to pay for it?"* Otherwise AWS bills a card monthly.

If your institution provides accounts, skip to **Part 5** for whatever they haven't done.

---

## Part 1: Prepare email addresses (10 minutes)

Every AWS account needs its **own email address that has never been used with AWS**. You need five.
Don't use a personal inbox: if you leave, the consortium must still be able to recover the accounts.

The easiest route is one shared mailbox (ask IT, e.g. `aws-gwashub@your-uni.ac.uk`) plus
**plus-addressing**, which most email systems (Microsoft 365, Gmail) support. Mail to
`name+anything@` arrives in `name@`:

| Account | Email |
|---|---|
| Management | `aws-gwashub@your-uni.ac.uk` |
| Log archive | `aws-gwashub+logs@your-uni.ac.uk` |
| Audit | `aws-gwashub+audit@your-uni.ac.uk` |
| Dev | `aws-gwashub+dev@your-uni.ac.uk` |
| Prod | `aws-gwashub+prod@your-uni.ac.uk` |

Send a test email to a `+` address first to check it arrives. If it doesn't, ask IT for five aliases instead.

**No institutional mailbox yet?** A dedicated free mailbox works: Gmail, Outlook.com and Proton Mail
all support plus-addressing.

1. Create one **only for this**, e.g. `gwashub.aws@gmail.com`, then use `gwashub.aws+logs@gmail.com`,
   `+audit`, `+dev` and `+prod` for the other accounts.
2. Secure it like the AWS root user, because it can reset every account's root password:
   - a unique password in a password manager;
   - 2-Step Verification with a security key or authenticator app (not SMS);
   - a recovery phone, and a colleague's email as the recovery address.
3. Share it securely with a second admin, so the consortium isn't locked out if you're unavailable.
4. When the university provides a shared mailbox, change each account's root email to it (in each
   account's settings; member accounts can now be changed from the management account; check the
   current AWS docs). Keep the old mailbox until every account has moved.

You'll also want an **authenticator app** on your phone (Microsoft Authenticator, Google
Authenticator or 1Password), and ideally a **hardware security key** (e.g. YubiKey) for the root user.

---

## Part 2: Create the management account and lock it down (30 minutes)

### 2.1 Sign up

> **Already signed up through the new experience and activated advanced features?** Skip to
> **Appendix A** at the end of this guide.
>
> **Use the standard ("advanced") sign-up, not AWS's new simplified experience.** Since 2026, AWS
> offers some new customers a simplified experience: a site at `settings.aws.com` with "Projects",
> a region AWS picks for you (which can't be changed), and AWS-managed guardrails. It's fine for
> quick experiments, but GWASHub needs to choose its region (London), set its own security policies
> and use every service. If you see "Projects" and "AWS Settings" after signing up, you're in the
> simplified experience: start again with the link below.

1. Go to <https://signin.aws.amazon.com/signup?request_type=register> (AWS's "Sign up for AWS
   (advanced)" page).
2. Root user email: the management address (`aws-gwashub@…`). Account name: `gwashub-management`.
3. Verify the email with the code AWS sends, and choose a **long, unique root password**. Store it
   in a password manager the consortium controls, not just your own.
4. **Account plan:** choose **Paid plan**. You still get the $100 of new-customer credits. The Free
   plan closes the account after six months, limits which services you can use, and switches to
   paid automatically as soon as you set up Control Tower anyway. The budget alerts in 2.4 protect
   you from surprises.
5. Contact information: choose **Business** (or Professional) and use your institution's address.
6. Billing: enter the card or invoice details. AWS may place a small temporary charge to verify it.
7. Verify the phone number.
8. Support plan: choose **Basic support (free)**.
9. Wait for the "account is ready" email (minutes, occasionally hours), then sign in to the
   console as the root user.

### 2.2 Protect the root user (do this immediately)

1. Top right, click the account name → **Security credentials**.
2. Under **Multi-factor authentication (MFA)** choose **Assign MFA device**.
3. Name it (e.g. `root-yubikey`), pick **Passkey or security key** (best) or **Authenticator app**,
   and follow the prompts. Ideally register **two** devices (e.g. a key, plus an app on a second
   person's phone), so losing one doesn't lock you out.
4. On the same page, confirm there are **no access keys**. If there are any, delete them.

From now on, sign in as root only for the few tasks that need it (closing accounts, some billing settings).

### 2.3 Let administrators see the bill

1. Account name (top right) → **Account**.
2. Find **IAM user and role access to Billing information** → **Edit** → tick **Activate IAM Access** → **Update**.

Without this, the people you give admin access to later can't see costs.

### 2.4 Set up spending alerts

1. Open **Billing and Cost Management** → **Budgets** → **Create budget**.
2. Choose **Use a template** → **Monthly cost budget**.
3. Name `gwashub-monthly`, amount e.g. **$100** to start (raise it once you know real costs), and
   add the email addresses of at least two people.
4. **Create budget**. AWS emails at 85% and 100% of actual spend, and when forecast spend will exceed it.
5. Also open **Cost Anomaly Detection** (inside Billing and Cost Management) → **Create monitor** →
   **AWS services** → subscribe the same emails with **daily** alerts. It catches sudden unusual
   spend, e.g. something left running.

---

## Part 3: Build the secure foundation with Control Tower (about 1 hour, mostly waiting)

Control Tower creates the organization, the log archive and audit accounts, organization-wide
CloudTrail, AWS Config, a region restriction, and single sign-on, all in one wizard.

**Before you start:** check the console shows **Europe (London) eu-west-2** in the region menu
(top right). Control Tower's "home region" can't easily be changed later.

1. Open **Control Tower** → **Set up landing zone**.
2. **Home Region:** Europe (London) eu-west-2.
3. **Region deny setting:** **Enabled**. This blocks resources being created outside the regions you allow.
4. **Additional regions for governance:** add **US East (N. Virginia) us-east-1**. A few global
   services (CloudFront certificates, some billing features) must live there; nothing else will.
5. **Organizational units:**
   - Foundational OU: keep the name **Security**.
   - Additional OU: rename it from "Sandbox" to **Workloads**. GWASHub's dev and prod accounts will go here.
6. **Shared accounts:**
   - Log archive: email `aws-gwashub+logs@…`, name `gwashub-log-archive`.
   - Audit: email `aws-gwashub+audit@…`, name `gwashub-audit`.
7. **Additional configurations:**
   - **AWS account access configuration:** choose **AWS IAM Identity Center** (Control Tower sets it up for you).
   - **CloudTrail:** **Enabled** (organization-level).
   - Log retention: e.g. **1 year** for logs, **10 years** for access logs, or what your institution requires.
   - **KMS encryption:** optional. Leave it off for now if unsure; logs are still encrypted by default.
8. Review, tick the acknowledgements, and choose **Set up landing zone**.

This takes **30–60 minutes**. You'll get emails for the two new accounts. You don't need to
sign in to them as root, but **set a root password and MFA on each** (Part 3.1) so nobody else can claim them.

### 3.1 Secure the root users of the new accounts

For the log archive and audit accounts (and dev and prod after Part 4):

1. Go to the AWS sign-in page, choose **Root user**, enter that account's email, and choose **Forgot password**.
2. Set a password from the reset email, sign in, and add MFA exactly as in Part 2.2.
3. Sign out. You should never need these root logins again.

---

## Part 4: Create the dev and prod accounts (20 minutes)

1. Open **Control Tower** → **Account Factory** → **Create account**.
2. Fill in:
   - **Account email:** `aws-gwashub+dev@…`
   - **Display name:** `gwashub-dev`
   - **IAM Identity Center user email:** your own work email. This creates your single sign-on
     user if it doesn't exist yet.
   - **Organizational unit:** **Workloads**
3. **Create account** and wait (about 20–30 minutes) until its state is **Enrolled**.
4. Repeat for **prod**: `aws-gwashub+prod@…`, display name `gwashub-prod`, OU Workloads.
5. Secure both root users (Part 3.1).

---

## Part 5: Set up your single sign-on and MFA (30 minutes)

### 5.1 Require MFA for everyone

1. Open **IAM Identity Center** (make sure the region menu says London).
2. **Settings** → **Authentication** tab → **Multi-factor authentication** → **Configure**.
3. Choose:
   - Prompt users for MFA: **Every time they sign in**.
   - Users can authenticate with: **Security keys and built-in authenticators** and **Authenticator apps**.
   - If a user doesn't have a registered MFA device: **Require them to register an MFA device at sign in**.
4. **Save changes**.

### 5.2 Give the sign-in portal a memorable address

1. **Settings** → **Identity source** tab → **AWS access portal URL** → **Customize**.
2. Choose e.g. `gwashub` so the address becomes `https://gwashub.awsapps.com/start`. **Bookmark it**:
   from now on this is how you sign in to AWS, not the root user.

### 5.3 Create groups and permission sets

1. **Groups** → **Create group**: `gwashub-admins` (full control). Optionally also
   `gwashub-readonly` (for colleagues who only need to look).
2. Add yourself, and a second trusted admin, to `gwashub-admins` (**Users** → **Add user** for anyone
   new; they'll get an invitation email).
3. **Permission sets** → **Create permission set** → **Predefined permission set**:
   - **AdministratorAccess**, session duration **4 hours**;
   - **ReadOnlyAccess**, session duration **8 hours**;
   - **Billing**, for whoever looks after costs.

### 5.4 Give the groups access to the accounts

1. **AWS accounts** → tick **gwashub-dev** and **gwashub-prod** → **Assign users or groups**.
2. **Groups** tab → tick `gwashub-admins` → **Next** → tick **AdministratorAccess** → **Next** → **Submit**.
3. Repeat for `gwashub-readonly` with **ReadOnlyAccess**, if you created it.
4. For the **audit** and **log-archive** accounts, give `gwashub-admins` **ReadOnlyAccess** only
   (nobody should change logs). Leave the management account to one or two people.

### 5.5 Try it

1. Sign out of the console completely.
2. Open your portal URL from 5.2, sign in with your work email, set your password, and register MFA when prompted.
3. You should see **gwashub-dev** and **gwashub-prod**. Click **AdministratorAccess** next to dev to open its console.

From now on, use only this portal.

---

## Part 6: Turn on threat detection and security checks (30 minutes)

These run organization-wide and are managed from the **audit** account, which Control Tower made for this purpose.

### 6.1 Find the audit account's ID

**Control Tower** → **Organization** (or **AWS Organizations**) → note the 12-digit **Account ID** of `gwashub-audit`.

### 6.2 GuardDuty (threat detection)

1. In the **management account** (via the portal, or root if needed), open **GuardDuty** →
   **Get started** (if asked) → **Settings** → **Delegated administrator**: enter the audit account
   ID → **Delegate**.
2. Switch to the **audit account** (portal → gwashub-audit). You may need a temporary
   AdministratorAccess assignment for this; remove it afterwards.
3. Open **GuardDuty** → **Accounts** → choose **Auto-enable GuardDuty for all member accounts** (and existing accounts).
4. Under **Protection plans**, turn on **S3 Protection**. (**Malware Protection for S3** is enabled
   per bucket later, on the upload bucket, during deployment.)
5. Set up alerts: **Settings** → **Findings export options**, or simpler, in **Amazon EventBridge**
   create a rule for GuardDuty findings that emails your admins via an SNS topic. The deployment
   guide's monitoring stack automates this later, so it can wait.

### 6.3 Security Hub (best-practice checks)

1. In the **management account**, open **Security Hub** → **Settings** → **Delegated administrator**: audit account ID.
2. In the **audit account**, open **Security Hub** → choose **central configuration** when offered,
   and apply a policy to the whole organization with these standards:
   **AWS Foundational Security Best Practices** and **CIS AWS Foundations Benchmark**.
3. Expect a list of findings at first. Many will be resolved once the deployment follows the guide;
   revisit it monthly.

### 6.4 IAM Access Analyzer (spots anything shared outside your organization)

In the **audit account**, open **IAM** → **Access analyzer** → **Create analyzer** → zone of trust:
**Organization**. It flags any bucket, key or role reachable from outside.

---

## Part 7: Command-line access on your computer (15 minutes)

Deployment (`cdk deploy`) runs from your computer or from GitHub, using your single sign-on. No passwords or keys are saved.

1. Install the AWS CLI. On a Mac: `brew install awscli` (or the installer from the AWS website).
2. Run `aws configure sso` and answer:
   - SSO session name: `gwashub`
   - SSO start URL: your portal URL from 5.2
   - SSO region: `eu-west-2`
   - Registration scopes: press Enter for the default
3. A browser opens. Sign in and approve. Back in the terminal, pick **gwashub-dev** and
   **AdministratorAccess**; default region `eu-west-2`; output `json`; profile name `gwashub-dev`.
4. Repeat `aws configure sso` for prod, choosing gwashub-prod, with profile name `gwashub-prod`.
5. Test: `aws sts get-caller-identity --profile gwashub-dev` should print the dev account ID and your role.
6. Each day, sign in with `aws sso login --sso-session gwashub`. Sessions expire after a few hours, which is intended.

**Never** create "access keys" for yourself in the IAM console. If a tool asks for them, there's
almost always an SSO or role-based alternative.

---

## Part 8: Let GitHub deploy without keys (later, 20 minutes)

Do this when you're ready to set up automatic deployment (deployment guide §11). In **each** of dev and prod:

1. Open **IAM** → **Identity providers** → **Add provider** → **OpenID Connect**.
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
2. **IAM** → **Roles** → **Create role** → **Web identity** → choose that provider, audience
   `sts.amazonaws.com`, and restrict it to your GitHub organization and repository (and, for prod,
   the `main` branch or a GitHub environment). The trust policy should contain a condition like:
   ```json
   "Condition": {
     "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
     "StringLike":   { "token.actions.githubusercontent.com:sub": "repo:YOUR-ORG/gwashub:ref:refs/heads/main" }
   }
   ```
3. Permissions: start with the CDK deployment permissions (`cdk bootstrap` creates suitable roles;
   allow this role to assume them), name it `gwashub-github-deploy`, and put its ARN in the GitHub
   workflow. GitHub then exchanges a short-lived token for temporary AWS credentials on every run.

---

## Part 9: Checklist

- [ ] Root user of **all five** accounts: strong password in a shared password manager, MFA on, no access keys
- [ ] Billing alerts and cost anomaly detection email at least two people
- [ ] Control Tower landing zone **Enrolled**: London home region, region deny on, us-east-1 governed
- [ ] OUs: Security (log archive, audit) and Workloads (dev, prod)
- [ ] IAM Identity Center: MFA required at every sign-in; portal URL bookmarked; two admins in `gwashub-admins`
- [ ] Admins can open dev and prod via the portal; audit and log archive are read-only
- [ ] GuardDuty and Security Hub delegated to the audit account and enabled organization-wide
- [ ] IAM Access Analyzer with an organization zone of trust
- [ ] AWS CLI works with `--profile gwashub-dev` via SSO
- [ ] Notes kept (in a shared, secure place): account IDs, portal URL, who the admins are, where root credentials and MFA devices are

---

## What this costs before GWASHub is deployed

Control Tower itself is free; you pay for what it switches on. With no workloads, expect roughly
**$5–30 a month**: AWS Config recordings, CloudTrail log storage, GuardDuty and Security Hub checks.
GuardDuty and Security Hub offer free trials for the first 30 days.

## If something goes wrong

- **Locked out of the root user** (lost MFA): use **Troubleshoot MFA** on the sign-in page; AWS
  verifies by email and phone. This is why two MFA devices and a shared, recoverable email matter.
- **Control Tower setup failed:** open **Control Tower** → **Landing zone settings** → **Repair**.
  If that fails, AWS Support (Basic) can help with account and billing issues.
- **Unexpected charges:** **Billing and Cost Management** → **Cost Explorer**, grouped by service,
  shows what's costing money. Nothing in Parts 1–8 should cost more than a few dollars a day.

---

## Appendix A: If you started in AWS's new experience and activated advanced features

Use this route instead of Parts 2–5 if you signed up through the simplified experience
(`settings.aws.com`, "Projects") and then chose **Explore advanced features → activate**. That
can't be undone, but it leaves you in a good position: AWS has already created an organization,
single sign-on (IAM Identity Center) and two member accounts. You do the rest by hand instead of with
Control Tower. Control Tower expects to set up single sign-on itself, so it's awkward to add to an
organization that already has it in a different region.

What you have after activation:

| Thing | Notes |
|---|---|
| **Management account** (e.g. `gwashub-management-team`) | You now administer it; it has a root user you must secure |
| **Organization** | With AWS-written guardrail policies (SCPs) you can now edit |
| **IAM Identity Center** | Sign-in uses your **AWS Builder ID**; your team name is the instance name |
| **Identity Delegated Admin** account | Created by AWS to administer Identity Center; used in **US East (N. Virginia)** |
| **Your first project** (e.g. "Console Dot Dream") | A member account; keep it as a sandbox, or close it later |
| **Region** | AWS chose one (e.g. Stockholm). GWASHub's accounts and data will go in **London** |

The region AWS chose only affects where the sign-in directory lives. Your data goes wherever you
create resources, which will be London.

### A.1 Secure the management account's root user (15 minutes)

1. AWS Settings (`settings.aws.com`) links to setting the management account's **root user
   password** (AWS's page "Set a root user password"). Set a long, unique password and store it in a
   password manager the consortium controls.
2. Sign in at <https://signin.aws.amazon.com> as **Root user** with that email, then follow
   **Part 2.2** (MFA, ideally two devices; no access keys).
3. Follow **Part 2.3** (billing access) and **Part 2.4** (budget and cost anomaly alerts). Activation
   removed AWS's spend limit, so budget alerts are now your safety net. Don't skip this.
4. Make sure **MFA is on your AWS Builder ID** too, since it signs you in to everything: go to
   <https://profile.aws.amazon.com> → **Security** → register an authenticator app or security key.

### A.2 Allow London in the guardrails (15 minutes)

AWS's guardrail policy restricts which regions can be used, and London may not be on the list.

1. In the management account, open **AWS Organizations** → **Policies** → **Service control
   policies** (SCPs).
2. Open the AWS-created policy that contains a statement with `"Sid": "RegionFloor"` (AWS's
   documentation names it). **Copy the whole JSON somewhere safe before changing anything.**
3. In that statement, find the list of allowed regions (`aws:RequestedRegion`) and add `"eu-west-2"`.
   Keep the regions already there: the Identity Center region and `us-east-1` must stay.
4. Save. If you're unsure, paste the JSON into this conversation and I'll check the edit with you
   before you save.

### A.3 Create the folders and accounts (30 minutes)

1. **AWS Organizations** → **AWS accounts** → select **Root** → **Actions** → **Create new**
   organizational unit twice: **Security** and **Workloads**.
2. **Add an AWS account** → **Create an AWS account**, four times (one email per account, from Part 1):

   | Account name | Email | Then move it to |
   |---|---|---|
   | `gwashub-log-archive` | `…+gwashub-logs@…` | Security |
   | `gwashub-audit` | `…+gwashub-audit@…` | Security |
   | `gwashub-dev` | `…+gwashub-dev@…` | Workloads |
   | `gwashub-prod` | `…+gwashub-prod@…` | Workloads |

   Leave the IAM role name as the default (`OrganizationAccountAccessRole`).
3. Move each account into its folder: select it → **Actions** → **Move**.
4. Secure each new account's root user (**Part 3.1**).
5. Your first project account (e.g. "Console Dot Dream"): keep it as a personal sandbox for
   experiments, outside the Workloads folder. Don't put GWASHub data in it.

### A.4 Single sign-on for the new accounts (20 minutes)

Identity Center is administered from the **Identity Delegated Admin** account, in **US East (N. Virginia)**.

1. In AWS Settings or your access portal, open the Identity Delegated Admin account, then **IAM
   Identity Center**. Switch the region menu to **US East (N. Virginia)** if the console asks.
2. Follow **Part 5.3** (group `gwashub-admins`, permission sets) and **Part 5.4** (assign the group to
   dev and prod with AdministratorAccess, and to audit and log-archive with ReadOnlyAccess).
3. Your Builder ID is already your sign-in. Invite colleagues from AWS Settings (team members) or
   from Identity Center. **Don't change the identity source** (e.g. to your university's login)
   without thinking it through: that change is irreversible.
4. Sign out and back in through AWS Settings or the access portal, and check you can open
   gwashub-dev and gwashub-prod.

### A.5 Organization-wide audit log (CloudTrail) (20 minutes)

1. In the management account, set the region menu to **Europe (London)** and open **CloudTrail** →
   **Trails** → **Create trail**.
2. Name `gwashub-org-trail`; tick **Enable for all accounts in my organization**.
3. Storage: **Create new S3 bucket** (e.g. `gwashub-org-cloudtrail-<random>`), **log file
   validation** on, and **SSE-KMS encryption** with a new key if offered.
4. Management events: Read and Write. Create the trail.

The deployment guide later moves this bucket into `gwashub-log-archive` as code. Starting in the
management account is fine.

### A.6 Then continue with the main guide

- **Part 6:** GuardDuty, Security Hub and IAM Access Analyzer, delegated to `gwashub-audit`. Do these
  in **Europe (London)**, and for GuardDuty enable it in your other active regions too.
- **Part 7:** command-line access. With a Builder ID sign-in, AWS's current method is
  `aws login --profile gwashub-dev --region eu-west-2` (browser sign-in, no stored keys). If that
  doesn't list your new accounts, use `aws configure sso` with your access portal URL as in Part 7.
- **Parts 8 and 9:** as written.

Control Tower can still be added later if you want its automatic guardrails. Treat that as a
separate, careful project, and ask AWS Support or a consultant first, because the organization
already has its own single sign-on and policies.
