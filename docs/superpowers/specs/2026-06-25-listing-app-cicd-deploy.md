# CI/CD & Deployment Design — Listing Web App

Date: 2026-06-25 (written 2026-06-28)
Status: design (not yet implemented — this is Phase 4 of the listing-web-app build)
Parent spec: `docs/superpowers/specs/2026-06-25-listing-web-app-cloud-deploy-design.md` (§9)
Repo: `gopalthakur71/marketplace-bulklisting-semi-automation`
Region: `ap-south-1`

> **Why this doc exists:** the CI/CD pipeline is the *showcase* of the listing-web-app
> project — the UI is deliberately simple so the cloud/CI-CD story is the centrepiece.
> This is the **GitHub Actions** step of the personal CI/CD learning roadmap (Jenkins
> CI+CD-to-EC2 is already done; this is the modern, job-relevant equivalent). It is
> intentionally written so it can become its own implementation plan later, independent
> of the backend (Phase 1) and the web app (Phase 2–3).

---

## 1. Goal

On every push to `main`: run the test suite, and if it passes, build a Docker image of
the app and publish it to a private registry — **with no long-lived AWS keys stored in
GitHub**. The deploy target is a **start/stop EC2 t3.micro** that pulls and runs the
latest image on boot, so *starting the box = deploying the newest build*.

Non-goals here (deferred, see §9): Terraform/IaC, Auto Scaling Group / immutable AMI
deploy, blue-green, a running-instance auto-redeploy. Those are later roadmap steps; this
doc is the single-box GitHub-Actions-to-ECR pipeline.

---

## 2. Where this sits in the bigger picture

```
Developer push to main
        │
        ▼
GitHub Actions (ci-cd.yml)
   1. Checkout + Python
   2. pytest  ◄── GATE: build stops here if tests fail
   3. docker build
   4. assume AWS role via GitHub OIDC  (no stored AWS keys)
   5. docker push → Amazon ECR  (tags: <git-sha> and :latest)
        │
        ▼
Amazon ECR (private repo: marketplace-bulklisting)
        │
        ▼  (pulled on boot, NOT pushed to)
EC2 t3.micro (start-on-demand)
   user-data + systemd unit:
     - aws ecr get-login-password | docker login
     - docker pull <repo>:latest
     - docker run -p 80:8000 ...   (FastAPI app)
   all AWS access via the EC2 instance role (no keys in the container)
```

**Mental model (ties to the roadmap):** this box is still a **pet** — one named instance
we start, stop, and let pull its own code. That is correct for a low-traffic internal
tool. The "pets → cattle" shift (ASG + bake-AMI + instance-refresh) is a deliberate later
step; we are *not* doing it now, and the parent spec records that as out of scope.

---

## 3. The pipeline file — `.github/workflows/ci-cd.yml`

```yaml
name: ci-cd

on:
  push:
    branches: [main]
  workflow_dispatch:        # manual "Run workflow" button

# OIDC needs id-token: write; checkout needs contents: read
permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: ap-south-1
  ECR_REPOSITORY: marketplace-bulklisting

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install -r requirements.txt
      - name: Run tests (deploy gate)
        run: python -m pytest -q

  build-and-push:
    needs: test                      # only runs if `test` passed
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/github-actions-ecr-push
          aws-region: ${{ env.AWS_REGION }}

      - name: Log in to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        env:
          REGISTRY: ${{ steps.login-ecr.outputs.registry }}
        run: |
          IMAGE=$REGISTRY/$ECR_REPOSITORY
          docker build -t $IMAGE:${{ github.sha }} -t $IMAGE:latest .
          docker push $IMAGE:${{ github.sha }}
          docker push $IMAGE:latest
```

Notes:
- Two jobs, not one: the `test` job is the **gate**; `build-and-push` has `needs: test`
  so a red suite never produces an image. (Same idea as a Jenkins `Test` stage failing
  the build before `Deploy`.)
- `github.sha` tag = an immutable, traceable artifact per commit. `:latest` = what the
  EC2 box pulls. Rollback = re-tag a previous SHA as `:latest`, or pin the box to a SHA.

---

## 4. GitHub OIDC → AWS (the "no stored keys" part)

This is the single most important detail for the showcase. Instead of putting an AWS
access key/secret in GitHub Secrets (long-lived, leakable), GitHub Actions presents a
short-lived **OIDC token** that AWS trades for temporary credentials. Nothing durable is
stored in GitHub.

**One-time AWS setup (console or CLI):**

1. **Create the OIDC identity provider** in IAM (once per account):
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`

2. **Create the role** `github-actions-ecr-push` with a trust policy that only this
   repo's `main` branch can assume:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:gopalthakur71/marketplace-bulklisting-semi-automation:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

3. **Attach a least-privilege permissions policy** to that role — push to this one ECR
   repo only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPushPull",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "arn:aws:ecr:ap-south-1:<ACCOUNT_ID>:repository/marketplace-bulklisting"
    }
  ]
}
```

`ecr:GetAuthorizationToken` must be `Resource: "*"` (it's an account-level action); every
other action is scoped to the single repo ARN.

---

## 5. The image — `Dockerfile`

A single, small image that runs the FastAPI app. (Phase 3 of the web-app build produces
this; included here so the pipeline has something concrete to build.)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# FastAPI served by uvicorn on 8000; container is stateless (durable state = S3)
EXPOSE 8000
CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Keep it slim: `python:3.12-slim`, deps copied before source for cache reuse, no build
tools left in the final layer. (If `Pillow`/`openpyxl` need system libs, add a minimal
`apt-get install` step — flag at build time, don't pre-add speculatively.)

---

## 6. ECR repository

- One **private** repo: `marketplace-bulklisting` in `ap-south-1`.
- Enable **scan-on-push** (free image vulnerability scan).
- Add a **lifecycle policy** so old SHA-tagged images don't accumulate cost, e.g. "keep
  last 10 images, expire the rest." Storage is ~$0.10/GB-month, so this is pennies, but
  it's good hygiene and a nice thing to show.

---

## 7. The deploy target — EC2 t3.micro (start-on-demand)

Because the box is started on demand, **boot = deploy**. No push-to-server step, no SSH
deploy key, no hardcoded host IP in the pipeline (contrast with the Jenkins single-box
setup, which SSH-pulled — that pattern is deliberately dropped here).

**user-data** (runs on first boot; installs Docker + a systemd unit that always pulls the
latest image on start):

```bash
#!/bin/bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker

cat >/etc/systemd/system/listing-app.service <<'UNIT'
[Unit]
Description=Marketplace Listing App
After=docker.service
Requires=docker.service

[Service]
# pull-on-start = deploy newest build whenever the box is started
ExecStartPre=-/usr/bin/docker rm -f listing-app
ExecStartPre=/bin/bash -lc 'aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com'
ExecStartPre=/usr/bin/docker pull <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStart=/usr/bin/docker run --rm --name listing-app -p 80:8000 \
  <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStop=/usr/bin/docker stop listing-app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl enable --now listing-app.service
```

**EC2 instance role** (`listing-app-ec2-role`) — no static keys in the container; the app
and the pull both use this role. Least privilege:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrPull",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AppS3",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": [
        "arn:aws:s3:::ijorethnicpartners/myntra/*",
        "arn:aws:s3:::ijorethnicpartners/state/myntra_groupid.json"
      ]
    },
    {
      "Sid": "AppConfig",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
      "Resource": "arn:aws:ssm:ap-south-1:<ACCOUNT_ID>:parameter/listing-app/*"
    },
    {
      "Sid": "AppSecret",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-south-1:<ACCOUNT_ID>:secret:listing-app/*"
    }
  ]
}
```

(`ecr:GetAuthorizationToken` is account-level → `*`; everything else is scoped.) The S3,
SSM, and Secrets entries match the runtime needs from the parent spec §7 (image bucket,
styleGroupId ledger, app config, Cognito client secret).

---

## 8. Cost

- **EC2 t3.micro stopped:** you pay only for the EBS root volume — ~**$0.5–0.75/mo** for
  an 8 GB gp3 volume. Compute is $0 while stopped.
- **EC2 running:** t3.micro on-demand in ap-south-1 ≈ $0.0112/hr ≈ **~$0.27/day** if left
  on; started only when listing, the monthly compute cost is negligible.
- **ECR:** ~$0.10/GB-month storage; with a 10-image lifecycle policy, pennies.
- **GitHub Actions:** free minutes cover this (and free entirely if the repo is public).
- **No NAT gateway, no ALB, no Elastic IP** (use the auto-assigned public IP, which changes
  per start — acceptable for an internal tool). These are the usual silent cost sinks and
  we avoid all three.

Effective idle cost ≈ **under $1/month**.

---

## 9. Deferred / out of scope (and why)

| Deferred | Why now | When to revisit |
|---|---|---|
| Terraform / IaC for the box | One box, set up once; hand-config is fine to start | Roadmap step after GitHub Actions — provision EC2/IAM/SG/ECR as `.tf`, split infra vs app pipelines |
| ASG + immutable AMI deploy | Single low-traffic box doesn't need autoscaling; pull-on-boot suffices | If usage grows / needs HA → bake-AMI + instance-refresh (pets→cattle) |
| Auto-redeploy a *running* instance | Box is start-on-demand, so boot already deploys latest | Add a GitHub Actions step issuing an **SSM Run Command** to `docker pull && systemctl restart` if we ever keep it running |
| Blue/green / CodeDeploy | Overkill for one box | Comes with the CodePipeline+CodeBuild redo (most job-relevant, later roadmap step) |
| Staging environment | Single internal tool, trusted team | If a second marketplace or external users arrive |

---

## 10. Implementation checklist (for when we build this)

One-time AWS (console/CLI):
- [ ] Create ECR repo `marketplace-bulklisting` (scan-on-push + 10-image lifecycle policy).
- [ ] Create GitHub OIDC identity provider in IAM.
- [ ] Create role `github-actions-ecr-push` (trust = this repo's `main`; perms = ECR push).
- [ ] Create EC2 instance role `listing-app-ec2-role` (ECR pull + S3 + SSM + Secrets).
- [ ] Put non-secret config in SSM `/listing-app/*`; Cognito client secret in Secrets Manager.
- [ ] Launch t3.micro with the instance role + user-data; security group opens 80 (and 443 if TLS added).

Repo:
- [ ] Add `Dockerfile` (built/owned by web-app Phase 3).
- [ ] Add `.github/workflows/ci-cd.yml` (§3).
- [ ] Push to `main`, confirm: tests gate → image in ECR → start box → app reachable on the public IP.

Each unchecked box is a concrete, reviewable step; this list becomes the CI/CD
implementation plan when we get to it.
```
