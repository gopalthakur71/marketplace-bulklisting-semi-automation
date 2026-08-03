# AWS CLI Quick Reference (PowerShell)

This runbook summarizes the AWS CLI commands used to configure a profile,
verify the active account, start/stop the EC2 instance, change the security
group, and open the app in your browser.

> **Account** `048589483919` · **Region** `ap-south-1` (Mumbai)
> **Instance** `i-0add667d4cec224c6` (`listing-app`) · **Security group**
> `sg-06ceeb9a898378bb0` (`listing-app-sg`) · **Key pair** `listing-app` (→ `listing-app.pem`)

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

## 7. Start the EC2 instance

```powershell
aws ec2 start-instances --instance-ids i-0add667d4cec224c6
```

The public IP **changes on every stop/start** — grab the new one with step 8.

---

## 8. Check status / get the current public IP

```powershell
aws ec2 describe-instances --instance-ids i-0add667d4cec224c6 --query "Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]" --output table
```

Displays Instance ID, current state, and the public IP. Useful right after
starting.

---

## 9. Stop the EC2 instance

```powershell
aws ec2 stop-instances --instance-ids i-0add667d4cec224c6
```

Gracefully stops the instance. It can be started again later. **Stop it when
done** to keep the bill near zero.

---

## 10. Change the security group

The security group is `sg-06ceeb9a898378bb0`. You mainly need to touch it when
**your home/office public IP changes** and the SSH rule no longer matches (SSH
is what the browser tunnel in step 11 rides on).

### 10.1 — Update SSH to your current IP (copy-paste this whole block)

Paste all four lines **into one PowerShell window, in this order**. Do not skip
the first two — they are what create `$myip`.

```powershell
$env:AWS_PROFILE = "gt"
$myip = (Invoke-RestMethod https://checkip.amazonaws.com).Trim()
"My current IP is: $myip"
aws ec2 authorize-security-group-ingress --group-id sg-06ceeb9a898378bb0 --protocol tcp --port 22 --cidr "$myip/32"
```

Line 3 must print a real address, e.g. `My current IP is: 49.36.180.22`.

> **If line 3 prints `My current IP is:` with nothing after it — STOP.** The
> lookup failed, `$myip` is empty, and line 4 will fail with
> `CIDR block /32 is malformed`. That error means "you never set `$myip`", not
> that anything is wrong in AWS. Fix it by re-running line 2 in the *same*
> window (a new PowerShell window forgets `$myip`, and so does closing this one).

> `InvalidPermission.Duplicate` on line 4 just means that IP is already
> allowed — harmless, you're done.

### 10.2 — See which IPs are currently allowed

```powershell
aws ec2 describe-security-groups --group-ids sg-06ceeb9a898378bb0 --query 'SecurityGroups[].IpPermissions[?FromPort==`22`].IpRanges[].CidrIp' --output text
```

This prints only the port-22 (SSH) entries, one CIDR per line — for example:

```
49.36.180.22/32
49.36.174.9/32
```

(The `--query` part must stay in **single** quotes. In PowerShell a backtick
inside double quotes is an escape character and would silently mangle the
query.)

Want to see everything, not just SSH? Drop the filter:

```powershell
aws ec2 describe-security-groups --group-ids sg-06ceeb9a898378bb0 --query "SecurityGroups[].IpPermissions" --output json
```

### 10.3 — Remove an old IP you no longer use

There is **no variable** for the old IP — you must type the real value you saw
in 10.2, exactly as printed, `/32` included:

```powershell
aws ec2 revoke-security-group-ingress --group-id sg-06ceeb9a898378bb0 --protocol tcp --port 22 --cidr 49.36.174.9/32
```

> `49.36.174.9/32` above is only an **example**. If you paste a placeholder such
> as `OLD.IP.HERE/32`, AWS replies `CIDR block OLD.IP.HERE/32 is malformed` —
> it takes whatever you type literally.

**Order matters:** add the new IP (10.1) *before* revoking the old one. If you
revoke the rule you're currently connected through, your SSH tunnel drops.

### 10.4 — Close the public HTTP ports when you're done testing

The box has **no
TLS**, so it should not stay open to the world. Once you use the tunnel (step
11) you don't need port 80/8080 open at all:

```powershell
aws ec2 revoke-security-group-ingress --group-id sg-06ceeb9a898378bb0 --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 revoke-security-group-ingress --group-id sg-06ceeb9a898378bb0 --protocol tcp --port 8080 --cidr 0.0.0.0/0
```

---

## 11. Open the app in your browser

> **You cannot log in via `http://<public-ip>`.** Login goes through Cognito,
> and Cognito is configured to send you back to **`http://localhost:8000/auth/callback`**
> only. That URL is stable and does **not** change when the instance IP changes.
> So you must reach the app through an **SSH tunnel** that maps your local port
> 8000 → the box's port 80. Then Cognito's redirect lands back through the tunnel
> and login works.

**Steps:**

1. Make sure the instance is running (step 7) and note the current public IP
   (step 8). Make sure SSH is open to your IP (step 10.1).

2. Open the tunnel from PowerShell. Leave this window open the whole time:

   ```powershell
   ssh -i C:\path\to\listing-app.pem -L 8000:localhost:80 ec2-user@<PUBLIC_IP>
   ```

   Replace `C:\path\to\listing-app.pem` with wherever your key is saved, and
   `<PUBLIC_IP>` with the value from step 8.

3. In your browser, go to:

   ```
   http://localhost:8000
   ```

   You'll be redirected to the Cognito login page → sign in with
   `gopalthakur71@gmail.com` → you land on the dashboard.

**Notes:**

- Use `http://` (not `https://`) — there is no TLS on the box.
- If SSH says `Permission denied (publickey)`, the key path is wrong or it's
  not the `listing-app.pem` key.
- **Lost the `.pem`?** You can tunnel without any key using SSM Session Manager
  (needs the [Session Manager plugin] installed once):

  ```powershell
  aws ssm start-session --target i-0add667d4cec224c6 --document-name AWS-StartPortForwardingSession --parameters "portNumber=80,localPortNumber=8000"
  ```

  Then browse `http://localhost:8000` exactly as above. This needs no key and no
  open SSH port.

[Session Manager plugin]: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

---

## Typical workflow

1. Open PowerShell → `$env:AWS_PROFILE = "gt"`.
2. Start the instance (step 7) and note the new public IP (step 8).
3. If your IP changed, update the SSH rule (step 10.1).
4. Open the tunnel and browse `http://localhost:8000` (step 11).
5. Do your work.
6. Close the public ports if you opened any (step 10.4).
7. **Stop the instance (step 9)** to reduce cost.

---

## 12. Windows / PowerShell gotchas

Four things that have actually bitten, with the exact error each produces.

**`CIDR block /32 is malformed`** — `$myip` was never set. You skipped the lookup line, or
you are in a *new* PowerShell window (variables do not survive one). Re-run step 10.1's
block from the top. This is not an AWS problem.

**`CIDR block OLD.IP.HERE/32 is malformed`** — you pasted a placeholder. AWS takes the
text literally. Get the real value from step 10.2 and type that.

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
JMESPath filter no longer parses. Always single-quote a query containing backticks — see
step 10.2.

---

## 13. Deploy failed — reading the real reason

The GitHub Actions `deploy` job restarts the service over SSM. When it fails, read the error
in this order; each one narrows it down.

**`Process completed with exit code 1`** with `No running instance tagged Name=listing-app`
— the box is stopped. Start it (step 7) and re-run the failed job.

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
(the command id is printed in the job log):

```powershell
aws ssm get-command-invocation --command-id <CMD_ID> --instance-id i-0add667d4cec224c6 --query "{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}" --output json
```

If that only says *"See systemctl status … / journalctl -xeu …"*, systemd is pointing at the
journal. Fetch it — and check the disk in the same round trip, because that is the usual
culprit:

```powershell
$id = aws ssm send-command --instance-ids i-0add667d4cec224c6 --document-name AWS-RunShellScript --parameters 'commands=["journalctl -u listing-app.service --no-pager -n 80","echo ===DISK===","df -h /","echo ===DOCKER===","docker images"]' --query "Command.CommandId" --output text
Start-Sleep -Seconds 10
aws ssm get-command-invocation --command-id $id --instance-id i-0add667d4cec224c6 --query "StandardOutputContent" --output text
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
$id = aws ssm send-command --instance-ids i-0add667d4cec224c6 --document-name AWS-RunShellScript --parameters 'commands=["systemctl stop listing-app.service","docker image prune -f","df -h /","systemctl start listing-app.service","sleep 20","systemctl is-active listing-app.service","curl -s -o /dev/null -w \"%{http_code}\" http://localhost:80/"]' --query "Command.CommandId" --output text
Start-Sleep -Seconds 45
aws ssm get-command-invocation --command-id $id --instance-id i-0add667d4cec224c6 --query "{Status:Status,Out:StandardOutputContent}" --output json
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
