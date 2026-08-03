# EC2 box configuration (`listing-app`) — what lives only on the instance

Everything here is **config drift by design**: it exists on the instance, not in the
container image, so it is *not* reproduced by a deploy. If the box is ever rebuilt or
replaced, these files must be recreated or the app will not run — and two of them are what
keep the 8 GB root volume from filling up.

> **Instance** `i-0add667d4cec224c6` (`listing-app`) · **Region** `ap-south-1` ·
> **Root volume** 8 GB (`/dev/nvme0n1p1`) · Amazon Linux 2023

Verify or reapply with SSM (see
[`aws-cli-powershell-quick-reference.md`](aws-cli-powershell-quick-reference.md) §13).

---

## 1. `/etc/systemd/system/listing-app.service`

The unit that runs the app. **`restart` == `deploy`**: it re-pulls `:latest` on every start,
which is why the CI/CD deploy job only has to send `systemctl restart listing-app`.

```ini
[Unit]
Description=Marketplace Listing App
After=docker.service
Requires=docker.service

[Service]
ExecStartPre=-/usr/bin/docker rm -f listing-app
ExecStartPre=/bin/bash -lc 'aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 048589483919.dkr.ecr.ap-south-1.amazonaws.com'
ExecStartPre=/usr/bin/docker pull 048589483919.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStart=/usr/bin/docker run --rm --name listing-app -p 80:8080   -e AWS_REGION=ap-south-1   -e AWS_DEFAULT_REGION=ap-south-1   -e EXPLAIN_WITH_GEMINI=1   -e GEMINI_MODEL=gemini-2.5-flash   048589483919.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStop=/usr/bin/docker stop listing-app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Things worth knowing about it:

- **`Restart=on-failure` retries forever.** When the 2026-08-03 disk-full failure hit, the
  restart counter reached 68 — each round re-pulling ~540 MB. When debugging a start
  failure, `systemctl stop listing-app` **first**, or the loop fights you.
- **`--rm`** means the container's json-file log is deleted on every restart, which caps log
  growth between deploys as a side effect.
- All app config comes from **SSM Parameter Store** via the instance role — only these four
  env vars are set here.

## 2. `/etc/docker/daemon.json` — container log rotation

Added 2026-08-03. Docker's `json-file` driver is **unbounded by default**; this caps it.

```json
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
```

Requires `systemctl restart docker`, which **kills the app container** — restart
`listing-app` afterwards and verify.

> ⚠️ **This file must be valid JSON or the Docker daemon will not start at all**, taking the
> app down with it. That happened while applying it: an unquoted `echo` in a remote shell
> brace-expanded the JSON and stripped its quotes, writing
> `log-driver:json-file log-opts:max-size:10m …`, and `dockerd` refused to boot. Always
> single-quote the JSON in the shell, and validate before restarting:
>
> ```bash
> echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
> python3 -c "import json;json.load(open('/etc/docker/daemon.json'))" || rm -f /etc/docker/daemon.json
> systemctl restart docker
> ```
>
> Removing the file entirely is a safe fallback — Docker starts with its defaults.

Confirm it reached the container (the daemon default alone is not proof):

```bash
docker inspect -f '{{json .HostConfig.LogConfig}}' listing-app
# {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}}
```

## 3. `/etc/systemd/journald.conf` — journal cap

Added 2026-08-03. Journald **already self-caps** at 10% of the filesystem (~800 MB here),
so this is belt-and-braces rather than a fix for anything that was broken; it was using
83 MB when measured.

```ini
[Journal]
SystemMaxUse=200M
MaxRetentionSec=1month
```

Then `systemctl restart systemd-journald` and `journalctl --vacuum-size=200M`. The original
file is backed up alongside as `journald.conf.bak`.

---

## Disk budget on the 8 GB volume

| | |
|---|---|
| OS, Docker, packages | ~2 GB |
| Current tagged image | ~540 MB |
| Previous image, pruned at the next deploy | ~540 MB |
| Journal | ≤200 MB |
| Container logs | ≤30 MB |
| **Steady state** | **~3.5 GB / 44%** |

**The one thing that is not capped by any of the above is old Docker images**, and that is
what actually filled the disk on 2026-08-03 — 15 accumulated images, 94% full, deploys
failing with `no space left on device`. That is fixed in git, not on the box: the CI/CD
deploy job now runs `docker image prune -f` before the restart
(`.github/workflows/ci-cd.yml`), so each deploy clears the previous one's leftover.

Pruning happens *before* the restart on purpose: at that moment the running image is still
tagged, so `-f` (dangling only) provably cannot remove it, and the previous image survives
until the next deploy as a local rollback option.

## If the box is rebuilt

Recreate items 1–3 above, then confirm:

```bash
systemctl is-active listing-app        # active
curl -s -o /dev/null -w '%{http_code}' http://localhost:80/   # 302 = Cognito redirect
docker inspect -f '{{json .HostConfig.LogConfig}}' listing-app
df -h /
```

A **302** is the healthy answer for an unauthenticated request — it is the Cognito login
redirect, not an error.
