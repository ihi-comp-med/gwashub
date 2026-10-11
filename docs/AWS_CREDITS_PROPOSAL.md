# AWS Cloud Credit for Research: project proposal

https://pages.awscloud.com/aws-cloud-credit-for-research.html

The proposal for AWS Cloud Credit for Research (October 2026), requesting USD 7,500 to run GWASHub's production
environment for its first year. The budget comes from the AWS Pricing Calculator estimate
https://calculator.aws/#/estimate?id=839531ca98d8b7c4c2ce0371716ae55a8fa7c9de (USD 456.95 a month; breakdown and
assumptions in `docs/PRODUCTION_ROADMAP.md`, Costs). The text below is the proposal as written; `[calculator link]`
stands for that link.

---

Dear AWS Team,

**Project summary**

Large genome-wide association studies (GWAS) of disease depend on combining summary statistics from many research cohorts. In the HERMES Consortium (Heart Failure Molecular Epidemiology for Therapeutic Targets), each cohort currently sends its files by email or file transfer, and a central analyst runs quality control (QC) and meta-analysis by hand on a university HPC cluster. This is slow, hard to audit, and depends on a few people.

GWASHub is a web platform, built and validated on AWS, that replaces this process. Cohorts upload their summary statistics, a versioned QC pipeline runs automatically, each contributor receives a cleaned file and a QC report, and consortium reviewers run meta-analyses with established software (GWAMA and METAL). Every run records the pipeline version, reference file versions and container image digests, so any result can be reproduced exactly.

The platform is built and running in a development environment on AWS, and has passed four validation tests: meta-analysis results match GWAMA and METAL run independently by hand, and the QC pipeline catches every error deliberately planted in test files. I am requesting credits to run the production environment for its first year, while 20 HERMES cohorts upload data for the next dilated cardiomyopathy meta-analysis.

**Research problem and significance**

Heart failure affects more than 60 million people worldwide, and its genetic architecture is still only partly understood. HERMES meta-analyses have identified loci for heart failure and dilated cardiomyopathy that point to new therapeutic targets, and each new round needs more cohorts, more ancestries and more precise phenotypes.

The bottleneck is no longer genotyping but the work of bringing summary statistics together. Files arrive in different formats, genome builds and conventions. Common errors can silently bias a meta-analysis if they are not caught: odds ratios labelled as log-odds, swapped effect alleles, the wrong genome build, rounded values, or a truncated upload. Today these checks are done by hand, differently by different analysts, and the decisions are rarely recorded. A shared platform makes QC consistent, documented and repeatable across the consortium, shortens the time from data freeze to results, and gives every cohort the same feedback on its own data.

**Objectives**

1. Run GWASHub in production for HERMES, with 20 cohorts uploading summary statistics for dilated cardiomyopathy.
2. Apply one versioned QC pipeline to every dataset: parsing and recoding, filtering, harmonisation to the reference genome with liftover to GRCh38, allele-frequency checks against 1000 Genomes, LD score regression and summary statistics, with a report for each contributor.
3. Run the consortium's meta-analyses on the platform with GWAMA and METAL, producing lead loci, locus plots and forest plots the same way every time.
4. Record the provenance of every result so any analysis can be repeated exactly.
5. Open the platform to other cardiovascular genetics consortia, which can add their own QC steps and meta-analysis methods.
6. Establish a sustainable funding model for the platform beyond the credit period (see below).

**Proposed use of AWS**

GWASHub is defined as infrastructure-as-code with the AWS CDK and deployed in Europe (London), so consortium data stays in the UK.

- Compute: AWS Batch on EC2 Spot instances (Graviton r7g and m7g, plus r7i and m7i for two x86-only tools). Each QC step runs in its own container image from Amazon ECR, and Batch scales to zero between data freezes. QC of a full-size dataset (10 to 20 million variants) costs about USD 0.25 to 0.40.
- Application: the API and job worker on AWS Fargate (ARM) behind an Application Load Balancer; the website on Amazon CloudFront, protected by AWS WAF.
- Data: uploads, QC outputs, results and reference files in Amazon S3, encrypted with AWS KMS and versioned. Study metadata and run records are in Amazon RDS for PostgreSQL (Multi-AZ, 30-day backups).
- Access and security: Amazon Cognito with multi-factor authentication; Amazon GuardDuty with malware scanning of every upload; AWS Config, AWS Security Hub and AWS CloudTrail data events, so every read and change of a file is recorded.

The AWS Pricing Calculator estimate for production is USD 457 a month (USD 5,483 a year): [calculator link]. I am requesting USD 7,500 for 12 months. That covers the production environment, a development environment for testing pipeline changes before release (about USD 120 a month), and a contingency for re-running QC when the pipeline is updated.

**Timeline and milestones**

- Months 1 to 2: production hardening (web firewall, malware scanning, alarms, access logging), information-governance sign-off, and deployment of the production environment. Milestone: production live.
- Month 3: repeat the validation tests on production, then open the platform to the 20 cohorts. Milestone: validation passed on production; first cohort uploads.
- Months 3 to 9: cohort uploads and QC, with reviewers working through each dataset's QC report. Milestone: all 20 datasets QC'd and approved.
- Months 9 to 12: dilated cardiomyopathy meta-analysis on the platform and preparation of results for publication. Milestone: meta-analysis complete.

**Sharing outcomes**

- Software: the GWASHub code, including the QC pipeline, meta-analysis wrappers, infrastructure definitions and validation tests, will be released on GitHub under an open-source licence, so any consortium can deploy its own copy on AWS.
- Results: meta-analysis summary statistics will be published with the consortium's papers and deposited in the GWAS Catalog, in line with HERMES data-sharing policy. AWS's support will be acknowledged.
- Methods: the platform and its validation will be described in a software paper or preprint.
- Feedback to cohorts: every contributing cohort receives a documented QC report on its own data.

**Future use of AWS beyond the award**

GWASHub is designed to keep running on AWS after the credit period. The first year will show the cost per cohort and per meta-analysis, and the time saved. I will use these figures to seek continuing support: from consortium and grant funding (including the next HERMES funding round and infrastructure grants), and from cost-sharing between the consortia that adopt it. Other cardiovascular genetics groups can also deploy their own copy from the open-source release on their own AWS accounts.

**AWS employees contacted:** none.

**AWS Public Data Sets:** the Pan-UK Biobank LD scores, from the Registry of Open Data on AWS (`s3://pan-ukb-us-east-1`), used for LD score regression. 1000 Genomes and the Broad GRCh38 reference genome, also on the Registry, are used as reference data. They are currently downloaded from their original hosts (EBI and the Broad); production will read them from the Registry where its copies match the versions used in validation.

I would be very grateful for AWS's support through the initial adoption phase. Once the platform is in routine use across the consortium, I believe its value to genomics research would be clear, and funding to maintain it easier to secure.

Yours sincerely,

Dr Nick Sunderland
University of Bristol
