# AWS CLI Quick Reference (PowerShell)

This runbook summarizes the AWS CLI commands used to configure a profile, verify
the active account, and diagnose the server over SSM. **Turning the server on/off
and opening the app are Terraform's job now**: see
[`infra-terraform-ansible.md`](infra-terraform-ansible.md).

> **Account** `048589483919` · **Region** `ap-south-1` (Mumbai)
> **Instance** tag `Name=listing-app` (new id on every build: `terraform output -raw instance_id`)
> · **Security group** `sg-06ceeb9a898378bb0` (`listing-app-sg`, managed by Terraform) · **No SSH key**

---

## ⚠️ 0. Always select the `gt` profile first

Your machine has more than one profile. The **default** credentials are the
`Meta-ad-Banner` user, which is **not** allowed to touch EC2 — commands will
fail with `UnauthorizedOperation`. The profile that works is **`gt`** (IAM user
`gt71`). Set it once per PowerShell window:

```powershell
$env:AWS_PROFILE = "gt"
```

Verify you're on the right identity (should show `user/gt71`):

```powershell
aws sts get-caller-identity
```

Everything below assumes `gt` is selected. (Alternatively add `--profile gt` to
each command.)

---

## 1. List all configured AWS profiles

```powershell
aws configure list-profiles
```

Displays all AWS CLI profiles configured on your computer.

---

## 2. Check the currently selected profile

```powershell
echo $env:AWS_PROFILE
```

Shows which AWS profile is active for this PowerShell session.

---

## 3. Select an AWS profile

```powershell
$env:AWS_PROFILE = "gt"
```

Sets the active profile to `gt`. Only affects the current PowerShell window.

---

## 4. Verify the logged-in AWS account

```powershell
aws sts get-caller-identity
```

Confirms the credentials in use — Account ID, IAM user ARN, User ID.

---

## 5. List all EC2 instances

```powershell
aws ec2 describe-instances --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key=='Name']|[0].Value]" --output table
```

Displays Instance ID, current state, and Name tag.

---

## 6. Configure AWS CLI

```powershell
aws configure
```

Configures credentials and defaults for a profile (Access Key ID, Secret Access
Key, Default Region, Output Format).

---

## 7. The server — on, off, open (Terraform, not the CLI)

Since 2026-11-17 the EC2 server is **created and deleted by Terraform**, not started and
stopped. There is no fixed instance id and no SSH key. The full routine is in
[`infra-terraform-ansible.md`](infra-terraform-ansible.md); in short (Git Bash, `infra/terraform`):

```bash
terraform apply -var app_running=true     # build a fresh server, then run Ansible (see runbook)
terraform apply -var app_running=false    # delete it
terraform output -raw ssm_port_forward_command   # the command that opens the app on localhost:8000
```

For the CLI commands below you need the **current instance id**. In PowerShell, from
`infra/terraform`:

```powershell
$iid = terraform output -raw instance_id
```

> **Don't change the security group, the instance or IAM with the AWS CLI any more.** Terraform
> owns them, and a CLI change becomes drift that the next `terraform apply` silently reverts.
> Edit the `.tf` file instead. (The old SG/SSH steps that used to live here are gone: the server
> has no key, and access is the SSM port forward.)

---

## 12. Windows / PowerShell gotchas

Things that have actually bitten, with the exact error each produces.

**`'charmap' codec can't encode characters in position …`** — the command *succeeded*; only
printing it failed. Linux log output (especially `journalctl -x`, which draws `░░`) contains
characters your console's cp1252 codepage cannot render. Fix the console, not the command:

```powershell
chcp 65001 > $null
$env:PYTHONIOENCODING = "utf-8"
```

Or strip non-ASCII on the box before it reaches you: `… | tr -cd "\11\12\15\40-\176"`.

**A `--query` that contains backticks silently breaks in double quotes.** In PowerShell a
backtick is the escape character, so `"…FromPort==`22`…"` loses its backticks and the
JMESPath filter no longer parses. Always single-quote a query containing backticks.

---

## 13. Deploy failed — reading the real reason

The GitHub Actions `deploy` job restarts the service over SSM. When it fails, read the error
in this order; each one narrows it down.

**`Process completed with exit code 1`** with `No running instance tagged Name=listing-app`
— the server is off (the normal state). Nothing to fix: the next "on" pulls the new image.
If you want it live now, turn it on (step 7) and re-run the failed job.

**`Process completed with exit code 255`** — an `aws` call itself errored. Exit 255 is the
AWS CLI's own error code, which *rules out* the "no instance" case above (that exits 1).
Look at which echo lines appear before it:

| Last line in the log | Where it failed |
|---|---|
| no `Deploying …` | `describe-instances` — role/OIDC/region |
| `Deploying <sha> to i-…`, no `SSM command:` | `send-command` — usually the SSM agent has not registered yet on a freshly-started box; wait ~2 min |
| `SSM command: <id>` present | the command ran **on the box** and failed — see below |

**`Waiter CommandExecuted failed: … matched expected path: "Failed"`** — the restart itself
failed on the instance. GitHub, OIDC, ECR and SSM are all fine. Get the command's own output
(the command id is printed in the job log; `$iid` from step 7):

```powershell
aws ssm get-command-invocation --command-id <CMD_ID> --instance-id $iid --query "{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}" --output json
```

If that only says *"See systemctl status … / journalctl -xeu …"*, systemd is pointing at the
journal. Fetch it — and check the disk in the same round trip, because that is the usual
culprit:

```powershell
$id = aws ssm send-command --instance-ids $iid --document-name AWS-RunShellScript --parameters 'commands=["journalctl -u listing-app.service --no-pager -n 80","echo ===DISK===","df -h /","echo ===DOCKER===","docker images"]' --query "Command.CommandId" --output text
Start-Sleep -Seconds 10
aws ssm get-command-invocation --command-id $id --instance-id $iid --query "StandardOutputContent" --output text
```

### 13.1 `no space left on device` — the disk fills up (happened 2026-08-03)

The service unit re-pulls `:latest` on every start, and **each deploy leaves the previous
image behind untagged**. On the 8 GB root volume that accumulates until a pull cannot finish:

```
failed to register layer: … no space left on device
/dev/nvme0n1p1  8.0G  7.5G  494M  94% /
```

Note `Restart=on-failure` means systemd retries forever — the journal will show a restart
counter in the dozens, re-pulling ~540 MB each time. **Stop the service first**, or the loop
fights your cleanup:

```powershell
$id = aws ssm send-command --instance-ids $iid --document-name AWS-RunShellScript --parameters 'commands=["systemctl stop listing-app.service","docker image prune -f","df -h /","systemctl start listing-app.service","sleep 20","systemctl is-active listing-app.service","curl -s -o /dev/null -w \"%{http_code}\" http://localhost:80/"]' --query "Command.CommandId" --output text
Start-Sleep -Seconds 45
aws ssm get-command-invocation --command-id $id --instance-id $iid --query "{Status:Status,Out:StandardOutputContent}" --output json
```

`docker image prune -f` removes only **dangling** (untagged) images, so the tagged `:latest`
the app needs is never touched. On 2026-08-03 this reclaimed **4.59 GB**, taking the disk from
94% to 33%.

A healthy result is `active` and HTTP **302** — that is the Cognito login redirect, which is
what an unauthenticated request to `/` should return. To prove the *new* code is really live,
look inside the running container rather than trusting the tag:

```powershell
docker exec listing-app ls src/web/templates/
```
