# Runbook — SSM Parameter Store & Secrets Manager Setup via the **Console**

Same outcome as the CLI runbook, done through the AWS web console. This sets up the configuration parameters and secrets that the Marigold Ops web app reads on startup when environment variables are not set. The settings loader falls back per-field: each config value is read from the environment first, then from AWS Systems Manager Parameter Store (SSM) or Secrets Manager if absent. Do the steps in order.

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

1. Open **Secrets Manager** → **Secrets** → **Store a new secret**.
2. Secret type: **Plaintext**.
3. Secret name: `/marketplace-listing/cognito_client_secret`
4. Secret value: `<paste the Client secret from the Cognito runbook, Task 8>`
5. **Next**.
6. Leave **Automatic rotation** unchecked (optional for dev; configure for production).
7. **Next** → **Store secret**.

---

## 3. Verify the setup locally

The settings loader (`src/web/settings.py`) reads each config value from the environment first, then falls back per-field to SSM Parameter Store (non-secret values) or Secrets Manager (the `cognito_client_secret`). This means you can set some values via environment and leave the rest to AWS.

1. Unset the env vars so the loader falls back to AWS:

   ```bash
   unset S3_BUCKET S3_REGION S3_PREFIX COGNITO_POOL_ID COGNITO_CLIENT_ID COGNITO_CLIENT_SECRET COGNITO_DOMAIN COGNITO_REDIRECT_URI
   ```

2. Ensure your AWS credentials are set in the shell (your IAM user must have permissions to read SSM and Secrets Manager):

   ```bash
   # Verify AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, or AWS_PROFILE are set
   aws sts get-caller-identity
   ```

3. Run the app:

   ```bash
   uvicorn src.web.main:app --reload
   ```

4. The app should boot successfully. Check the logs — you should see no "missing config" errors.

5. Open `http://localhost:8000/` in a browser. If using Cognito (not dev bypass), you should be redirected to the Cognito login page. If you see it, the settings loaded correctly.

**Note:** In production (Phase 4), the EC2 instance will have an IAM instance role that grants `ssm:GetParameter*` and `secretsmanager:GetSecretValue` permissions. Locally, your IAM user provides those permissions.

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
