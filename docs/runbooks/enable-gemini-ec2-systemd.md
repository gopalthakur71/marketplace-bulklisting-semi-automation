# Runbook — Enable Gemini on the EC2 box (systemd unit)

**Purpose.** Part 2 of turning on the Fix-Error flow's Gemini explanations in production. Part 1
(`add-gemini-api-key-ssm.md`) stored the API key in SSM. This part sets the two **env-only** switches
that are NOT in SSM — they live in the `docker run` line of the systemd unit on the EC2 box:

- `EXPLAIN_WITH_GEMINI=1` — the master switch. Without it, `/fix` runs but falls back to YAML rules +
  raw messages (no plain-English, no crash).
- `GEMINI_MODEL=gemini-2.5-flash` — optional; the code already defaults to this. Set for explicitness.

| Fact | Value |
|---|---|
| Region | **Asia Pacific (Mumbai) `ap-south-1`** |
| Instance | **`listing-app`** = `i-0add667d4cec224c6` (t3.micro, ap-south-1b) |
| Live unit file | `/etc/systemd/system/listing-app.service` |
| Repo source-of-truth | `aws/ec2/userdata.sh` |

---

## Concept: three places, don't conflate them

Editing the live box does **not** update the instance's stored user-data. Three distinct layers:

| Layer | What it is | This runbook changes it? |
|---|---|---|
| **A. `aws/ec2/userdata.sh` (repo)** | Template you paste when launching a fresh box | No — committed separately (`11b63f8`) |
| **B. Instance stored *User data* (EC2 console)** | Runs only on the instance's **first** boot; a stop/start does NOT re-run it | **No** — stays stale on this instance until re-provision |
| **C. Live `listing-app.service` on the box** | The file systemd uses right now | **Yes — this runbook rewrites C** |

**This is a live patch (hotfix) to layer C.** Like any patch it introduces drift: keep A in sync in
the repo (done), and remember that B on this instance is now stale but harmless — it only matters if
you rebuild the box, in which case launch it from the updated A. A plain `systemctl restart` (what the
CI deploy runs) reuses the existing C file, so C must be rewritten here, not just restarted.

---

## Steps (AWS Console → Session Manager)

### 1. Sign in with a privileged identity
Use your admin login — **not** the `Meta-ad-Banner` IAM user (it lacks EC2/SSM privileges). Same
identity used to create the SSM parameter.

### 2. Set the region
Top-right region selector → **Asia Pacific (Mumbai) `ap-south-1`**.

### 3. Open Session Manager
Search **Systems Manager** → left sidebar *Node Management* → **Session Manager** → **Start session**.

### 4. Pick the box
Under *Target instances* select **`listing-app`** (`i-0add667d4cec224c6`) → **Start session**.

> If the instance isn't listed, SSM doesn't see it as online yet. Wait ~30–60s after power-on and
> refresh — the SSM agent needs a moment after boot. (CI already deploys to it via SSM, so it should
> appear once online.)

### 5. Confirm the shell
A browser terminal opens as `ssm-user`:
```bash
whoami   # -> ssm-user
```

### 6. Rewrite the unit + restart
Paste this **entire** block and press Enter. It regenerates `listing-app.service` identically to the
committed `userdata.sh` (with the two Gemini `-e` flags), reloads, and restarts:

```bash
sudo bash <<'EOF'
set -euo pipefail
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
  -e AWS_REGION=$REGION \
  -e AWS_DEFAULT_REGION=$REGION \
  -e EXPLAIN_WITH_GEMINI=1 \
  -e GEMINI_MODEL=gemini-2.5-flash \
  $IMAGE
ExecStop=/usr/bin/docker stop listing-app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl restart listing-app.service
EOF
```

> Note: this restart pulls the **current** `:latest` (pre-merge code). That's fine — when `main` is
> pushed next, CI rebuilds `:latest` and restarts again; the unit already carries the env.

### 7. Verify
```bash
systemctl show listing-app.service -p ExecStart | grep -o 'EXPLAIN_WITH_GEMINI=1'
systemctl is-active listing-app.service
```
Expected:
```
EXPLAIN_WITH_GEMINI=1
active
```

---

## After this runbook

Merge `feat/fix-error-flow` → `main` and push → CI (`ci-cd.yml`) builds a new `:latest` and restarts
the service via SSM. The new fix-error code then runs on the box with Gemini enabled. Confirm live by
driving a rejection file through `/fix` and checking the explanations come back in plain English.

---

_Created 2026-07-07. Part 2 of the Fix-Error prod deploy; sibling of
`add-gemini-api-key-ssm.md`. See [[ec2-deploy-stage1-done]], [[fix-error-flow-build-status]]._
