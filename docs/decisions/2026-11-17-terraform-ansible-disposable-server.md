# Decision — infrastructure as code (Terraform + Ansible) and a disposable server

**Date:** 2026-11-17
**Status:** Accepted. Phases 1–3 done; phases 4–5 pending (see "Still to do").
**Design spec:** [`../superpowers/specs/2026-11-13-terraform-ansible-iac-design.md`](../superpowers/specs/2026-11-13-terraform-ansible-iac-design.md)
**Operate it:** [`../runbooks/infra-terraform-ansible.md`](../runbooks/infra-terraform-ansible.md)

---

## The problem

Until now every AWS resource was created by hand in the console, and the server's setup (the
systemd unit, Docker log rotation, the journal cap) existed only on the one EC2 instance. That
caused three problems:

1. **The server couldn't be safely rebuilt.** Its setup lived in a runbook and in someone's memory.
   Its own `user_data` was stale (Stage 1, `AUTH_DISABLED=1`), so rebooting from it would *not*
   have recreated the live box.
2. **The docs drifted from reality, unnoticed.** When the resources were imported, the security
   group turned out to be **open to the internet on ports 22 and 80**, while the docs said
   "My IP only". The docs also listed an IAM policy that didn't exist and missed an SSM parameter.
3. **Paying for idle.** The app is used a few 2–3 hour sessions a month, sometimes not at all
   for months. Even a *stopped* box costs money (its disk), and a running one costs ~$11/month.

## The decision

- **Terraform owns every AWS resource.** The console-built resources were **imported**, not
  recreated: each import had to reach `terraform plan` → "No changes" before being applied, so
  adopting them changed nothing in AWS. State lives in a versioned, encrypted S3 bucket with S3-native locking.
- **Ansible owns the server's setup**, run from WSL over **SSM** (no SSH): Docker + log rotation,
  the journal cap, the app's systemd unit, and a health check.
- **The server is disposable.** `terraform apply -var app_running=true|false` creates or deletes
  it. The variable has **no default**, so Terraform asks every time and the box is never built
  or deleted by accident. The server holds nothing: data is in S3, config in SSM, the image in ECR.
- **Ansible's dynamic inventory finds the box by its `Name` tag**, so Terraform doesn't write an
  inventory file. The instance ID changes on every rebuild, and a generated file would go stale.

## Why not…

| Alternative | Why not |
|---|---|
| **`terraform destroy` to switch off** | It targets *everything*, including the data bucket (images + ledger + HSN KB + SKU registry), ECR, IAM (breaks CI's login) and SSM. `prevent_destroy` makes it refuse outright. Those resources cost cents; only the EC2 costs real money. |
| **Just stop the instance** (the old habit) | A stopped box still bills its disk, keeps drifting from code, and keeps a fixed identity worth protecting. A deleted-and-rebuilt box proves the code can reproduce it every single time. |
| **Recreate the resources instead of importing them** | Recreating the data bucket or IAM roles would break live image URLs and CI. Import is risk-free when it's gated on "No changes". |
| **SSH for Ansible** | Needs port 22 open and a key to manage. SSM uses the instance role and needs no inbound ports. Access to the app is via an SSM port forward too. |
| **Terraform-generated Ansible inventory** | Goes stale on every rebuild. The `aws_ec2` dynamic inventory asks AWS live. |

## What the migration found and fixed

- **Security group open to `0.0.0.0/0` on 22 and 80**: closed the day it was found.
- **A latent unit bug**: systemd's default 90 s `TimeoutStartSec` covers the image pull. On a
  fresh box (nothing cached) the ~540 MB pull took longer, so systemd killed it and
  `Restart=on-failure` looped forever. It never showed on the old box because its image was
  cached. Fixed with `TimeoutStartSec=600` in the Ansible-managed unit. Only a real rebuild
  could have surfaced this.
- **`daemon.json` is validated before writing**, so the 2026-08-03 invalid-JSON Docker outage
  can't recur.

## Trade-offs accepted

- **CI's `deploy` job fails while the server is off.** It's harmless (the next "on" pulls
  `:latest`), but it shows red. Making the job skip cleanly is a possible follow-up.
- **Terraform state contains the decrypted SecureStrings.** The state bucket is private,
  encrypted and versioned; local copies (`errored.tfstate`, `*.tfplan`) are git-ignored and
  deleted after use. The Cognito secret disappears entirely in phase 5.
- **Turning on takes ~5 minutes** (create + Ansible + first image pull), versus ~1 minute to
  start a stopped box. That's acceptable for a few sessions a month.
- **Terraform runs from the laptop**, not CI.

## Still to do

- **Phase 4:** use the SSM port forward in the browser; delete the last two inbound rules (zero
  inbound) and the old `listing-app` key pair.
- **Phase 5:** remove Cognito (`AUTH_DISABLED=1` via Ansible, delete the pool/client/domain and
  the 5 `cognito_*` parameters). AWS IAM (needed to open the SSM session) becomes the only gate.
- **Agreed additions:** ECR lifecycle keep 3 (not 10); a **Spot** instance one size up with an
  on-demand fallback.
