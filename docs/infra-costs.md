# Infrastructure Cost Breakdown — AWS

> What every AWS resource in this project costs, and what your monthly bill actually depends on.
> Companion to [`docs/infra-resources.md`](infra-resources.md) (the resource map). Snapshot:
> **2026-07-02**, region **ap-south-1 (Mumbai)**. Prices are approximate on-demand rates and drift
> over time — treat as ballpark, confirm live figures in the **Billing → Cost Explorer** console.
>
> **Account context:** this account is **past its 12-month Free Tier**, so EC2 compute is billed at
> on-demand rates. (The "Free tier eligible" badge on `t3.micro` in the console is a static label on
> the instance type, *not* an account-specific entitlement.)

## TL;DR

**The bill is dominated by one lever: how many hours the EC2 box is running.** Everything else is a
dollar or less per month. With the start/stop habit (run it to list, then stop it), expect
**≈ $1–3/month**. Left on 24×7, **≈ $12/month**.

| Scenario | Approx / month |
|---|---|
| **Box stopped most of the time** (start on demand, e.g. ~20 h/mo) | **~$1.5** |
| Moderate use (~50 h/mo running) | ~$2 |
| Heavy use (~100 h/mo running) | ~$2.7 |
| **Left running 24×7** | **~$12** |

---

## Per-resource costs

### Billed while the box is RUNNING (the variable part)
| Resource | Rate (ap-south-1) | 24×7/mo | Notes |
|---|---|---|---|
| **EC2 `t3.micro`** compute | ~**$0.0112/hr** | ~$8.2 | Only billed while the instance is *running*. Stopped = $0 compute. |
| **Public IPv4 address** | ~**$0.005/hr** | ~$3.6 | AWS charges for in-use public IPv4 (since 2024). Released when the box stops → $0 while stopped. |
| → **combined running rate** | ~**$0.0162/hr** | ~$11.8 | This is what each hour of uptime costs. |

### Billed always (the fixed baseline, ~$0.85/mo)
| Resource | Rate | ~/mo | Notes |
|---|---|---|---|
| **EBS root volume** (~8 GB gp3) | ~$0.0912/GB-mo | ~**$0.70** | Charged even while the instance is stopped — it's the disk. |
| **ECR image storage** (~1–2 GB) | $0.10/GB-mo | ~**$0.10–0.20** | `:latest` + a few `:<sha>` tags; lifecycle policy keeps the last 10. |
| **S3 storage** (`ijorethnicpartners`) | $0.025/GB-mo | **cents** | Product JPGs + the ledger. 2 GB ≈ $0.05/mo. Scales with image count. |

### Effectively free (at this scale)
| Resource | Why free |
|---|---|
| **SSM Parameter Store** (8 params, incl. the SecureString) | Standard parameters are free. The SecureString uses the AWS-managed `aws/ssm` KMS key — no key fee; the few decrypt calls per app start are negligible ($0.03 / 10k requests). |
| **Cognito** (user pool, hosted UI) | Free tier covers 50,000 monthly active users; this is single-user. The hosted-UI domain's CloudFront is AWS-managed and not billed to you. |
| **IAM** (roles, OIDC provider, policies) | No charge. |
| **SSM Run Command** (CI deploy job) | No charge. |
| **S3 requests + data transfer** | PUT $0.005/1k, GET $0.0004/1k, first 100 GB/mo egress free — all cents at this volume. |
| **GitHub Actions** | Public/allowance minutes on the plan; the pipeline is a few minutes per push. |

---

## What used to cost more (removed)
- **Secrets Manager** — retired 2026-07-02. It charged **~$0.40/mo per secret**; the Cognito client
  secret is now a free SSM **SecureString**. (See [`decisions/2026-06-30-config-ssm-secrets-rationale.md`](decisions/2026-06-30-config-ssm-secrets-rationale.md).)

## What would push the bill UP (cost levers)
1. **Leaving the box running 24×7** — the single biggest factor (~$11.8/mo of the ~$12 total).
2. **TLS + public URL (deferred):** a CloudFront distribution is usage-billed (cents for one user,
   but real if traffic grows), and an **Elastic IP** costs ~$3.6/mo *while the box is stopped*
   (in-use EIPs on a running box are free). This is why it was parked.
3. **A bigger instance** (t3.small/medium) if the pipeline needs more RAM/CPU — 2–4× the compute rate.
4. **Large image volume in S3** — storage + egress grow with tens of thousands of product images
   (still cheap: ~$0.025/GB-mo storage, first 100 GB/mo egress free).

## How to keep it low
- **Stop the instance when you're not actively listing.** That zeroes the ~$0.0162/hr running cost;
  you keep only the ~$0.85/mo fixed baseline.
- Don't allocate an Elastic IP unless you commit to always-on.
- The ECR lifecycle policy (keep last 10 images) already caps registry growth.

## How to monitor / stay safe
- **Billing → Cost Explorer** — actual spend by service, filterable by day/service.
- **Billing → Free Tier** — shows what, if anything, you're still drawing from a free allowance.
- **Recommended:** set a **Budget alert** (Billing → Budgets) at, say, **$5/month** so a forgotten
  running box emails you before it becomes a surprise. This is the single best safeguard against the
  "left it on 24×7" scenario.
