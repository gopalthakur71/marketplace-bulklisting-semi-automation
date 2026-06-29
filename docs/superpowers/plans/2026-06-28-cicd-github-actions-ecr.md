# CI/CD Pipeline (GitHub Actions → ECR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every push to `main`, run the test suite and — only if it passes — build a Docker image of the app and push it to Amazon ECR, with **no long-lived AWS keys** stored in GitHub (GitHub OIDC → short-lived role).

**Architecture:** A two-job GitHub Actions workflow (`test` gate → `build-and-push`). The `build-and-push` job authenticates to AWS by exchanging a GitHub OIDC token for temporary credentials on an IAM role scoped to this repo's `main` branch, then pushes an image tagged with both the git SHA and `:latest` to a private ECR repo. **This branch is CI-only:** it stops at "image in ECR." The EC2 start-on-demand deploy (spec §7) and the runnable FastAPI web server are deferred to later phases — the image's `CMD` is the existing CLI as a valid placeholder until then. All AWS account-side setup is delivered as committed policy JSON + a runbook the repo owner executes ("I write, you run").

**Tech Stack:** GitHub Actions, Docker (`python:3.12-slim`), Amazon ECR, AWS IAM (OIDC federation), `aws-actions/configure-aws-credentials@v4`, `aws-actions/amazon-ecr-login@v2`.

**Spec:** `docs/superpowers/specs/2026-06-25-listing-app-cicd-deploy.md` (this plan implements §1–§6 and §10; §7 EC2 deploy and §8 cost/§9 deferred items are out of scope for this branch by decision).

## Global Constraints

- GitHub repo: `gopalthakur71/marketplace-bulklisting-semi-automation` — copy verbatim into OIDC trust `sub`.
- AWS region: `ap-south-1` (everywhere).
- ECR repository name: `marketplace-bulklisting` (private).
- IAM role for the pipeline: `github-actions-ecr-push`.
- Python: `3.12` (runner `setup-python` and Docker base `python:3.12-slim` must match).
- Image tags: `<git-sha>` (immutable, traceable) **and** `:latest` (mutable pointer).
- **No long-lived AWS access keys anywhere** — OIDC short-lived credentials only.
- Least-privilege IAM: `ecr:GetAuthorizationToken` is account-level (`Resource: "*"`); every other ECR action is scoped to the single repo ARN.
- Existing test command: `python -m pytest -q` (41 tests, currently green).
- Do not commit real secrets; account id appears as `<ACCOUNT_ID>` placeholder in committed files, filled in only by the runbook executor in their own copies / live AWS.

---

### Task 1: Docker image + build context

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: existing `requirements.txt` (pandas, openpyxl, Pillow, PyYAML, requests, boto3, pytest), `src/`, `config/`, `templates/`, `run.py`.
- Produces: a locally buildable image `listing-app:test` whose runtime deps and app package import cleanly. Later tasks (workflow) build this same `Dockerfile`.

- [ ] **Step 1: Write the `.dockerignore`**

Keeps the build context lean and secrets/artefacts out of the image. Create `.dockerignore`:

```
.git
.github
.gitignore
__pycache__/
*.pyc
.pytest_cache/
.venv/
venv/
tests/
docs/
.superpowers/
input/
output/
errors/
_docx_extract/
_tpl/
S3/
aws/
*.md
~$*
```

- [ ] **Step 2: Write the `Dockerfile`**

Create `Dockerfile`. Deps are copied/installed before source so the dependency layer is cached across code-only changes:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Dependencies first for layer caching (changes to src/ won't reinstall deps).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and the data it reads at runtime.
COPY src ./src
COPY config ./config
COPY templates ./templates
COPY run.py ./

# The web server (uvicorn src.web.app:app) arrives in a later phase. Until then
# the image's default entrypoint is the existing CLI; this CI-only phase builds
# and pushes the image but does NOT run the container, so this CMD is just a
# valid default (swapped to uvicorn when the FastAPI app lands).
CMD ["python", "run.py"]
```

- [ ] **Step 3: Build the image locally to verify it builds**

Run: `docker build -t listing-app:test .`
Expected: build completes, ends with `naming to docker.io/library/listing-app:test` (exit 0). If a system lib is missing for Pillow/pandas wheels, the build fails here — add a minimal `apt-get install` only if that actually happens (don't pre-add speculatively).

- [ ] **Step 4: Smoke-test that runtime deps and app code import inside the image**

Run:
```bash
docker run --rm listing-app:test python -c "import pandas, openpyxl, PIL, yaml, boto3, requests; from src.myntra import pipeline; print('image-ok')"
```
Expected: prints `image-ok` (exit 0). This proves the image has the runtime dependencies and the `src` package is importable — the meaningful "does the image work" check for a CI-only phase.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(cicd): Dockerfile + .dockerignore for the listing app image"
```

---

### Task 2: GitHub Actions workflow (test gate → build & push)

**Files:**
- Create: `.github/workflows/ci-cd.yml`

**Interfaces:**
- Consumes: `Dockerfile` (Task 1), `requirements.txt`, the AWS role `github-actions-ecr-push` and ECR repo `marketplace-bulklisting` (created by the runbook in Task 3 — referenced by name/ARN here).
- Produces: a workflow that on `push` to `main` runs `test` then `build-and-push`; on `pull_request` runs `test` only (safe early signal, no AWS); and is manually triggerable via `workflow_dispatch`.

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/ci-cd.yml`. Note the two-job gate (`build-and-push` has `needs: test`), the OIDC permissions block, and that the AWS-touching job is gated to `main`/manual so PRs never need credentials:

```yaml
name: ci-cd

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:        # manual "Run workflow" button

# Default read-only; the OIDC token (id-token: write) is granted per-job, only
# to build-and-push — not to the test job that runs untrusted PR code.
permissions:
  contents: read

env:
  AWS_REGION: ap-south-1
  ECR_REPOSITORY: marketplace-bulklisting

jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests (deploy gate)
        run: python -m pytest -q

  build-and-push:
    # Only after tests pass, only on main (push or manual dispatch), never on PRs.
    # The main-branch guard matches the IAM trust policy (refs/heads/main) so a
    # dispatch from another branch can't push :latest.
    needs: test
    if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials via OIDC
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT_ID }}:role/github-actions-ecr-push
          aws-region: ${{ env.AWS_REGION }}

      - name: Log in to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push image
        env:
          REGISTRY: ${{ steps.login-ecr.outputs.registry }}
        run: |
          IMAGE="$REGISTRY/$ECR_REPOSITORY"
          docker build -t "$IMAGE:${{ github.sha }}" -t "$IMAGE:latest" .
          docker push "$IMAGE:${{ github.sha }}"
          docker push "$IMAGE:latest"
```

Note: the account id is read from a GitHub repository **secret** `AWS_ACCOUNT_ID` (it is not itself a credential, but keeping it out of the committed file avoids publishing the account number). The runbook (Task 3) tells the owner to set it.

- [ ] **Step 2: Validate the workflow YAML parses**

Run:
```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci-cd.yml')); print('yaml-ok')"
```
Expected: prints `yaml-ok` (exit 0). (CI/CD config is verified end-to-end on GitHub in Task 4 — this step just catches syntax errors before pushing.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci-cd.yml
git commit -m "feat(cicd): GitHub Actions workflow — pytest gate then OIDC build/push to ECR"
```

---

### Task 3: AWS setup — committed IAM policies + runbook ("I write, you run")

**Files:**
- Create: `aws/cicd/oidc-trust-policy.json`
- Create: `aws/cicd/ecr-push-permissions.json`
- Create: `aws/cicd/ecr-lifecycle-policy.json`
- Create: `docs/runbooks/cicd-aws-setup.md`

**Interfaces:**
- Consumes: the role name / repo name / region from Global Constraints; the workflow's expected role ARN and `AWS_ACCOUNT_ID` secret (Task 2).
- Produces: everything the repo owner needs to create the OIDC provider, role, and ECR repo by hand. No code consumes these; they are operator inputs.

- [ ] **Step 1: Write the OIDC trust policy**

Create `aws/cicd/oidc-trust-policy.json` (only this repo's `main` branch may assume the role):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
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

- [ ] **Step 2: Write the ECR push permissions policy**

Create `aws/cicd/ecr-push-permissions.json`:

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
      "Resource": "arn:aws:ecr:ap-south-1:<ACCOUNT_ID>:repository/marketplace-bulklisting"
    }
  ]
}
```

- [ ] **Step 3: Write the ECR lifecycle policy**

Create `aws/cicd/ecr-lifecycle-policy.json` (keep the 10 most recent images, expire older — pennies of storage, good hygiene):

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep only the 10 most recent images",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": { "type": "expire" }
    }
  ]
}
```

- [ ] **Step 4: Write the runbook**

Create `docs/runbooks/cicd-aws-setup.md` with the exact, copy-pasteable steps. Content:

````markdown
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
````

- [ ] **Step 5: Validate the policy JSON files parse**

Run:
```bash
python -c "import json; [json.load(open(f)) for f in ['aws/cicd/oidc-trust-policy.json','aws/cicd/ecr-push-permissions.json','aws/cicd/ecr-lifecycle-policy.json']]; print('json-ok')"
```
Expected: prints `json-ok` (exit 0).

- [ ] **Step 6: Commit**

```bash
git add aws/cicd/ docs/runbooks/cicd-aws-setup.md
git commit -m "docs(cicd): AWS IAM/ECR policies + one-time setup runbook"
```

---

### Task 4: End-to-end verification + README section

**Files:**
- Modify: `README.md` (add a CI/CD section; create the file if absent)

**Interfaces:**
- Consumes: a merged `main` with Tasks 1–3, and the AWS setup performed by the repo owner via the Task 3 runbook.
- Produces: the verified, documented pipeline. This task's "test" is the real GitHub Actions run — it cannot be unit-tested locally, so it is a manual verification gate.

- [ ] **Step 1: Add the CI/CD section to the README**

Add (or create `README.md` with) this section:

```markdown
## CI/CD

On every push to `main`, GitHub Actions runs the test suite and, if it passes,
builds a Docker image and pushes it to a private Amazon ECR repo
(`marketplace-bulklisting`, `ap-south-1`). Authentication uses GitHub OIDC —
**no AWS keys are stored in GitHub**. Pull requests run the test job only.

- Workflow: `.github/workflows/ci-cd.yml`
- One-time AWS setup: `docs/runbooks/cicd-aws-setup.md`
- Design: `docs/superpowers/specs/2026-06-25-listing-app-cicd-deploy.md`

Deferred to a later phase: running the image on a start-on-demand EC2 t3.micro
(spec §7) and the FastAPI web server it serves.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(cicd): document the pipeline in the README"
```

- [ ] **Step 3: Manual verification gate (after merge + AWS setup)**

This is performed by the repo owner once `aws/cicd` setup is done and the branch
is merged to `main`. Checklist:
- [ ] Runbook `docs/runbooks/cicd-aws-setup.md` steps 1–4 completed (ECR repo, OIDC provider, role, `AWS_ACCOUNT_ID` secret).
- [ ] Push to `main` (or trigger "Run workflow"). The `test` job goes green.
- [ ] `build-and-push` runs, assumes the role via OIDC (no stored keys), builds, and pushes.
- [ ] `aws ecr list-images --repository-name marketplace-bulklisting --region ap-south-1` shows a `latest` tag and a git-SHA tag.
- [ ] Open a throwaway PR and confirm only the `test` job runs (no `build-and-push`, no AWS access).

---

## Self-Review

**Spec coverage (against `2026-06-25-listing-app-cicd-deploy.md`):**
- §1 Goal (test → build → push, no long-lived keys): Tasks 1+2. ✓
- §2 big picture diagram: realised by Tasks 1–3 (the EC2 leg is the documented deferral). ✓
- §3 workflow file: Task 2 (extended with `pull_request` test-only + `if` guard; account id via secret instead of inline `<ACCOUNT_ID>` — a security improvement, flagged here as an intentional deviation). ✓
- §4 OIDC → AWS (provider, trust, perms): Task 3 (`oidc-trust-policy.json`, `ecr-push-permissions.json`, runbook §2–§3). ✓
- §5 Dockerfile: Task 1 (CMD is the CLI placeholder, not uvicorn, because the web app is deferred — intentional, documented in the Dockerfile comment). ✓
- §6 ECR repo (scan-on-push + lifecycle): Task 3 (`ecr-lifecycle-policy.json`, runbook §1). ✓
- §7 EC2 deploy, §8 cost, §9 deferred table: **intentionally out of scope** for this CI-only branch (decision recorded in the plan header). Not a gap.
- §10 checklist: covered by the runbook (Task 3) + verification gate (Task 4). ✓

**Placeholder scan:** `<ACCOUNT_ID>` in committed JSON is intentional (filled by the runbook executor); every code/config step shows complete content. No TBD/TODO.

**Type/name consistency:** role `github-actions-ecr-push`, repo `marketplace-bulklisting`, region `ap-south-1`, image tags `${{ github.sha }}`/`latest`, secret `AWS_ACCOUNT_ID` — used identically in Tasks 2, 3, 4.

**Intentional deviations from spec (both improvements):** (1) account id via `secrets.AWS_ACCOUNT_ID` rather than inline; (2) `test` job also runs on `pull_request` for pre-merge signal while `build-and-push` is gated off PRs.
