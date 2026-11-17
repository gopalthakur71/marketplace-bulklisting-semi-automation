# Infrastructure as Code — Terraform + Ansible (design)

**Date:** 2026-11-13 · **Status:** phases 1–3 **done**, phase 5 + additions **done in code** (2026-11-17; AWS applies pending), phase 4 pending · **Owner:** Gopal

> **Progress & how the build differed from this plan (2026-11-17):**
> - **Phase 1–2 done.** 27 console resources imported with zero changes, plus a 9th SSM parameter
>   (`gemini_api_key`) that the docs had missed. The SG was found **open to `0.0.0.0/0` on 22 and
>   80**; those rules were removed right after import (not deferred to phase 4).
> - **Phase 3 done, with a change of plan:** instead of a long-lived replacement box, the server is
>   **disposable**. `var.app_running` (no default) creates/deletes `aws_instance.server`, and the
>   console box was deleted at cutover (`moved` block next → server[0]). "Terminate the old box
>   after a few days" became "delete it once the new one passed its health check".
> - **Ansible SSM connection is `community.aws.aws_ssm` 9.x**, not `amazon.aws.aws_ssm` (that needs
>   amazon.aws 10 / ansible-core 2.17; Ubuntu ships 2.16).
> - **The transfer bucket needs no EC2-role grant.** The plugin hands the box presigned URLs.
> - **New unit setting `TimeoutStartSec=600`**: the default 90 s killed the first image pull on a
>   fresh box (a latent bug in the original unit).
> - Operating runbook: [`docs/runbooks/infra-terraform-ansible.md`](../../runbooks/infra-terraform-ansible.md);
>   ADR: [`docs/decisions/2026-11-17-terraform-ansible-disposable-server.md`](../../decisions/2026-11-17-terraform-ansible-disposable-server.md).

> **Phase 5 + additions (2026-11-17, done: step A applied, step B = cognito.tf deleted):**
> - **ECR keeps 3 images**, and the **server is a one-time Spot t3.small** (`var.use_spot`,
>   default true, with on-demand as the fallback; `var.instance_type`). The size was chosen
>   without checking live Spot prices, because no AWS commands were run; compare prices before
>   moving to t3.medium.
> - **Cognito code deleted, not kept dormant** (open item settled). `AUTH_DISABLED` went too,
>   because there's no login left to disable. Ansible's health check expects 200. ADR:
>   [`docs/decisions/2026-11-17-remove-cognito-iam-only-access.md`](../../decisions/2026-11-17-remove-cognito-iam-only-access.md).
> - **Deleting the pool takes two applies**, not one: its AWS-side `deletion_protection = ACTIVE`
>   blocks a destroy. Step A sets it `INACTIVE`, drops `prevent_destroy` and deletes the 5 SSM
>   params. Step B deletes `cognito.tf` and its import blocks.
> - **Phase 4's ingress removal was pulled into this change**, and the container binds to
>   `127.0.0.1:80`. A login-less app must never coexist with an inbound rule. The key pair
>   deletion is still phase 4.
> - **Order:** push the code first (so `:latest` has no login), then apply.

## Goal

Make the `listing-app` server **disposable**: every AWS resource and every on-box setting is
described in code, so the instance can be terminated and rebuilt in minutes. Today the
resources were created by hand in the console ([`docs/infra-resources.md`](../../infra-resources.md))
and the box config lives only on the instance ([`docs/runbooks/ec2-box-configuration.md`](../../runbooks/ec2-box-configuration.md)).

End state:
- **Terraform** owns the AWS resources (adopted with `import`, not recreated).
- **Ansible** (run from WSL) owns the box config.
- Access is through **SSM port forwarding**. The security group has **no inbound rules**
  (no port 22, no port 80) and there is no SSH key.
- **Cognito is removed**. The app runs with `AUTH_DISABLED=1`, and AWS IAM (the `gt`
  profile needed to open the SSM session) is the only gate. There is one user (Gopal).

## Non-goals

- TLS, a public URL, Cloudflare. The Cloudflare runbook is out of scope.
- Running Terraform in CI. It's applied from the laptop for now; a `plan`-on-PR job can
  come later with its own IAM role.
- Changing the CI/CD deploy flow (SSM Run Command → `systemctl restart listing-app`).

## Layout

```
infra/
  terraform/
    versions.tf        # terraform >= 1.10, aws provider pinned
    backend.tf         # S3 backend, use_lockfile = true
    providers.tf       # region ap-south-1
    imports.tf         # import blocks (kept as the audit trail of what was adopted)
    network.tf         # security group
    iam.tf             # EC2 role + profile, GitHub OIDC provider + deploy role
    ecr.tf             # repo + lifecycle (keep last 10)
    s3.tf              # ijorethnicpartners (prevent_destroy) + ansible-ssm transfer bucket
    ssm.tf             # /marketplace-listing/* parameters
    cognito.tf         # imported only so phase 5 can delete it cleanly
    ec2.tf             # instance, AL2023 AMI via data source, 8 GB gp3
    outputs.tf         # instance id, ssm port-forward command
  ansible/
    ansible.cfg
    inventory.aws_ec2.yml   # dynamic inventory: tag Name=listing-app
    site.yml
    roles/
      docker/          # install + enable docker, daemon.json (log rotation)
      journald/        # journald.conf cap
      listing_app/     # systemd unit template, enable + start, health check
```

## Phase 1: state backend

- One S3 bucket for state (e.g. `ijor-tfstate-048589483919`), created **once by hand**
  (chicken-and-egg). Turn on versioning and default encryption, and block all public access.
- `backend "s3" { use_lockfile = true }`, so no DynamoDB table is needed.
- **State is never committed.** It will contain the Cognito client secret until phase 5.
  Add `*.tfstate*` and `.terraform/` to `.gitignore`. Commit `.terraform.lock.hcl`.

## Phase 2: import the existing resources

For each resource: write an `import` block → `terraform plan -generate-config-out` →
clean up the generated code → repeat until **`terraform plan` says "No changes"** → `apply`.
That apply only records the imports.

| Resource | Notes |
|---|---|
| SG `sg-06ceeb9a898378bb0` | Port-80-from-my-IP rule imported as it is; removed in phase 4. |
| IAM `listing-app-ec2-role` + profile + `listing-app-runtime` v2 | Attach managed `AmazonSSMManagedInstanceCore` by ARN. |
| IAM `github-actions-ecr-push` + OIDC provider | ⚠️ **A wrong trust policy breaks CI.** Must reach "No changes" before any apply. |
| ECR `marketplace-bulklisting` + lifecycle | |
| S3 `ijorethnicpartners` | `prevent_destroy`. Import the bucket, its policy and its public-access-block **exactly**: product images are served publicly as `.jpg` (invariant 4). The objects are never managed by Terraform. |
| SSM params (7 plain + 1 SecureString) | SecureString: `lifecycle { ignore_changes = [value] }`. The secret value never goes in `.tf` files. |
| Cognito pool / client / domain | `prevent_destroy` until phase 5. The test user and branding are **not** imported. |
| EC2 `i-0add667d4cec224c6` | Imported so its config is visible; it gets replaced in phase 3. |

**Rule:** if any plan shows `destroy` or `replace` on an imported resource, stop and fix the
code. Never apply through it.

## Phase 3: Ansible + rebuild the instance

**Connection:** the `amazon.aws.aws_ssm` connection plugin from WSL, not SSH. It needs:
- `session-manager-plugin` and the `amazon.aws` collection in WSL, plus the `gt` profile
- a small S3 bucket for file transfer (Terraform creates it, with a 1-day expiry
  lifecycle; the EC2 role gets Get/Put on it)

**Playbook** (contents taken word for word from the box-configuration runbook):
1. `docker` role: install docker and enable it, then write `/etc/docker/daemon.json` with
   `validate: python3 -c "import json,sys; json.load(open(sys.argv[1]))" %s`. The
   2026-08-03 invalid-JSON outage becomes impossible.
2. `journald` role: `SystemMaxUse=200M`, `MaxRetentionSec=1month`.
3. `listing_app` role: unit template with the env vars as Ansible variables
   (`auth_disabled: false` for now), then `daemon-reload`, enable and start.
4. Health check: `curl localhost:80` returns `302` while Cognito is live (`200` after phase 5).

Handlers: when a docker restart is needed, the `listing_app` restart runs after it.

**Cutover** (safe order):
1. Terraform creates the new instance tagged **`Name=listing-app-next`**. ⚠️ It must not
   share the `listing-app` tag while the old box runs: the CI deploy job's `describe-instances`
   would return two IDs and `send-command` breaks.
2. Run Ansible on it, then check it through SSM port forwarding (login, generate a sheet).
3. Stop the old instance and retag the new one to `listing-app` in Terraform. Push a trivial
   commit to confirm the CI deploy reaches it.
4. Terminate the old instance after a few days (remove it from state/code).

## Phase 4: close the ports

- Access command (also a Terraform output):
  `aws ssm start-session --target <id> --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=8000 --profile gt`
  → `http://localhost:8000`. The Cognito callback is already `localhost:8000`, so login keeps working.
- Remove all inbound SG rules. Delete the `listing-app` key pair.

## Phase 5: remove Cognito

1. Ansible: `auth_disabled: true`, which writes `AUTH_DISABLED=1` into the unit. Check the app.
2. **Check first:** that `src/web` settings don't require the Cognito SSM params at startup
   when auth is disabled.
3. Terraform: remove `prevent_destroy`, then delete `cognito.tf` and the 5 Cognito SSM params → apply.
4. Code: drop the Cognito login path (or leave it dormant — decide then). Update the tests.
5. Docs: `infra-resources.md`, `AGENTS.md` (deployment status, invariant 7), the deploy and
   box-config runbooks, and an ADR in `docs/decisions/`.

## Safety rules

- No AWS command (including read-only imports and plans) without Gopal's explicit go.
- Every `apply` follows a reviewed `plan`. Imported resources get zero destroys.
- `ijorethnicpartners` is `prevent_destroy` permanently. It holds the ledger, HSN KB, SKU
  registry and images.

## Additions agreed 2026-11-17 (do next session)

Usage pattern: the app is used a few 2–3 hour sessions a month, sometimes not for months.
Goal: pay nothing when it's not in use. Phase 3 already delivers the on/off switch
(`terraform apply -var app_running=true|false`; the server is deleted when off).

1. **ECR lifecycle: keep 3 images, not 10.** Rollback beyond the last few builds is never
   needed; even pennies of idle storage aren't worth paying.
2. **Spot instance, a size up from t3.micro.** For short sessions Spot is far cheaper per
   hour, and a bigger box makes the ~540 MB first image pull (several minutes on a t3.micro)
   faster. The box is stateless (data in S3, config in SSM), so an interruption only costs
   a re-run of `apply` + `ansible-playbook`. Design points to settle when building it:
   - `instance_market_options` (one-time request, `terminate` on interruption) on
     `aws_instance.server`.
   - Size: candidates `t3.small` / `t3.medium` (2–4 GB RAM); check current ap-south-1 Spot
     prices and interruption rates before choosing.
   - Fallback when Spot capacity isn't available: a variable to launch on-demand instead.

## Open items

- State bucket name.
- Pin exact Terraform / AWS provider / `amazon.aws` versions at implementation time.
- ~~Phase 5: delete the Cognito code outright or keep it dormant.~~ Deleted (2026-11-17).
