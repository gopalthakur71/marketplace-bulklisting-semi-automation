# EC2 box configuration (`listing-app`) — what Ansible sets up on the server, and why

> **Since 2026-11-17 none of this is done by hand.** Ansible ([`infra/ansible/`](../../infra/ansible/))
> writes every file below onto each freshly built server, and the server itself is created and
> deleted on demand by Terraform. **Don't edit these files on the box**: the next
> `ansible-playbook site.yml` overwrites them and the next rebuild discards them. Change the Ansible
> source instead (paths below), then re-run the playbook.
>
> This page explains **what** the setup is and **why**: the incidents behind each setting.
> How to run it: [`infra-terraform-ansible.md`](infra-terraform-ansible.md).

> **Server** tag `Name=listing-app` (new instance id on every build) · **Region** `ap-south-1` ·
> Amazon Linux 2023 · **Root volume** 8 GB gp3, encrypted

---

## 1. `/etc/systemd/system/listing-app.service`

**Source:** `infra/ansible/roles/listing_app/templates/listing-app.service.j2` (env vars from
`infra/ansible/group_vars/all.yml`).

The unit that runs the app. **`restart` == `deploy`**: it re-pulls `:latest` on every start,
which is why the CI/CD deploy job only has to send `systemctl restart listing-app`. As rendered:

```ini
[Unit]
Description=Marketplace Listing App
After=docker.service
Requires=docker.service

[Service]
TimeoutStartSec=600
ExecStartPre=-/usr/bin/docker rm -f listing-app
ExecStartPre=/bin/bash -lc 'aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 048589483919.dkr.ecr.ap-south-1.amazonaws.com'
ExecStartPre=/usr/bin/docker pull 048589483919.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStart=/usr/bin/docker run --rm --name listing-app -p 127.0.0.1:80:8080 \
  -e AWS_REGION=ap-south-1 \
  -e AWS_DEFAULT_REGION=ap-south-1 \
  -e EXPLAIN_WITH_GEMINI=1 \
  -e GEMINI_MODEL=gemini-2.5-flash \
  048589483919.dkr.ecr.ap-south-1.amazonaws.com/marketplace-bulklisting:latest
ExecStop=/usr/bin/docker stop listing-app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

The app has no login (Cognito was removed in phase 5), so there is no auth setting in the unit.

Things worth knowing about it:

- **`-p 127.0.0.1:80:8080`**: the app answers only on the box's own loopback. The SSM port
  forward and the health check both connect from there, so nothing else (not even a
  mistakenly added security group rule) can reach a login-less app.

- **`TimeoutStartSec=600`** (added 2026-11-17). systemd's default 90 s start timeout covers the
  `ExecStartPre` steps, including the image pull. On a **fresh** server nothing is cached, and
  pulling and unpacking ~540 MB on the old t3.micro took longer than 90 s, so systemd killed the pull
  and `Restart=on-failure` looped it forever. It never showed on the old long-lived box because
  its image was cached. Found by the first rebuild from code.
- **`Restart=on-failure` retries forever.** When the 2026-08-03 disk-full failure hit, the
  restart counter reached 68, each round re-pulling ~540 MB. When debugging a start failure,
  `systemctl stop listing-app` **first**, or the loop fights you.
- **`--rm`** means the container's json-file log is deleted on every restart, which caps log
  growth between deploys as a side effect.
- All app config comes from **SSM Parameter Store** via the instance role; only these env vars
  are set here.

## 2. `/etc/docker/daemon.json` — container log rotation

**Source:** `infra/ansible/roles/docker/files/daemon.json`. Added 2026-08-03. Docker's
`json-file` driver is **unbounded by default**; this caps it:

```json
{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}
```

> ⚠️ **This file must be valid JSON or the Docker daemon will not start at all**, taking the app
> down with it. That happened on 2026-08-03 when it was applied by hand: an unquoted `echo` in a
> remote shell stripped the JSON's quotes and `dockerd` refused to boot. **Ansible now validates
> it before writing** (`validate: python3 -c "import json,sys; json.load(open(sys.argv[1]))" %s`),
> so a broken file can't reach the server. Restarting Docker kills the app container, so the
> playbook's handler restarts `listing-app` right after.

Confirm it reached the container (the daemon default alone is not proof):

```bash
docker inspect -f '{{json .HostConfig.LogConfig}}' listing-app
# {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}}
```

## 3. `/etc/systemd/journald.conf.d/size.conf` — journal cap

**Source:** `infra/ansible/roles/journald/files/size.conf`. A drop-in, so the distro's own
`journald.conf` stays untouched (the hand-applied version edited that file directly). Journald
**already self-caps** at 10% of the filesystem (~800 MB here), so this is belt-and-braces:

```ini
[Journal]
SystemMaxUse=200M
MaxRetentionSec=1month
```

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

**The one thing not capped by any of the above is old Docker images**, and that is what actually
filled the disk on 2026-08-03: 15 accumulated images, 94% full, deploys failing with `no space
left on device`. That's fixed in git: the CI/CD deploy job runs `docker image prune -f` before
the restart (`.github/workflows/ci-cd.yml`), so each deploy clears the previous one's leftover.
Since the server is now rebuilt fresh each time it's turned on, images rarely pile up anyway.

Pruning happens *before* the restart on purpose: at that moment the running image is still
tagged, so `-f` (dangling only) provably cannot remove it, and the previous image survives until
the next deploy as a local rollback option.

## Checking a server

The playbook's last task is the health check, so `failed=0` already proves the app is up. To look
by hand (WSL, `infra/ansible`):

```bash
ansible listing_app -b -m shell -a "systemctl is-active listing-app; curl -s -o /dev/null -w '%{http_code}' http://localhost:80/; echo; df -h /"
```

(Leave the `docker inspect` check out of ad-hoc Ansible commands: Ansible would try to treat
its `{{json …}}` as a template. Run it in an SSM shell instead: `aws ssm start-session --target
<id> --profile gt`, then `sudo docker inspect …`.)

`active` and **200** are healthy (the home page; there is no login since phase 5).
