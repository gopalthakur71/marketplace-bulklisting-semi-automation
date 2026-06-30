# Runbook — SSM Parameter Store & Secrets Manager Setup via the **Console**

Same outcome as the CLI runbook, done through the AWS web console. This sets up the configuration parameters and secrets that the Marigold Ops web app reads on startup when environment variables are not set. The settings loader falls back per-field: each config value is read from the environment first, then from AWS Systems Manager Parameter Store (SSM) or Secrets Manager if absent. Do the steps in order.

> **New to SSM / Secrets Manager, or wondering why there are 7 parameters and whether this
> is over-engineered?** Read the plain-English rationale first:
> [`docs/decisions/2026-06-30-config-ssm-secrets-rationale.md`](../decisions/2026-06-30-config-ssm-secrets-rationale.md).
> It explains what each service is, how the count of 7 was derived (one per non-secret
> field), why we chose this design, and the leaner future alternative (env vars + 1 SSM
> SecureString).

**Values used throughout (don't substitute — these are this project's):**

| Thing | Value |
|---|---|
| AWS account id | `048589483919` |
| Region | **Asia Pacific (Mumbai) `ap-south-1`** |
| SSM prefix | `/marketplace-listing/` |
| S3 bucket | `ijorethnicpartners` |
| S3 region | `ap-south-1` |
| S3 prefix | `myntra/` |

> Sign in with an IAM user that has SSM and Secrets Manager write permissions.
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

## 2. Create the Secrets Manager secret

> **Console UI note (2026).** "Plaintext" is no longer a top-level secret type. On the
> **Choose secret type** screen, the top-level options are RDS/DocumentDB/Redshift/other-DB
> credentials and **Other type of secret**. Plaintext lives inside that last one.

1. Open **Secrets Manager** → **Secrets** → **Store a new secret**.
2. Secret type: select **Other type of secret** ("API key, OAuth token, other"). The
   credentials editor changes to a **Key/value | Plaintext** toggle — click the
   **Plaintext** tab.
3. Secret value: paste **only the raw client secret** (the `cognito_client_secret` string
   from the Cognito runbook, Task 8). No JSON, no quotes, no key name — the app reads the
   secret string verbatim via `get_secret_value(...)["SecretString"]`.
4. **Encryption key:** leave the default `aws/secretsmanager`.
5. **Next**.
6. Secret name: `/marketplace-listing/cognito_client_secret`
7. **Next**.
8. Leave **Automatic rotation** unchecked (optional for dev; configure for production).
9. **Next** → **Store secret**.

---

## 3. Verify the setup locally

The settings loader (`src/web/settings.py`) reads each config value from the environment first, then falls back per-field to SSM Parameter Store (non-secret values) or Secrets Manager (the `cognito_client_secret`). This means you can set some values via environment and leave the rest to AWS.

> **What you can and can't verify today.** The hosted-UI login redirect and the
> `/auth/callback` route are **not built yet** (deferred to the deploy phase). So running
> the app with auth enabled will **not** redirect you to the Cognito login page — with no
> token, `current_user` (`src/web/auth.py`) raises `AuthError`. What you *can* verify now
> is that the settings loader reads your new SSM/Secrets values back from AWS.

1. Ensure your AWS credentials are active and your IAM user can read SSM + Secrets Manager:

   ```bash
   aws sts get-caller-identity
   ```

2. Confirm per-field fallback resolves the values from AWS (env vars popped so it must hit AWS):

   ```powershell
   $env:AWS_REGION="ap-south-1"
   python -c "import os; [os.environ.pop(k, None) for k in ('S3_BUCKET','S3_REGION','S3_PREFIX','COGNITO_POOL_ID','COGNITO_CLIENT_ID','COGNITO_CLIENT_SECRET','COGNITO_DOMAIN','COGNITO_REDIRECT_URI')]; from src.web.settings import load_settings; s=load_settings(); print('pool:', s.cognito_pool_id); print('client:', s.cognito_client_id); print('secret loaded:', bool(s.cognito_client_secret))"
   ```

   Seeing the pool id, client id, and `secret loaded: True` confirms the SSM parameters and
   the Secrets Manager secret are stored and readable.

**Note:** In production (Phase 4), the EC2 instance will have an IAM instance role that grants `ssm:GetParameter*` and `secretsmanager:GetSecretValue` permissions. Locally, your IAM user provides those permissions. The end-to-end login check (redirect to Cognito → `/auth/callback` → cookie) becomes possible only once that route is implemented in the deploy phase.

---

## 4. Teardown (only if you need to undo this)

- **SSM parameters**: Systems Manager → Parameter Store → select each `/marketplace-listing/*` parameter → **Delete parameter** (repeat for all 7 parameters).
- **Secrets Manager secret**: Secrets Manager → Secrets → select `/marketplace-listing/cognito_client_secret` → **Delete secret** → choose **Confirm deletion** and optionally set recovery window or delete immediately.

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
