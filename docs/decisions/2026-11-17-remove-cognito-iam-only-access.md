# Decision: remove the Cognito login, AWS IAM is the only gate

**Date:** 2026-11-17
**Status:** Accepted and done (2026-11-17). Step A (unlock, drop the SSM params) was applied;
step B deletes `infra/terraform/cognito.tf`.
**Design spec:** [`../superpowers/specs/2026-11-13-terraform-ansible-iac-design.md`](../superpowers/specs/2026-11-13-terraform-ansible-iac-design.md), phase 5
**Follows:** [`2026-11-17-terraform-ansible-disposable-server.md`](2026-11-17-terraform-ansible-disposable-server.md)

---

## The problem

The app had its own login: a Cognito hosted UI, an OAuth callback, and a JWT check on every
request. That made sense when the plan was a public URL. Since the move to IaC, though:

- The server is **never exposed to the internet**. The security group allows nothing public, and
  the only way in is an **SSM port forward**, which needs the owner's AWS credentials (the `gt`
  profile).
- There is **one user**, the owner.

So Cognito had become a second lock on the same door. It still cost things: a client secret in
SSM and in Terraform state, 5 SSM parameters, ~180 lines of auth code plus three test files, the
`python-jose` dependency, an 8-hour re-login, and a class of bugs it had already caused (the
`at_hash` login loop, a stray `\n` in the redirect URI).

## The decision

- **The app has no login.** `get_user()` returns a fixed `OPERATOR` user, and `/login`,
  `/logout` and `/auth/callback` are gone (they return 404).
- **The code is deleted, not left dormant.** `src/web/auth.py`, `oauth.py`,
  `routers/auth_routes.py`, their tests, the `cognito_*` / `auth_disabled` / `cookie_secure`
  settings and `python-jose` were all removed. The `AUTH_DISABLED` flag went too, because there's
  nothing left for it to switch. If a public URL ever needs a login, git history has the old
  code, but a new pool would be needed anyway.
- **Phase 4's network lockdown ships in the same change** (raised by a security review): the
  security group's last two inbound rules (80 and 22 from the laptop IP) are deleted, and the
  container binds to `127.0.0.1:80`. Without that, whoever next held the laptop's old ISP IP
  could reach a login-less app.
- **Ansible** no longer passes any auth flag, and its health check expects `200` instead of the
  old `302` login redirect.
- **Terraform** deletes the pool, client, domain and the 5 `cognito_*` SSM parameters. The pool
  has AWS-side `deletion_protection`, so this takes two applies: step A switches it to `INACTIVE`
  and drops `prevent_destroy`, then step B deletes `cognito.tf` and its import blocks.

## Consequences

- Anyone with AWS access to start an SSM session on the box can use the app. That's the owner
  only, which is the intended boundary. **Never open the security group to the internet**: with
  no login, the port forward is the only lock.
- **Rollout order matters.** Push the code first, so CI builds a `:latest` image with no login.
  Turning the server on with an older image would fail the `200` health check, because the old
  image redirects to a Cognito pool that no longer exists.
- The Terraform state no longer holds the Cognito client secret.
