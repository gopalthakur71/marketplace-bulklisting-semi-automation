# Runbook — Add the Gemini API key to SSM (production)

**Purpose.** The Fix-Error flow calls Gemini to explain cryptic Myntra errors in plain English. In
production the app reads its config from **SSM Parameter Store** (region `ap-south-1`). This runbook
adds one new parameter — the Gemini API key — as a **SecureString**, exactly the same pattern as the
existing `cognito_client_secret`.

| Fact | Value |
|---|---|
| Region | **Asia Pacific (Mumbai) `ap-south-1`** (SSM is regional — get this right) |
| Parameter name | **`/marketplace-listing/gemini_api_key`** |
| Type | **SecureString** (Standard tier, AWS-managed `aws/ssm` KMS key) |
| Value | the **raw** Gemini API key — the same `GEMINI_API_KEY` string in your local `.env` |
| Read by | `src/web/settings.py` → `load_settings()` (`SSM_PREFIX + "gemini_api_key"`, `WithDecryption=True`) |

> **Why SecureString.** The value is a live credential. A SecureString is encrypted at rest with the
> `aws/ssm` KMS key and only decrypted on read (`WithDecryption=True`). The EC2 instance role already
> reads the `cognito_client_secret` SecureString, so **no IAM change is needed** — the same
> `ssm:GetParameter` + `kms:Decrypt` permissions cover this new param.

---

## Prerequisites

1. Your AWS credentials are active and point at an IAM user/role with SSM **write** permission,
   including SecureString (`ssm:PutParameter` + `kms` on `aws/ssm`).
2. You have the raw Gemini API key handy. It's the `GEMINI_API_KEY=...` line in your repo-root `.env`.
   Copy the value **without** quotes or a trailing newline.

Quick credential check:

```powershell
aws sts get-caller-identity --region ap-south-1
```

---

## Option A — AWS Console (click-through)

1. Set the **region selector** (top-right) to **Asia Pacific (Mumbai) ap-south-1**.
2. Open **Systems Manager → Parameter Store → Create parameter**.
3. **Name:** `/marketplace-listing/gemini_api_key`
4. **Description** (optional): `Gemini API key for Fix-Error explanations`
5. **Tier:** Standard.
6. **Type:** **SecureString**.
7. **KMS key source:** *My current account* → **`alias/aws/ssm`** (the default AWS-managed key).
8. **Value:** paste **only the raw API key** — no quotes, no `GEMINI_API_KEY=`, no trailing space/newline.
9. **Create parameter.**

Skip to [Verify](#verify).

---

## Option B — CLI

> ⚠️ **Windows Git Bash gotcha (bit us on 2026-07-02).** MSYS rewrites any argument starting with `/`
> into a Windows path, so `--name /marketplace-listing/...` silently becomes
> `C:/Program Files/Git/marketplace-listing/...` → phantom `ParameterNotFound`. **Use PowerShell**
> (below) — or if you must use Git Bash, prefix the command with `MSYS_NO_PATHCONV=1`.

### PowerShell (recommended on this machine)

```powershell
# Pull the key straight out of .env so it's never typed/pasted into shell history.
$key = (Get-Content .env | Where-Object { $_ -match '^GEMINI_API_KEY=' }) -replace '^GEMINI_API_KEY=', ''
$key = $key.Trim()

aws ssm put-parameter `
  --name "/marketplace-listing/gemini_api_key" `
  --value $key `
  --type SecureString `
  --overwrite `
  --region ap-south-1
```

### Git Bash / Linux / macOS

```bash
MSYS_NO_PATHCONV=1 aws ssm put-parameter \
  --name /marketplace-listing/gemini_api_key \
  --value "$(grep '^GEMINI_API_KEY=' .env | cut -d= -f2-)" \
  --type SecureString --overwrite --region ap-south-1
```

`--overwrite` is safe here and makes the command idempotent — re-running it just updates the value.

---

## Verify

**1. The parameter exists and decrypts (value is masked below — only its length/prefix matters):**

```powershell
aws ssm get-parameter `
  --name "/marketplace-listing/gemini_api_key" `
  --with-decryption --region ap-south-1 `
  --query "Parameter.{Type:Type,Value:Value}"
```

Expect `"Type": "SecureString"` and your key as the `Value`.

**2. The app loader actually resolves it** (run from the repo root; the temporary `env.pop` forces the
SSM path instead of any local `.env`/env var):

```powershell
$env:AWS_DEFAULT_REGION="ap-south-1"   # botocore reads AWS_DEFAULT_REGION, not AWS_REGION
python -c "import os; os.environ.pop('GEMINI_API_KEY', None); from src.web.settings import load_settings; s=load_settings(); print('gemini key loaded:', bool(s.gemini_api_key), 'len=', len(s.gemini_api_key))"
```

Expect `gemini key loaded: True len= <nonzero>`.

---

## What this runbook does NOT cover (the other two prod-enable steps)

Adding the key to SSM is necessary but **not sufficient** to turn Gemini on in production. Two more
things live in the EC2 **systemd unit** (`listing-app.service`), because they are env-only (not in the
SSM `_FIELDS` list):

- `EXPLAIN_WITH_GEMINI=1` — the master switch. Without it the flow runs but falls back to YAML rules +
  raw messages (no plain-English, no crash).
- `GEMINI_MODEL=gemini-2.5-flash` — optional; the code already defaults to this.

Full step-by-step for that is the sibling runbook **`enable-gemini-ec2-systemd.md`** (Session Manager
walkthrough). Then: start the EC2 box, push `main`, CI deploys.

---

_Created 2026-07-07 for the Fix-Error flow deploy. Sibling of
`docs/runbooks/web-ssm-secrets-setup-console.md`._
