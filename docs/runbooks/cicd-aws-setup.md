# Runbook — CI/CD AWS setup (GitHub Actions → ECR)

One-time setup so the `ci-cd` workflow can push images. Run these in **your**
AWS account (`ap-south-1`) with admin credentials. Policy JSON lives in
`aws/cicd/`. Replace `<ACCOUNT_ID>` with your 12-digit AWS account id first:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "$ACCOUNT_ID"
# Fill the placeholder in local copies the CLI will send:
sed "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" aws/cicd/oidc-trust-policy.json > /tmp/trust.json
sed "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" aws/cicd/ecr-push-permissions.json > /tmp/perms.json
```

## 1. Create the ECR repository (scan-on-push)

```bash
aws ecr create-repository \
  --repository-name marketplace-bulklisting \
  --region ap-south-1 \
  --image-scanning-configuration scanOnPush=true
```

Apply the lifecycle policy (keep last 10 images):

```bash
aws ecr put-lifecycle-policy \
  --repository-name marketplace-bulklisting \
  --region ap-south-1 \
  --lifecycle-policy-text file://aws/cicd/ecr-lifecycle-policy.json
```

## 2. Create the GitHub OIDC identity provider (once per account)

Skip if it already exists (check: `aws iam list-open-id-connect-providers`).

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

(AWS now validates the OIDC thumbprint automatically, but the parameter is
still required by the API.)

## 3. Create the role and attach the push policy

```bash
aws iam create-role \
  --role-name github-actions-ecr-push \
  --assume-role-policy-document file:///tmp/trust.json

aws iam put-role-policy \
  --role-name github-actions-ecr-push \
  --policy-name ecr-push \
  --policy-document file:///tmp/perms.json
```

## 4. Tell GitHub the account id

The workflow reads the account id from a repo secret (keeps the number out of
the committed file):

```bash
gh secret set AWS_ACCOUNT_ID --body "$ACCOUNT_ID" \
  --repo gopalthakur71/marketplace-bulklisting-semi-automation
```

## 5. Verify

Push to `main` (or use the Actions "Run workflow" button). Confirm:
- the `test` job passes, then `build-and-push` runs;
- an image appears: `aws ecr list-images --repository-name marketplace-bulklisting --region ap-south-1`.

## Teardown (if ever needed)

```bash
aws ecr delete-repository --repository-name marketplace-bulklisting --region ap-south-1 --force
aws iam delete-role-policy --role-name github-actions-ecr-push --policy-name ecr-push
aws iam delete-role --role-name github-actions-ecr-push
```
