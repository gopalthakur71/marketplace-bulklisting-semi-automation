# Runbook — run the app with Terraform + Ansible

> How to turn the listing-app server **on**, use it, and turn it **off**, plus one-time laptop
> setup and troubleshooting. Resource map: [`../infra-resources.md`](../infra-resources.md).
> Why it works this way: [`../decisions/2026-11-17-terraform-ansible-disposable-server.md`](../decisions/2026-11-17-terraform-ansible-disposable-server.md).

**The idea:** the server doesn't exist while you're not using it, so it costs nothing. Turning
it on builds a brand-new one from code; turning it off deletes it. Your data is never on the
server (it's in S3 and SSM), so nothing is lost.

| Tool | Runs in | Folder |
|---|---|---|
| Terraform (creates/deletes AWS resources) | **Git Bash** on Windows | `infra/terraform` |
| Ansible (sets up the server) | **WSL** (Ubuntu) | `infra/ansible` |

Both use the AWS profile **`gt`**. No AWS command needs the console.

---

## Daily use

### 1. Turn it on (~1 min) — Git Bash
```bash
cd "/e/Myntra Bulk listing Automation/infra/terraform"
terraform apply -var app_running=true
```
Check that the plan says **`1 to add, 0 to change, 0 to destroy`** (the server), then type `yes`.
If you forget `-var`, Terraform asks for `app_running`: type `true`.

### 2. Set it up (~3–6 min on a fresh box) — WSL
Wait about **2 minutes** after step 1 so the new box can register with SSM. Then:
```bash
cd "/mnt/e/Myntra Bulk listing Automation/infra/ansible"
export ANSIBLE_CONFIG=$PWD/ansible.cfg
ansible-playbook site.yml
```
It should end with **`failed=0 unreachable=0`**. The last task, *Health check*, retries every
10 s while the box downloads the ~540 MB app image; several "FAILED - RETRYING" lines are normal.
It passes on HTTP `302` (the login redirect) while Cognito is on.

### 3. Open the app — Git Bash
```bash
terraform output -raw ssm_port_forward_command   # prints the command below with the right id
aws ssm start-session --target <instance-id> --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=8000 --profile gt
```
Leave that window open and browse **http://localhost:8000**. Closing it (Ctrl+C) closes the tunnel.

### 4. Turn it off — Git Bash
```bash
terraform apply -var app_running=false
```
Check that the plan says **`1 to destroy`** and that the one resource is **`aws_instance.server[0]`**,
then type `yes`. Nothing else is touched. Confirm with `terraform output`: `instance_id` is gone.

> **Never run `terraform destroy`.** It targets *everything* (data bucket, IAM, ECR, SSM). The
> data bucket's `prevent_destroy` makes it refuse, but it's the wrong tool. Use
> `app_running=false`.

### Deploys while you work
A push to `main` still deploys automatically **if the server is on**: CI restarts the app over
SSM and it re-pulls `:latest`. While it's off, CI's `deploy` job fails (nothing to restart).
That's harmless: the next "on" pulls the newest image anyway.

---

## Changing the server's setup

Edit Ansible, never the box. Anything changed by hand on the server disappears at the next
rebuild, and Ansible overwrites managed files on the next run.

| To change… | Edit | Then |
|---|---|---|
| App env vars (Gemini model, `AUTH_DISABLED`, …) | `infra/ansible/group_vars/all.yml` | `ansible-playbook site.yml` (restarts the app) |
| The systemd unit | `infra/ansible/roles/listing_app/templates/listing-app.service.j2` | same |
| Docker log rotation | `infra/ansible/roles/docker/files/daemon.json` | same (validated before writing, so invalid JSON can't stop Docker) |
| Journal cap | `infra/ansible/roles/journald/files/size.conf` | same |
| Instance type, disk, network, IAM, ECR, SSM, S3 settings | `infra/terraform/*.tf` | `terraform plan`, review, `terraform apply` |

**Terraform rule:** read every plan before `yes`. If a plan shows `destroy` or `replace` on
anything other than `aws_instance.server[0]`, stop and find out why.

---

## One-time laptop setup

### Git Bash (Windows)
- Terraform ≥ 1.10 (`terraform version`).
- AWS CLI v2 with profile `gt` (`aws sts get-caller-identity --profile gt`).
- **Session Manager plugin for Windows**, needed for step 3 (the port forward). Install from AWS's
  "Install the Session Manager plugin for the AWS CLI" page, then check with
  `session-manager-plugin --version`.
- First time in a fresh clone: `cd infra/terraform && terraform init`.

### WSL (Ubuntu)
```bash
apt update && apt install -y python3-boto3
curl -o /tmp/ssm.deb https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb && dpkg -i /tmp/ssm.deb
cd "/mnt/e/Myntra Bulk listing Automation/infra/ansible"
ansible-galaxy collection install -r requirements.yml
```
Also needed: AWS profile `gt` in WSL's own `~/.aws` (separate from Windows'), and Ansible
(Ubuntu 24.04 ships ansible-core 2.16).

**Why `community.aws` and not `amazon.aws` for the SSM connection:** the `aws_ssm` plugin only
moved into `amazon.aws` in 10.0, which needs ansible-core ≥ 2.17. With 2.16 it's
`community.aws.aws_ssm` 9.x (pinned in `requirements.yml`).

**Why `export ANSIBLE_CONFIG`:** Ansible ignores an `ansible.cfg` sitting in a world-writable
directory, and WSL mounts `/mnt/e` world-writable. Without the export, the inventory and roles
aren't found.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Terraform: `dial tcp: lookup …amazonaws.com: no such host` | Your laptop's DNS, not AWS. Just retry. Recurring? Wi-Fi's **IPv6** DNS may still point at the router; set it too (admin PowerShell): `Set-DnsClientServerAddress -InterfaceAlias "Wi-Fi" -ServerAddresses ("1.1.1.1","8.8.8.8","2606:4700:4700::1111","2001:4860:4860::8888")`. |
| Terraform: `Failed to save state` → `errored.tfstate` written | Usually the DNS issue above. **Don't run apply again.** Run `terraform state push errored.tfstate`, then `terraform plan` (expect no changes), then `rm errored.tfstate` (it contains secrets). |
| Ansible: `the connection plugin '…aws_ssm' was not found` | Collections missing or wrong version: re-run `ansible-galaxy collection install -r requirements.yml`. |
| `ansible-inventory --graph` shows no host | The box isn't running yet, or hasn't registered with SSM (wait ~2 min after `apply`). |
| Ansible task `UNREACHABLE … timeout` | An SSM command ran past `ansible_aws_ssm_timeout` (120 s). Re-run the playbook; finished steps are skipped. |
| Health check fails, `Connection refused` | The app isn't up. Read its logs over SSM: `ansible listing_app -b -m shell -a "systemctl status listing-app --no-pager \| head -15; journalctl -u listing-app --no-pager -n 40; docker ps -a; df -h /; free -m"` |
| Unit stuck in `activating (start-pre)` and the pull keeps restarting | systemd's start timeout killed the image pull. The unit sets `TimeoutStartSec=600` for exactly this; if it recurs, the box is too slow or the image grew. |
