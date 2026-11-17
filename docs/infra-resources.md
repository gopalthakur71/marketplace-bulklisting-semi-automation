# Infrastructure Resource Map — AWS + GitHub

> Every cloud resource this project uses, how they connect, and **which file in code owns it**.
> Snapshot: **2026-11-17**. Account **`048589483919`**, region **`ap-south-1`** (Mumbai).
>
> **Since 2026-11-17 the code is the source of truth.** Every AWS resource below is managed by
> Terraform in [`infra/terraform/`](../infra/terraform/), and the server's setup is managed by
> Ansible in [`infra/ansible/`](../infra/ansible/). If this page and the code disagree, the
> code wins; `terraform plan` shows the real state.
>
> Day-to-day operation (turn the server on/off, open the app):
> [`runbooks/infra-terraform-ansible.md`](runbooks/infra-terraform-ansible.md). Why it's built
> this way: [`decisions/2026-11-17-terraform-ansible-disposable-server.md`](decisions/2026-11-17-terraform-ansible-disposable-server.md).
> Costs: [`infra-costs.md`](infra-costs.md).

## How it all fits together

```mermaid
flowchart TB
    subgraph GH["GitHub — gopalthakur71/marketplace-bulklisting-semi-automation"]
        push["push to main"] --> wf[".github/workflows/ci-cd.yml"]
        wf --> jTest["job: test (pytest gate)"]
        jTest --> jBuild["job: build-and-push"]
        jBuild --> jDeploy["job: deploy"]
    end

    subgraph LAPTOP["Your laptop"]
        tf["Terraform (Git Bash)\ninfra/terraform"]
        ans["Ansible (WSL)\ninfra/ansible"]
        browser["browser → http://localhost:8000"]
    end

    subgraph AWS["AWS — account 048589483919 / ap-south-1"]
        state["S3: ijor-tfstate-048589483919\n(Terraform state)"]
        oidc["IAM OIDC provider\ntoken.actions.githubusercontent.com"]
        roleGH["IAM role: github-actions-ecr-push"]
        ecr["ECR: marketplace-bulklisting"]
        ec2["EC2: listing-app — ONLY WHILE ON\n(app_running=true; Spot planned)"]
        roleEC2["IAM role: listing-app-ec2-role"]
        sg["SG: listing-app-sg"]
        ssm["SSM Parameter Store\n/marketplace-listing/* (9 params)"]
        cog["Cognito (removal planned, phase 5)"]
        s3["S3: ijorethnicpartners\n(images + app state)"]
        xfer["S3: ijor-ansible-ssm-048589483919\n(Ansible file transfer)"]
    end

    tf -->|state + lock| state
    tf -->|creates / deletes| ec2
    ans -->|SSM session, files via| xfer
    ans -->|configures| ec2
    browser -->|SSM port forward 8000→80| ec2

    jBuild -->|assume via OIDC| oidc --> roleGH
    jBuild -->|docker push| ecr
    jDeploy -->|SSM SendCommand: systemctl restart| ec2
    ec2 -->|instance profile| roleEC2
    ec2 -->|pull :latest| ecr
    ec2 -->|read config + secrets| ssm
    ec2 -->|login| cog
    ec2 -->|read/write| s3
```

---

## The server is disposable

The EC2 server **exists only while you're using it**. `terraform apply -var app_running=false`
deletes it, `true` builds a fresh one, and Ansible configures it in a few minutes. Nothing
lives on it: product images and the app's state are in S3, config and secrets are in SSM, and
the app image is in ECR. So deleting it loses nothing.

There is **no fixed instance ID**: every "on" creates a new one. Find the current one with
`terraform output instance_id`.

---

## AWS resources

### Compute & network
| Resource | Identifier | Owned by | Notes |
|---|---|---|---|
| EC2 instance | Tag `Name=listing-app` (new ID on every build) | `infra/terraform/ec2.tf` (`aws_instance.server`) | t3.micro, Amazon Linux 2023, 8 GB encrypted gp3, IMDSv2 only, CPU credits `standard`. **No SSH key.** Created/deleted by `var.app_running`. Configured by Ansible. The CI deploy job finds it by the `listing-app` tag, so **exactly one instance may carry that tag**. |
| Security group | `sg-06ceeb9a898378bb0` (`listing-app-sg`) | `infra/terraform/network.tf` | Inbound: port 80 + 22 from one laptop IP (`laptop_cidr`) only. **Both rules are leftovers** (the box has no SSH key and access is via SSM); phase 4 deletes them so there is **zero inbound**. Outbound: all (ECR, SSM, S3). ⚠️ At import (2026-11-17) it also had `0.0.0.0/0` on 22 and 80; those were removed the same day. |
| ECR repository | `marketplace-bulklisting` | `infra/terraform/ecr.tf` | Tags `:latest` + `:<git-sha>`. Lifecycle: keep the last 10 images (to be lowered to 3). Scan on push. |

### Identity (three separate roles, least privilege)
| IAM identity | Type | Policies | Owned by | Used by |
|---|---|---|---|---|
| `github-actions-ecr-push` | Role (OIDC, no keys) | inline `ecr-push` (push to the one ECR repo), inline `ssm-deploy` (`ec2:DescribeInstances`, `ssm:SendCommand` only to instances tagged `Name=listing-app` + `AWS-RunShellScript`, read command results) | `infra/terraform/iam.tf` | CI `build-and-push` + `deploy` jobs |
| OIDC provider | `token.actions.githubusercontent.com` | trust: `repo:gopalthakur71/marketplace-bulklisting-semi-automation:ref:refs/heads/main` only | `infra/terraform/iam.tf` | lets GitHub Actions assume the role above without stored keys |
| `listing-app-ec2-role` (+ instance profile of the same name) | Role (EC2) | `listing-app-runtime` (ECR pull; S3 `ListBucket` + `Get`/`Put` on `ijorethnicpartners`; SSM `GetParameter*` on `/marketplace-listing/*`) + AWS-managed `AmazonSSMManagedInstanceCore` | `infra/terraform/iam.tf` | the app on EC2; SSM agent (CI deploys, Ansible, port forwarding) |

> There is no `listing-app-s3` policy. Older diagrams showed one; it never existed in IAM.

### Configuration & secrets — SSM Parameter Store
| Key | Value / note | Owned by |
|---|---|---|
| `/marketplace-listing/s3_bucket` | `ijorethnicpartners` | `ssm.tf` |
| `/marketplace-listing/s3_prefix` | `myntra/` | `ssm.tf` |
| `/marketplace-listing/s3_region` | `ap-south-1` | `ssm.tf` |
| `/marketplace-listing/cognito_client_id` | `29oo5dtqh8j30k2481lmffqb0e` | `ssm.tf` |
| `/marketplace-listing/cognito_pool_id` | `ap-south-1_NdxNQ1pIz` (capital **I**) | `ssm.tf` |
| `/marketplace-listing/cognito_domain` | `ijor-marketplace` | `ssm.tf` |
| `/marketplace-listing/cognito_redirect_uri` | `http://localhost:8000/auth/callback` | `ssm.tf` |
| `/marketplace-listing/cognito_client_secret` | **SecureString**. Terraform manages the parameter but **ignores its value**; the real value is set outside code and never appears in `.tf` files. | `ssm.tf` |
| `/marketplace-listing/gemini_api_key` | **SecureString**, same pattern. Google Gemini key for the Fix flow's explanations. (Was missing from this page before 2026-11-17.) | `ssm.tf` |

Both SecureStrings use the free AWS-managed `aws/ssm` key. **No trailing whitespace in any
value**: a stray `\n` in `cognito_redirect_uri` once broke login (`settings.py` now
`.strip()`s values). The 5 `cognito_*` parameters go away with Cognito in phase 5.

### Auth — Cognito (removal planned)
| Thing | Value |
|---|---|
| User pool | `ap-south-1_NdxNQ1pIz` (name `User pool - e6qzup`), `deletion_protection = ACTIVE` |
| App client | `29oo5dtqh8j30k2481lmffqb0e` (`marketplace-listing-pool`); secret in SSM |
| Hosted-UI domain | `ijor-marketplace` (ManagedLogin v2) |
| Callback / sign-out | `http://localhost:8000/auth/callback` / `http://localhost:8000/` |
| ID-token validity | 8 hours (= `TOKEN_MAX_AGE` in `auth_routes.py`) |
| Not in Terraform | the test user, the managed-login branding |

Owned by `infra/terraform/cognito.tf`, with `prevent_destroy` on everything. It's adopted only so
phase 5 can delete it cleanly: the only user is the owner, and AWS IAM (needed to open the SSM
session) is already the real gate.

### Storage — S3
| Bucket | Purpose | Owned by | Notes |
|---|---|---|---|
| `ijorethnicpartners` | `myntra/…` product JPGs (**public read**: Myntra downloads them by URL); `state/myntra_groupid.json`, `state/hsn_kb.json`, `state/sku_registry.json` (**private**) | `infra/terraform/s3.tf` | `prevent_destroy` **permanently** on the bucket and its public-read policy. Terraform manages settings only, **never the objects**. Only `.jpg` uploads land under `myntra/`. |
| `ijor-tfstate-048589483919` | Terraform state (`listing-app/terraform.tfstate`) + its lock file | **by hand**, `infra/bootstrap-state-bucket.sh` | Versioned, encrypted, all public access blocked. The state contains the decrypted SecureStrings, so never copy it anywhere else. |
| `ijor-ansible-ssm-048589483919` | Scratch space for Ansible over SSM | `infra/terraform/ansible_transfer.tf` | Private; objects expire after 1 day. The box reads it through presigned URLs signed by your laptop, so the EC2 role has **no** grant on it. |

---

## GitHub resources
| Resource | Value |
|---|---|
| Repository | `gopalthakur71/marketplace-bulklisting-semi-automation` (default branch `main`) |
| Workflow | `.github/workflows/ci-cd.yml`: `test` → `build-and-push` → `deploy`. Runs on push to `main`; PRs run `test` only. |
| Repo secret | `AWS_ACCOUNT_ID` = `048589483919` (an account number, **not a credential**; OIDC provides short-lived creds) |

**While the server is off, the `deploy` job fails.** There's no instance tagged `listing-app`
to restart. That's harmless: the image is already in ECR, and the next "on" pulls `:latest`.

---

## Not provisioned (deliberately)
- **TLS + a public URL.** The app is reached only through an SSM port forward to
  `localhost:8000`; nothing is exposed to the internet.
- **An Elastic IP or a fixed instance.** The server is rebuilt on demand.
- **Terraform in CI.** It's applied from the laptop; a `plan`-on-PR job can come later.
