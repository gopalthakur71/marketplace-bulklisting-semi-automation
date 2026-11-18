# Public HTTPS URL via Cloudflare Tunnel — PARKED (not needed now)

> **Status (2026-11-18): not in use, nothing to set up.** The app is opened with an SSM port
> forward to `http://localhost:8000` (see
> [`infra-terraform-ansible.md`](infra-terraform-ansible.md)). That is enough for one operator
> using the app a few sessions a month. This page only records the option for the future.
>
> An earlier version of this page described a Cloudflare Tunnel in front of **Cognito**. Cognito
> was removed on 2026-11-17, so that version no longer applies and was rewritten.

## When would we want it?

Only if the app needs to be reached **without the AWS CLI**, for example:

- a team member without AWS credentials needs to use it, or
- it has to be opened from a phone or a machine without the Session Manager plugin.

Until then, the port forward is simpler and safer: zero inbound ports, AWS IAM is the gate,
and nothing is exposed to the internet.

## The one rule if we ever do it

**The app has no login.** Whoever reaches it can use it (generate sheets, write to S3, use
the Gemini key). So a public URL must never point at the app on its own. It needs a login
in front of it **at Cloudflare**, not in the app:

- **Cloudflare Tunnel**: a small `cloudflared` service on the box dials *out* to Cloudflare,
  so the security group can stay at zero inbound. Cloudflare provides HTTPS and a stable
  hostname (e.g. `listing.ijor.in`) even though the box's IP changes every build.
- **Cloudflare Access** (Zero Trust, free for up to 50 users): an access policy on that
  hostname, e.g. "allow these email addresses", so Cloudflare asks for a one-time email code
  before any request reaches the box.

Both are free, so the "pay nothing when idle" goal still holds.

## Rough plan (for whoever picks this up)

1. Cloudflare Zero Trust → create a tunnel `listing-app`, public hostname → `HTTP localhost:80`.
2. Cloudflare Access → an application for that hostname with an allow-list policy.
   **Do this before step 4**: never have the hostname live without Access in front.
3. Store the tunnel token as an SSM SecureString (e.g. `/marketplace-listing/cloudflare_tunnel_token`)
   in Terraform (`infra/terraform/ssm.tf`, value ignored, set by hand like the Gemini key).
4. Add a `cloudflared` role to **Ansible** (a systemd unit that runs `cloudflare/cloudflared`
   with host networking and reads the token from SSM at start). Never configure the box by hand.
5. Keep the security group at zero inbound; the tunnel needs none.
6. Record the decision as an ADR in `docs/decisions/`.

Note that the app container is bound to `127.0.0.1:80` on the box, which `cloudflared` with
host networking can reach, so no app change is needed.
