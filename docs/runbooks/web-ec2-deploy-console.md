# Runbook — Deploy the Marigold Ops web app to EC2 via the **Console**

Phase 4: take the image that CI already publishes to ECR and run it on a **start/stop
EC2 t3.micro**, with all AWS access via an **instance role** (no keys in the container).
Done through the AWS web console. Do the steps in order.

> **Design recap (from `docs/superpowers/specs/2026-06-25-listing-app-cicd-deploy.md`):**
> this box is a deliberate **"pet"** — one named instance you start, stop, and let pull its
> own image. **Boot = deploy:** a systemd unit pulls `:latest` from ECR every time the box
> starts, so starting the box deploys the newest build. No ALB, no Elastic IP, no NAT — just
> the auto-assigned public IP (changes per start; fine for an internal tool). Idle cost
> ≈ **under $1/month** (you pay only for the ~8 GB EBS root volume while stopped).

**Values used throughout (this project's actual values — some differ from the old spec):**

| Thing | Value |
|---|---|
| AWS account id | `048589483919` |
| Region | **Asia Pacific (Mumbai) `ap-south-1`** |
| ECR repo | `marketplace-bulklisting` |
| Image URI | `048589483919.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest` |
| Instance role name | `listing-app-ec2-role` |
| Instance type | `t3.micro` |
| App module / port | `src.web.main:app` on **8080** inside the container |
| Host port mapping | host **80** → container **8080** |
| SSM prefix | **`/marketplace-listing/`** (NOT `/listing-app/`) |
| Secret name | `/marketplace-listing/cognito_client_secret` |
| S3 bucket | `ijorethnicpartners` |

> Sign in with an IAM user that can create IAM roles, security groups, and EC2 instances.

---

## Prerequisites (all already done — confirm before starting)

- [x] **Image in ECR** — CI publishes `marketplace-bulklisting:latest` to ap-south-1.
- [x] **Cognito** provisioned (pool `ap-south-1_NdxNQ1plz`, client `29oo5dtqh8j50k2481lmffqb0e`,
      domain `ijor-marketplace`) — see `web-cognito-setup-console.md`.
- [x] **SSM params + Secrets Manager secret** stored under `/marketplace-listing/*` — see
      `web-ssm-secrets-setup-console.md`.

> **Deploy in two stages.** The app has **no hosted-UI login route yet** (`/auth/callback`
> is not built — see Step 5). So:
> - **Stage 1 (Steps 1–4):** deploy and run the container with **`AUTH_DISABLED=1`** to prove
>   the box, the instance role, the ECR pull, and reachability all work. No login needed.
> - **Stage 2 (Steps 5–6):** build the `/auth/callback` + login route (code), register the
>   prod callback URL in Cognito, point config at SSM/Secrets, and flip auth on.

---

## 1. Create the EC2 instance role

The instance role lets the box pull from ECR and lets the app read S3 / SSM / Secrets —
**without any access keys**. It replaces the local IAM-user keys used during development.

> **Note on the console flow:** the "create the policy inline while creating the role" path
> isn't reliable in the current console — it's cleaner to do it in **three separate steps**:
> **(A) create the role**, **(B) create the policy**, **(C) attach the policy to the role**.
> That's the order below.

**A. Create the role (no policy yet):**

1. Region selector → **Asia Pacific (Mumbai) ap-south-1**.
2. Open **IAM** → **Roles** → **Create role**.
3. Trusted entity type: **AWS service**. Use case: **EC2** → **Next**.
4. Skip attaching permissions for now (don't select anything) → **Next**.
5. Role name: `listing-app-ec2-role` → **Create role**.

**B. Create the policy separately:**

6. **IAM** → **Policies** → **Create policy** → **JSON** tab → replace the contents with:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "EcrPull",
         "Effect": "Allow",
         "Action": [
           "ecr:GetAuthorizationToken",
           "ecr:BatchCheckLayerAvailability",
           "ecr:BatchGetImage",
           "ecr:GetDownloadUrlForLayer"
         ],
         "Resource": "*"
       },
       {
         "Sid": "AppS3",
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": [
           "arn:aws:s3:::ijorethnicpartners/myntra/*",
           "arn:aws:s3:::ijorethnicpartners/state/myntra_groupid.json"
         ]
       },
       {
         "Sid": "AppConfig",
         "Effect": "Allow",
         "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
         "Resource": "arn:aws:ssm:ap-south-1:048589483919:parameter/marketplace-listing/*"
       },
       {
         "Sid": "AppSecret",
         "Effect": "Allow",
         "Action": "secretsmanager:GetSecretValue",
         "Resource": "arn:aws:secretsmanager:ap-south-1:048589483919:secret:/marketplace-listing/*"
       }
     ]
   }
   ```

   - `ecr:GetAuthorizationToken` must be `Resource: "*"` (account-level action); everything
     else is scoped. The SSM/Secrets ARNs use the **real** `/marketplace-listing/` prefix.
   - **Secrets Manager ARN note:** Secrets Manager appends a random 6-char suffix to secret
     ARNs. The `secret:/marketplace-listing/*` wildcard covers it. If access is still denied,
     widen to `secret:*marketplace-listing*` or paste the exact ARN from the secret's page.
7. **Next** → policy name: `listing-app-runtime` → **Create policy**.

**C. Attach the policy to the role:**

8. **IAM** → **Roles** → open `listing-app-ec2-role` → **Add permissions** → **Attach policies**.
9. Search for `listing-app-runtime`, tick it → **Add permissions**.

   The role now has the runtime policy attached (as a customer-managed policy rather than an
   inline one — functionally identical for this instance; it's also reusable if you add more
   boxes later).

---

## 2. Create the security group

1. Open **EC2** → **Network & Security** → **Security Groups** → **Create security group**.
2. Name: `listing-app-sg`. Description: `Marigold Ops web app`. VPC: default.
3. **Inbound rules** → Add rule:
   - **HTTP**, port **80**, source **My IP** (recommended for an internal tool — restricts to
     your address). Use `0.0.0.0/0` only if others need access; this app is internal.
   - *(Skip 443 for now — TLS is added later if you put it behind a proxy / domain.)*
   - *(Optional)* **SSH** port 22 from **My IP** if you want to shell in for debugging.
4. Leave outbound as default (allow all — needed to reach ECR/SSM/Secrets) → **Create**.

---

## 3. Launch the EC2 instance (with role + user-data)

1. **EC2** → **Instances** → **Launch instances**.
2. Name: `listing-app`.
3. **AMI:** **Amazon Linux 2023** (the user-data below uses `dnf`).
4. **Instance type:** `t3.micro`.
5. **Key pair:** create or pick one if you want SSH; otherwise "Proceed without a key pair".
6. **Network settings** → **Edit** → **Select existing security group** → `listing-app-sg`.
7. **Advanced details:**
   - **IAM instance profile:** `listing-app-ec2-role`.
   - **User data:** paste the script below (it installs Docker and a systemd unit that pulls
     `:latest` on every start). **Stage 1** runs with `AUTH_DISABLED=1`.

   ```bash
   #!/bin/bash
   set -euo pipefail
   dnf install -y docker
   systemctl enable --now docker

   ACCOUNT=048589483919
   REGION=ap-south-1
   IMAGE=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/marketplace-bulklisting:latest

   cat >/etc/systemd/system/listing-app.service <<UNIT
   [Unit]
   Description=Marketplace Listing App
   After=docker.service
   Requires=docker.service

   [Service]
   ExecStartPre=-/usr/bin/docker rm -f listing-app
   ExecStartPre=/bin/bash -lc 'aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com'
   ExecStartPre=/usr/bin/docker pull $IMAGE
   ExecStart=/usr/bin/docker run --rm --name listing-app -p 80:8080 \
     -e AUTH_DISABLED=1 \
     -e AWS_REGION=$REGION \
     $IMAGE
   ExecStop=/usr/bin/docker stop listing-app
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   UNIT

   systemctl daemon-reload
   systemctl enable --now listing-app.service
   ```

   - Host port **80** maps to the container's **8080** (the Dockerfile's `CMD --port 8080`).
   - The `aws` CLI is preinstalled on Amazon Linux 2023 and authenticates via the instance role.
   - ⚠️ **Pasting from Windows:** make sure the user-data has **Unix (LF) line endings, not
     CRLF**. If a `\r` sneaks into the `#!/bin/bash` shebang line, the whole script fails
     **silently** (Docker never installs, no service is created — symptom: port 80 refuses and
     `systemctl status listing-app` says *"Unit could not be found"*). The console text box
     normally preserves what you paste, so paste from a plain-text source, not a rich editor.
8. **Launch instance.**

---

## 4. Verify (Stage 1 — infra works)

1. Wait until the instance shows **Running** and **2/2 status checks passed** (~2–3 min;
   user-data adds another minute to install Docker + pull the image).
2. Copy the instance's **Public IPv4 address**.
3. Open `http://<public-ip>/` in a browser → you should see the **Marigold Ops** dashboard
   logged in as **`dev@local`** (because `AUTH_DISABLED=1`).
4. If it doesn't load, SSH in (or use **EC2 Instance Connect**) and check:
   ```bash
   # 0. Is the instance role actually attached? (returns an ARN with
   #    assumed-role/listing-app-ec2-role/... — if it errors, the profile wasn't attached)
   aws sts get-caller-identity --region ap-south-1

   sudo systemctl status listing-app
   sudo journalctl -u listing-app -n 50 --no-pager
   sudo docker ps
   ```
   Common causes: image pull denied (instance role / ECR), port not 8080, security group
   doesn't allow your IP on 80.

   **If `systemctl status` says "Unit listing-app.service could not be found" or `docker` is
   "command not found" → user-data never ran** (most often CRLF in the shebang — see Step 3).
   Confirm and recover **without relaunching**:
   ```bash
   # Did user-data run / error? What was actually delivered?
   sudo cat /var/log/cloud-init-output.log | tail -60
   sudo cat /var/lib/cloud/instance/user-data.txt | head -5   # check the shebang line

   # First make sure the instance role is attached (Step 0 above). Then re-run the
   # Step 3 script by hand — paste the same #!/bin/bash … block, e.g.:
   sudo bash -s <<'EOF'
   set -euo pipefail
   dnf install -y docker
   systemctl enable --now docker
   # …paste the rest of the Step 3 user-data body here (ACCOUNT=… through
   #   systemctl enable --now listing-app.service)…
   EOF
   ```
   Running it manually is equivalent to what user-data would have done; no relaunch needed.

**Stage 1 done = the deploy mechanism works.** You can stop the box now; starting it again
re-pulls `:latest` and redeploys.

---

## Stage 2 — enable real auth (My-IP-only, no TLS)

Prereq: the app image includes the /login, /auth/callback, /logout routes
(plan 2026-07-01-web-auth-stage2-cognito-login).

1. **Cognito app client** (`marketplace-listing-pool`, pool `ap-south-1_NdxNQ1plz`):
   - Token expiration → set **ID token** validity to **8 hours** (keeps re-logins rare).
   - Allowed callback URLs → add `http://<EC2_PUBLIC_IP>/auth/callback`
     (keep `http://localhost:8000/auth/callback` for local dev).
   - Allowed sign-out URLs → add `http://<EC2_PUBLIC_IP>/`.
2. **SSM Parameter Store** (`/marketplace-listing/`): confirm `cognito_domain`,
   `cognito_client_id`, `cognito_pool_id`, `s3_region` are set, and set
   `cognito_redirect_uri = http://<EC2_PUBLIC_IP>/auth/callback`. The client
   secret stays in Secrets Manager. Confirm the `listing-app-ec2-role` policy
   allows `ssm:GetParameter` + `secretsmanager:GetSecretValue` on those paths.
3. **systemd unit** `listing-app.service`: remove `AUTH_DISABLED=1`. Leave
   `COOKIE_SECURE` unset (off) — there is no TLS yet.
4. **Redeploy:** `sudo systemctl restart listing-app` (re-pulls `:latest`).
5. **Verify** from the allowed IP: hit `/` → bounced to Cognito hosted UI →
   log in → land back on the dashboard; `/logout` clears the session.
6. **Keep access My-IP-only.** Do NOT open the SG to `0.0.0.0/0` — with no TLS
   the id_token cookie is sniffable in transit (a later chunk adds TLS + public).

**IP-change caveat:** stopping/starting the instance changes its public IP,
which breaks the registered callback. On restart, update the Cognito callback +
sign-out URLs and the `cognito_redirect_uri` SSM param to the new IP. (Add an
Elastic IP later only if restarts become frequent — it is free while attached to
a running instance, but billed while the instance is stopped.)

---

## 5. (Code task) Build the `/auth/callback` + login route

> This is **application code, not AWS** — it's the missing piece that makes Cognito login
> work end-to-end. Until it exists, the app can only run with `AUTH_DISABLED=1`.

What needs adding to `src/web` (a future implementation task, not part of this console runbook):
1. A **`/login`** route that redirects to the Cognito hosted UI:
   `https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/login?client_id=<CLIENT_ID>&response_type=code&redirect_uri=<prod-callback>&scope=openid+email`
2. A **`/auth/callback`** route that exchanges the `?code=` for tokens at the Cognito
   `/oauth2/token` endpoint (using the client id + secret), then sets the `id_token` as an
   HttpOnly cookie. The existing `current_user`/`verify_jwt` in `src/web/auth.py` already
   validates that cookie — this route just obtains and stores it.
3. A **`/logout`** route that clears the cookie and hits the Cognito `/logout` endpoint.

Plan and build this with the usual TDD flow before doing Stage 2.

---

## 6. (Stage 2) Turn on real auth

Once Step 5 is built and deployed:

1. **Register the prod callback URL in Cognito** (the public IP changes per start, so this is
   easier once you have a stable URL / domain; for an IP-based test, use the current IP):
   - Cognito → pool → **Applications → App clients** → your client → **Login pages → Edit**.
   - Add to **Allowed callback URLs**: `http://<prod-host>/auth/callback`
   - Add to **Allowed sign-out URLs**: `http://<prod-host>/`
   - Save.
2. **Update the deploy to read config from AWS and enable auth.** Edit the user-data (or the
   systemd unit on the box) `docker run` line: **remove `-e AUTH_DISABLED=1`** and set
   `-e COGNITO_REDIRECT_URI=http://<prod-host>/auth/callback`. Everything else (pool id,
   client id, secret, domain, S3) resolves automatically from SSM/Secrets via the instance
   role — no need to pass them as env vars.
   - Also update the SSM param `/marketplace-listing/cognito_redirect_uri` to the prod URL.
3. Restart: `sudo systemctl restart listing-app` (or stop/start the instance).
4. Open `http://<prod-host>/` → you should now be redirected to the Cognito login page; sign
   in with the test user → land back in the app authenticated.

> **TLS / stable URL (when ready):** the IP changes each start and HTTP is unencrypted. For a
> real internal URL, put the box behind a small reverse proxy with a domain + Let's Encrypt
> cert, or front it with an ALB + ACM cert (adds cost). The callback URL must then be the
> `https://…` address. Deferred until needed.

---

## 7. Start / stop workflow (day-to-day)

- **To use the app:** EC2 → Instances → select `listing-app` → **Instance state → Start**.
  Wait ~2 min (it re-pulls `:latest`), grab the new public IP, use it.
- **When done:** **Instance state → Stop** (compute billing stops; you keep only the EBS cost).
- **To deploy a new build:** merge to `main` (CI pushes a new `:latest`) → start (or restart)
  the box → it pulls the new image.

---

## 8. Teardown (only if you need to undo this)

- **Instance:** EC2 → Instances → select → **Instance state → Terminate**.
- **Security group:** EC2 → Security Groups → `listing-app-sg` → **Delete** (after the
  instance is gone).
- **Instance role:** IAM → Roles → `listing-app-ec2-role` → **Delete**.
- ECR image, Cognito, SSM/Secrets are shared with CI/other phases — leave them.

---

## 9. Deploy checklist

One-time AWS (console):
- [ ] Instance role `listing-app-ec2-role` created, `listing-app-runtime` policy created and
      attached to it (Step 1 A/B/C).
- [ ] Security group `listing-app-sg` (port 80 from your IP) (Step 2).
- [ ] Launch `t3.micro` (Amazon Linux 2023) with the role + user-data (Step 3).
- [ ] Verify the app on `http://<public-ip>/` with `AUTH_DISABLED=1` (Step 4).

Code + cutover:
- [ ] Build `/login` + `/auth/callback` + `/logout` (Step 5).
- [ ] Register prod callback URL in Cognito; drop `AUTH_DISABLED`; restart (Step 6).
- [ ] (Later) TLS + stable URL/domain.

Docs (do this whenever the above changes the system, not optional):
- [ ] After building `/login` + `/auth/callback` + `/logout`: update `docs/ARCHITECTURE.md`
      §5 (web routes/flow) and §7 (Cognito boundary — drop the "not built yet" caveat), and
      the deferred-auth note in §6. Update the `AGENTS.md` invariants if the auth model changes.
- [ ] When adding a new marketplace (e.g. Amazon) or any new module/integration: update
      `docs/ARCHITECTURE.md` §2 (layout), the relevant layer section, and §7 (boundaries);
      add an ADR under `docs/decisions/` for any non-obvious choice.

Related: `docs/runbooks/web-cognito-setup-console.md`,
`docs/runbooks/web-ssm-secrets-setup-console.md`,
`docs/runbooks/cicd-aws-setup-console.md`,
design spec `docs/superpowers/specs/2026-06-25-listing-app-cicd-deploy.md`.
</content>
