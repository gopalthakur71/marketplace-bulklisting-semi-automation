# Runbook — CI/CD AWS setup via the **Console** (GitHub Actions → ECR)

Same outcome as the CLI runbook ([cicd-aws-setup.md](cicd-aws-setup.md)), done
through the AWS web console + GitHub UI. One-time setup so the `ci-cd` workflow can
push images. Do the steps in order.

**Values used throughout (don't substitute — these are this project's):**

| Thing | Value |
|---|---|
| AWS account id | `048589483919` |
| Region (ECR) | **Asia Pacific (Mumbai) `ap-south-1`** |
| ECR repository | `marketplace-bulklisting` |
| IAM role | `github-actions-ecr-push` |
| OIDC provider | `token.actions.githubusercontent.com` |
| OIDC audience | `sts.amazonaws.com` |
| GitHub repo | `gopalthakur71/marketplace-bulklisting-semi-automation` |
| GitHub branch (trust scope) | `main` |
| GitHub secret | `AWS_ACCOUNT_ID` = `048589483919` |

> Sign in with the IAM user that carries the bootstrap policy (the `meta-ad-banner`
> user augmented with `EcrBootstrap` / `OidcProviderBootstrap` / `GithubActionsRole`).

---

## 1. Create the ECR repository

1. Set the **region selector** (top-right of the console) to **Asia Pacific (Mumbai)
   ap-south-1**. ECR is regional — if this is wrong the workflow's ECR login fails.
2. Open **Elastic Container Registry** → **Repositories** → **Create repository**.
3. Visibility settings: **Private**.
4. Repository name: `marketplace-bulklisting`.
5. **Image scan settings** → turn **on** "Scan on push".
6. **Create repository**.

### 1a. Lifecycle policy (keep last 10 images)

1. Open the `marketplace-bulklisting` repo → **Lifecycle Policy** tab → **Create rule**.
2. Rule priority: `1`
3. Description: `Keep only the 10 most recent images`
4. Image status: **Any** (tagged and untagged).
5. Match criteria: **Since image count more than** → `10`.
6. Action defaults to **Expire** → **Save**.

---

## 2. Create the GitHub OIDC identity provider

IAM is global — region doesn't matter for steps 2–3.

1. Open **IAM** → **Identity providers** → **Add provider**.
   - If `token.actions.githubusercontent.com` already appears in the list, **skip to
     step 3** (only one OIDC provider per URL per account).
2. Provider type: **OpenID Connect**.
3. Provider URL: `https://token.actions.githubusercontent.com` → click **Get thumbprint**.
4. Audience: `sts.amazonaws.com`.
5. **Add provider**.

---

## 3. Create the role `github-actions-ecr-push`

1. Open **IAM** → **Roles** → **Create role**.
2. Trusted entity type: **Web identity**.
3. Identity provider: **token.actions.githubusercontent.com**.
4. Audience: **sts.amazonaws.com**.
5. The GitHub-specific fields appear — fill them **exactly**:
   - GitHub organization: `gopalthakur71`
   - GitHub repository: `marketplace-bulklisting-semi-automation`
   - GitHub branch: `main`
6. **Next** → on the Add permissions page attach nothing yet → **Next**.
7. Role name: `github-actions-ecr-push` → **Create role**.

### 3a. Attach the ECR push permissions (inline policy)

1. Open the new role → **Permissions** tab → **Add permissions** → **Create inline policy**.
2. Switch to the **JSON** tab and paste (account id already filled in):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "EcrAuth",
         "Effect": "Allow",
         "Action": "ecr:GetAuthorizationToken",
         "Resource": "*"
       },
       {
         "Sid": "EcrPushPull",
         "Effect": "Allow",
         "Action": [
           "ecr:BatchCheckLayerAvailability",
           "ecr:InitiateLayerUpload",
           "ecr:UploadLayerPart",
           "ecr:CompleteLayerUpload",
           "ecr:PutImage",
           "ecr:BatchGetImage",
           "ecr:GetDownloadUrlForLayer"
         ],
         "Resource": "arn:aws:ecr:ap-south-1:048589483919:repository/marketplace-bulklisting"
       }
     ]
   }
   ```
3. **Next** → policy name: `ecr-push` → **Create policy**.

### 3b. Verify the trust policy

1. On the role → **Trust relationships** tab → **Edit trust policy**.
2. Confirm it matches this **exactly** — the console sometimes writes the `sub` as a
   wildcard (`...:*`) instead of the branch; if so, correct it:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "Federated": "arn:aws:iam::048589483919:oidc-provider/token.actions.githubusercontent.com"
         },
         "Action": "sts:AssumeRoleWithWebIdentity",
         "Condition": {
           "StringEquals": {
             "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
           },
           "StringLike": {
             "token.actions.githubusercontent.com:sub": "repo:gopalthakur71/marketplace-bulklisting-semi-automation:ref:refs/heads/main"
           }
         }
       }
     ]
   }
   ```
3. **Update policy** if you changed anything.
4. Copy the **Role ARN** at the top of the page. It should read
   `arn:aws:iam::048589483919:role/github-actions-ecr-push` — this is what the workflow
   assumes (it builds the ARN from the `AWS_ACCOUNT_ID` secret).

---

## 4. Tell GitHub the account id (repo secret)

1. In the browser go to the GitHub repo → **Settings** → **Secrets and variables** →
   **Actions** → **Secrets** tab → **New repository secret**.
2. Name: `AWS_ACCOUNT_ID`
3. Secret: `048589483919`
4. **Add secret**.

> Why only the account number? Auth is GitHub OIDC — no AWS keys are stored. GitHub
> presents a short-lived token and assumes the role above; the workflow only needs the
> account id to build the role ARN.

---

## 5. Run the pipeline and verify

The workflow ([.github/workflows/ci-cd.yml](../../.github/workflows/ci-cd.yml)) runs on
push to `main` or via the manual button.

1. GitHub repo → **Actions** tab → select the **ci-cd** workflow → **Run workflow**
   (choose branch `main`) — or just push a commit to `main`.
2. Watch the run:
   - `test` job runs pytest and must pass;
   - then `build-and-push` assumes the role via OIDC, logs in to ECR, builds and pushes.
3. Confirm the image landed: AWS console → **ECR** → `marketplace-bulklisting` repo →
   **Images** tab → you should see tags `latest` and the commit `<sha>`.

If `build-and-push` doesn't appear, that's expected on a `pull_request` or on any branch
other than `main` — it only runs for `main` (push or dispatch).

---

## Troubleshooting

- **`build-and-push` fails at "Configure AWS credentials" with
  `Not authorized to perform sts:AssumeRoleWithWebIdentity`** → the trust policy `sub`
  doesn't match. Re-check step 3b: org/repo spelling and `ref:refs/heads/main`.
- **AccessDenied creating the ECR repo (step 1)** → some accounts evaluate
  `ecr:CreateRepository` at account level. In the bootstrap policy on your IAM user,
  widen the `EcrBootstrap` statement's `Resource` to `"*"`, then retry.
- **ECR login fails in the workflow** → the repo was created in the wrong region. It
  must be `ap-south-1` (step 1.1).
- **OIDC "provider already exists" when adding it** → fine, that's step 2's skip case;
  go straight to the role.

---

## Teardown (only if you need to undo this)

- ECR: console → ECR → select `marketplace-bulklisting` → **Delete**.
- IAM role: console → IAM → Roles → `github-actions-ecr-push` → **Delete** (delete the
  inline `ecr-push` policy first if prompted).
- OIDC provider: console → IAM → Identity providers →
  `token.actions.githubusercontent.com` → **Delete** (only if nothing else uses it).
- GitHub secret: repo Settings → Secrets → remove `AWS_ACCOUNT_ID`.
