# Decision & rationale — runtime config via SSM Parameter Store + Secrets Manager

**Date:** 2026-06-30
**Status:** **Superseded 2026-07-02** — we adopted the "leaner alternative" this document
predicted (see §"leaner future alternative" below). The Cognito client secret was migrated from
**Secrets Manager → an SSM `SecureString`** at the same path `/marketplace-listing/cognito_client_secret`,
and Secrets Manager is no longer used at all. Reason: cost — Secrets Manager charges ~$0.40/mo per
secret; SSM SecureString is free and read the same way (`get_parameter(..., WithDecryption=True)`),
decrypted with the AWS-managed `aws/ssm` KMS key (no extra `kms:Decrypt` IAM permission needed).
Code: `src/web/settings.py` (the Secrets Manager getter was removed). The rest of this document is
kept for the original rationale and the trade-off discussion.
**Audience:** written plainly because the project owner is new to SSM / Secrets Manager.

---

## 0. The problem we're solving

When the web app runs on a server, it needs some values at startup:

- where to put images (S3 bucket, region, prefix),
- who handles login (Cognito pool id, client id, domain, callback URL),
- and **one genuine secret**: the Cognito **client secret**.

The question is: *where do those values come from on a deployed server?* We don't want
to hard-code them in the source (especially the secret — that must never be in git), and
we don't want to paste them by hand every time the server restarts. So we need a place to
store them that the running app can read automatically.

There are three common places to keep such values on AWS:

1. **Environment variables** — simplest; set when the container starts. Fine for non-secret
   config, but secrets in env vars can leak into logs / process listings, and you have to
   set them somewhere anyway.
2. **SSM Parameter Store** — a free, AWS-hosted key→value store. Good for plain config.
   It also has a `SecureString` type that encrypts the value (good enough for secrets).
3. **Secrets Manager** — like SSM SecureString but purpose-built for secrets, with extras
   like **automatic rotation** (auto-changing a password/key on a schedule). Costs about
   **$0.40 per secret per month** + a tiny per-call fee.

---

## 1. Plain-English: what each AWS service is

**SSM Parameter Store** ("SSM" = AWS Systems Manager). Think of it as a cloud dictionary.
You store entries like `/marketplace-listing/s3_bucket = ijorethnicpartners`. The app, with
the right permissions, asks AWS "give me the value of that name" and gets it back. Standard
parameters are **free**. Three value types: `String` (plain), `StringList`, and
`SecureString` (encrypted with a KMS key — usable for secrets).

**Secrets Manager.** Same idea — store a name → value — but specialised for secrets. The
headline extra feature is **rotation**: it can call a Lambda to change the secret and update
it in place on a schedule (e.g. rotate a database password every 30 days). It costs money
per secret. If you are **not** using rotation, it's doing roughly what an SSM SecureString
does, for a fee.

**Rule of thumb:** plain config → SSM `String`. A secret you rotate → Secrets Manager. A
secret you *don't* rotate → SSM `SecureString` is enough (and free).

---

## 2. How we arrived at **7** SSM parameters

We did not pick "7" as a target. The number fell out of a one-to-one mapping: **each
non-secret field the app reads gets its own parameter.** The app's settings object
(`src/web/settings.py`, the `_FIELDS` list) defines exactly which values it looks up, and we
created one SSM parameter per non-secret field, plus one Secrets Manager entry for the secret:

| # | Setting field | Where it's stored | Type | Why separate |
|---|---|---|---|---|
| 1 | `s3_bucket` | SSM `/marketplace-listing/s3_bucket` | String | infra value |
| 2 | `s3_region` | SSM `/marketplace-listing/s3_region` | String | infra value |
| 3 | `s3_prefix` | SSM `/marketplace-listing/s3_prefix` | String | infra value |
| 4 | `cognito_pool_id` | SSM `/marketplace-listing/cognito_pool_id` | String | auth config |
| 5 | `cognito_client_id` | SSM `/marketplace-listing/cognito_client_id` | String | auth config |
| 6 | `cognito_domain` | SSM `/marketplace-listing/cognito_domain` | String | auth config |
| 7 | `cognito_redirect_uri` | SSM `/marketplace-listing/cognito_redirect_uri` | String | differs dev vs prod |
| — | `cognito_client_secret` | **Secrets Manager** `/marketplace-listing/cognito_client_secret` | secret | the only true secret |

So: **7 SSM params = the 7 non-secret fields; 1 Secrets Manager entry = the 1 secret.**
One parameter per field (rather than, say, one big JSON blob) is what makes the app's
**per-field fallback** work: the loader resolves each field independently from the
environment first, then from AWS. That lets a deploy override just one value via an env var
without having to re-supply all the others.

---

## 3. Why we chose this design (the honest reasons)

1. **Stated project goal.** This whole web-app + cloud effort exists to **demonstrate
   cloud / CI-CD skills**, not to ship the absolute minimum. Using SSM Parameter Store +
   Secrets Manager + an EC2 instance role that reads them is a recognised, real-world
   pattern worth showing.
2. **Keeps the secret out of git and out of plain env.** The client secret lives in a
   managed store, encrypted, fetched at runtime by an IAM-scoped role.
3. **Per-field fallback flexibility.** Env-var-first, then AWS, per field, allows gradual
   migration and easy local overrides (`src/web/settings.py`).
4. **Cost is negligible.** Standard SSM parameters are free; the single Secrets Manager
   secret is ~$0.40/month.

---

## 4. Is this over-engineered? (yes, slightly — and that's fine here)

For the *business* need alone — a single-container internal tool for a small, trusted team
— this is more machinery than strictly required. A minimal version would be:

> **Leaner alternative (recorded for the future):**
> - Put the **6 non-secret values as environment variables** set in the container at deploy
>   time (EC2 user-data, `docker run -e`, or a compose file). They're not sensitive.
> - Put the **one real secret in SSM `SecureString`** (free) instead of Secrets Manager —
>   since we don't use rotation, Secrets Manager's main feature is unused.
> - Net result: **0 paid services, ~1 stored secret**, far fewer moving parts.

We are **not** doing the leaner version right now because:
- the richer setup is the point (skills showcase), and
- it's already built and working, and the cost is trivial.

The only low-effort simplification worth a future glance is **moving the single secret from
Secrets Manager → SSM SecureString** to drop the ~$0.40/mo and unify on one service. Not
worth reworking today; noted here so the option isn't lost.

---

## 5. Bottom line

- **Keep** the current SSM (7 params) + Secrets Manager (1 secret) design — intentional,
  cheap, and aligned with the showcase goal.
- **Don't** read "7" as arbitrary: it's one parameter per non-secret field the app needs.
- **Future option:** collapse to env-vars + 1 SSM SecureString if this ever needs to be
  leaner or cheaper.

Related runbook: `docs/runbooks/web-ssm-secrets-setup-console.md`.
Settings loader: `src/web/settings.py` (`_FIELDS`, `load_settings`).
