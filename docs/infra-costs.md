# Infrastructure Cost Breakdown — AWS

> What every AWS resource in this project costs, and what your monthly bill depends on.
> Companion to [`infra-resources.md`](infra-resources.md) (the resource map). Snapshot:
> **2026-11-17**, region **ap-south-1 (Mumbai)**. Prices are approximate on-demand rates and
> drift over time (Spot prices move daily); confirm live figures in **Billing → Cost Explorer**.
>
> **Account context:** this account is **past its 12-month Free Tier**, so EC2 is billed at
> normal rates.

## TL;DR

**You pay for the server only while it exists.** Since 2026-11-17 the EC2 server is created
when you need it and **deleted** when you're done (`terraform apply -var app_running=true|false`;
see [`runbooks/infra-terraform-ansible.md`](runbooks/infra-terraform-ansible.md)). When it's off
there is **no instance, no disk and no public IP**, so the idle bill is just storage:
**well under $0.50/month** (ECR now keeps only 3 images).

| Scenario | Approx / month |
|---|---|
| **Not used at all** (server off all month) | **~$0.20–0.50** |
| A few sessions (~10 h/mo) | under ~$1 |
| Regular use (~30 h/mo) | ~$1 or less |
| Left on 24×7 (don't) | well under ~$20 (Spot; ~$20 is the on-demand t3.small ceiling) |

The in-use figures are upper bounds at the **on-demand** t3.small rate; the server runs on
**Spot**, which is usually well below that. Check the current ap-south-1 Spot price in the EC2
console (Spot Requests → Pricing history) for the real number.

---

## Per-resource costs

### Billed only while the server EXISTS (the variable part)
| Resource | Rate (ap-south-1) | Notes |
|---|---|---|
| **EC2** compute (t3.small-like, Spot) | Spot price, **capped** at on-demand (~**$0.0224/hr** for t3.small) | Spot only — an EC2 Fleet picks the cheapest 2 vCPU / 2–4 GB burstable type with capacity (t3/t3a small/medium, t2.medium); there is no on-demand fallback. One size up from the old t3.micro (~$0.0112/hr) for 2 GB RAM and a faster first image pull. |
| **Public IPv4 address** | ~**$0.005/hr** | Released when the server is deleted. |
| **EBS root volume** (8 GB gp3) | ~$0.0912/GB-mo, so ~$0.0010/hr | Deleted with the server. (A *stopped* server would still pay ~$0.70/mo for this, which is why the box is deleted, not stopped.) |
| → **combined** | ≤ ~**$0.028/hr** | What each hour of use costs at most (on-demand rate; Spot lowers the EC2 part). |

### Billed always (the idle baseline, ~$0.50/mo)
| Resource | Rate | ~/mo | Notes |
|---|---|---|---|
| **ECR image storage** | $0.10/GB-mo | ~**$0.10–0.20** | At most 3 images of ~540 MB (lifecycle rule, lowered from 10 on 2026-11-17). Shared layers make it less. |
| **S3 `ijorethnicpartners`** | $0.025/GB-mo | **cents** | Product JPGs + the app's state files. Scales with image count. |
| **S3 Terraform state bucket** | — | **< 1 cent** | A few KB, versioned. |
| **S3 Ansible transfer bucket** | — | **$0** | Empty between runs; objects expire after a day. |

### Effectively free (at this scale)
| Resource | Why free |
|---|---|
| **SSM Parameter Store** (4 params, 1 SecureString) | Standard parameters are free; the SecureStrings use the free AWS-managed `aws/ssm` key. |
| **SSM Session Manager / Run Command** (port forward, Ansible, CI deploys) | No charge. |
| **IAM, security group, OIDC provider** | No charge. |
| **S3 requests + data transfer** | Cents at this volume; first 100 GB/mo egress free. |
| **GitHub Actions** | A few minutes per push. |

---

## What used to cost more (removed)
- **A stopped-but-kept EC2 box** (until 2026-11-17): its 8 GB disk billed ~$0.70/mo even when
  unused, plus ~$11.8/mo whenever it was left running. Replaced by create-on-demand.
- **Secrets Manager** (retired 2026-07-02): ~$0.40/mo per secret; the (now deleted) Cognito client
  secret became a free SSM SecureString instead. See [`decisions/2026-06-30-config-ssm-secrets-rationale.md`](decisions/2026-06-30-config-ssm-secrets-rationale.md).

## What would push the bill UP
1. **Forgetting to turn the server off.** That's the only way to a real bill (up to ~$20/mo 24×7).
   `terraform output` shows whether `instance_id` exists.
2. **Widening the instance shape** (the `server_*` locals in `ec2.tf`) to bigger types. The Fleet
   picks the cheapest match, but a wider range can still land on a pricier box.
3. **TLS + a public URL** (deferred): CloudFront is usage-billed, and an Elastic IP is billed
   while not attached to a running instance.
4. **Large image volume in S3**, which grows slowly (~$0.025/GB-mo).

## How to monitor
- **Billing → Cost Explorer**: actual spend by service. An instance's hours appear about a day later.
- **Recommended:** a **Budget alert** (Billing → Budgets) at about **$3/month**, so a server left on
  emails you before it becomes a surprise.
