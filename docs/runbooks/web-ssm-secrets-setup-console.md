# Runbook — SSM Parameter Store Setup via the **Console**

Same outcome as the CLI runbook, done through the AWS web console. This sets up the configuration
parameters (including the Cognito client secret as a **SecureString**) that the Marigold Ops web app
reads on startup when environment variables are not set. The settings loader falls back per-field:
each config value is read from the environment first, then from AWS Systems Manager Parameter Store
(SSM) if absent. **Secrets Manager is no longer used** (retired 2026-07-02 — SSM SecureString is
free). Do the steps in order.

> **New to SSM, or wondering why there are 8 parameters and whether this is over-engineered?**
> Read the plain-English rationale first:
> [`docs/decisions/2026-06-30-config-ssm-secrets-rationale.md`](../decisions/2026-06-30-config-ssm-secrets-rationale.md).
> It explains what SSM is, how the parameter count was derived, and why the client secret is now a
> SecureString in the same store rather than a separate Secrets Manager secret.

**Values used throughout (don't substitute — these are this project's):**

| Thing | Value |
|---|---|
| AWS account id | `048589483919` |
| Region | **Asia Pacific (Mumbai) `ap-south-1`** |
| SSM prefix | `/marketplace-listing/` |
| S3 bucket | `ijorethnicpartners` |
| S3 region | `ap-south-1` |
| S3 prefix | `myntra/` |

> Sign in with an IAM user that has SSM write permissions (incl. SecureString / `kms` on `aws/ssm`).
> The values for Cognito parameters come from the Cognito runbook (Task 8): `COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_CLIENT_SECRET`, `COGNITO_DOMAIN`. The production callback URL is determined by where you deploy (e.g., `https://marigold-ops.example.com/auth/callback`).

---

## 1. Create SSM Parameter Store parameters

1. Set the **region selector** (top-right of the console) to **Asia Pacific (Mumbai) ap-south-1**. SSM Parameter Store is regional.
2. Open **Systems Manager** → **Parameter Store** → **Create parameter**.
3. For **each parameter below**, repeat steps 4–9:

### 1a. `/marketplace-listing/s3_bucket`

4. Name: `/marketplace-listing/s3_bucket`
5. Description: `S3 bucket for product images and exports` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `ijorethnicpartners`
9. **Create parameter**.

### 1b. `/marketplace-listing/s3_region`

4. Name: `/marketplace-listing/s3_region`
5. Description: `AWS region for S3` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `ap-south-1`
9. **Create parameter**.

### 1c. `/marketplace-listing/s3_prefix`

4. Name: `/marketplace-listing/s3_prefix`
5. Description: `S3 object key prefix for uploads` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `myntra/`
9. **Create parameter**.

### 1d. `/marketplace-listing/cognito_pool_id`

4. Name: `/marketplace-listing/cognito_pool_id`
5. Description: `Cognito user pool ID from Task 8` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `<paste the Cognito POOL_ID from the Cognito runbook, Task 8; format: ap-south-1_xxxxxxxxx>`
9. **Create parameter**.

### 1e. `/marketplace-listing/cognito_client_id`

4. Name: `/marketplace-listing/cognito_client_id`
5. Description: `Cognito app client ID from Task 8` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `<paste the Client ID from the Cognito runbook, Task 8>`
9. **Create parameter**.

### 1f. `/marketplace-listing/cognito_domain`

4. Name: `/marketplace-listing/cognito_domain`
5. Description: `Cognito domain prefix from Task 8` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `<paste the Cognito domain prefix from Task 8; e.g., ijor-marketplace>`
9. **Create parameter**.

### 1g. `/marketplace-listing/cognito_redirect_uri`

4. Name: `/marketplace-listing/cognito_redirect_uri`
5. Description: `OAuth2 callback URL for the deployed app` (optional)
6. Tier: **Standard**
7. Type: **String**
8. Value: `<dev: http://localhost:8000/auth/callback; prod: https://marigold-ops.example.com/auth/callback>`
9. **Create parameter**.

---

## 2. Create the client-secret parameter (SSM SecureString)

> **Updated 2026-07-02.** The Cognito client secret is stored as an **SSM SecureString**, not a
> Secrets Manager secret. SSM parameters are free (Secrets Manager charges ~$0.40/mo per secret),
> and a SecureString is encrypted at rest with the AWS-managed `aws/ssm` KMS key. The app reads it
> the same way as every other parameter (`get_parameter(..., WithDecryption=True)`), so there is no
> Secrets Manager code path anymore.

1. Open **Systems Manager** → **Parameter Store** → **Create parameter**.
2. Name: `/marketplace-listing/cognito_client_secret`.
3. Tier: **Standard**. Type: **SecureString**.
4. **KMS key source:** *My current account* → key `alias/aws/ssm` (the default AWS-managed key —
   no extra IAM `kms:Decrypt` permission is needed for the instance role with this key).
5. Value: paste **only the raw client secret** (the `cognito_client_secret` string from the Cognito
   runbook, Task 8). No JSON, no quotes, **no trailing newline**.
6. **Create parameter.**

> CLI equivalent: `aws ssm put-parameter --name /marketplace-listing/cognito_client_secret
> --value '<secret>' --type SecureString --overwrite --region ap-south-1`.

---

## 3. Verify the setup locally

The settings loader (`src/web/settings.py`) reads each config value from the environment first, then falls back per-field to SSM Parameter Store — including the `cognito_client_secret` SecureString (decrypted via `WithDecryption=True`). This means you can set some values via environment and leave the rest to AWS.

> **What you can and can't verify today.** The hosted-UI login redirect and the
> `/auth/callback` route are **not built yet** (deferred to the deploy phase). So running
> the app with auth enabled will **not** redirect you to the Cognito login page — with no
> token, `current_user` (`src/web/auth.py`) raises `AuthError`. What you *can* verify now
> is that the settings loader reads your new SSM/Secrets values back from AWS.

1. Ensure your AWS credentials are active and your IAM user can read SSM (incl. SecureString decrypt):

   ```bash
   aws sts get-caller-identity
   ```

2. Confirm per-field fallback resolves the values from AWS (env vars popped so it must hit AWS):

   ```powershell
   $env:AWS_REGION="ap-south-1"
   python -c "import os; [os.environ.pop(k, None) for k in ('S3_BUCKET','S3_REGION','S3_PREFIX','COGNITO_POOL_ID','COGNITO_CLIENT_ID','COGNITO_CLIENT_SECRET','COGNITO_DOMAIN','COGNITO_REDIRECT_URI')]; from src.web.settings import load_settings; s=load_settings(); print('pool:', s.cognito_pool_id); print('client:', s.cognito_client_id); print('secret loaded:', bool(s.cognito_client_secret))"
   ```

   Seeing the pool id, client id, and `secret loaded: True` confirms the SSM parameters (incl. the
   SecureString) are stored and readable.

**Note:** In production the EC2 instance role grants `ssm:GetParameter*` on `/marketplace-listing/*`
(no `secretsmanager` permission needed — Secrets Manager is retired; the SecureString decrypts via
the AWS-managed `aws/ssm` key). Locally, your IAM user provides those permissions.

---

## 4. Teardown (only if you need to undo this)

- **SSM parameters**: Systems Manager → Parameter Store → select each `/marketplace-listing/*` parameter → **Delete parameter** (repeat for all 8 parameters, including the `cognito_client_secret` SecureString).

---

## Next: Verify Per-Field Fallback Behavior

To confirm the per-field fallback works as designed, try this:

1. Set only some env vars and leave others unset:

   ```bash
   export S3_BUCKET="ijorethnicpartners"
   unset S3_REGION S3_PREFIX COGNITO_POOL_ID COGNITO_CLIENT_ID COGNITO_CLIENT_SECRET COGNITO_DOMAIN COGNITO_REDIRECT_URI
   ```

2. Run the app — it should use the env var for `S3_BUCKET` and fall back to SSM/Secrets for the rest.

3. Check the logs or add a debug print in the settings loader to confirm which values came from where.

This per-field fallback design allows gradual migration from environment variables to AWS-hosted secrets and enables flexible deployments.
