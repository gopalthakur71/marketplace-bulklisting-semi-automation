# Runbook — Cognito Authentication Setup via the **Console**

Same outcome as the CLI runbook, done through the AWS web console. This sets up user authentication for the Marigold Ops web app. Do the steps in order.

**Values used throughout (don't substitute — these are this project's):**

| Thing | Value |
|---|---|
| AWS account id | `048589483919` |
| Region (Cognito) | **Asia Pacific (Mumbai) `ap-south-1`** |
| User pool name | `marketplace-listing-pool` |
| Sign-in method | Email |
| App client name | `marketplace-listing-web` |
| Client type | **Confidential** (has a client secret) |
| Callback URL (dev) | `http://localhost:8000/auth/callback` |
| Sign-out URL (dev) | `http://localhost:8000/` |
| Cognito domain prefix | `ijor-marketplace` |
| Test user email | `gopalthakur71@gmail.com` |

> Sign in with an IAM user that has Cognito admin permissions.

> **Console UI note (redesigned console, 2026).** AWS reworked the Cognito console.
> Key differences from older guides:
> - After you create a pool you may land on a **"Set up resources for your application" /
>   Quick setup guide** page offering sample Flask/authlib code — **ignore and skip it**;
>   our app is FastAPI and already implements JWT verification.
> - The app client now lives under **Applications → App clients** (not "Integrations →
>   App clients and analytics"), and the create-pool wizard often **auto-creates an app
>   client** for you.
> - The Cognito domain now lives under **Branding → Domain**.
> - Callback/sign-out URLs, identity providers, grant types and scopes are configured on
>   the **app client → Login pages tab → Managed login pages configuration**, and that
>   section only fully appears **after a Cognito domain exists** (so if the callback
>   fields are missing, do step 3 first, then come back to step 2).
> - "Confidential client" is now called **Traditional web application** in the app-type picker.
>
> The values below (account, region, pool ID `ap-south-1_NdxNQ1plz`, client ID
> `29oo5dtqh8j50k2481lmffqb0e`, domain `ijor-marketplace`) reflect the actual setup
> completed 2026-06-30.

---

## 1. Create the user pool

1. Set the **region selector** (top-right of the console) to **Asia Pacific (Mumbai)
   ap-south-1**. Cognito pools are regional.
2. Open **Amazon Cognito** → **User pools** → **Create user pool**.
3. Sign-in experience:
   - Cognito user name: **uncheck** (use email, not username).
   - Email: **check**.
   - No phone number.
   - **Next**.
4. Password policy: accept defaults → **Next**.
5. Multi-factor authentication: **No MFA** → **Next**.
6. User account recovery: accept defaults → **Next**.
7. Email provider: leave as default (Cognito will send) → **Next**.
8. User pool name: `marketplace-listing-pool`.
9. Do NOT enable self-registration (users are invited or created by admin).
10. **Create user pool**.

Wait for the pool to finish creating (a few seconds).

---

## 2. App client (confidential / "Traditional web application") + callback URLs

In the redesigned console the app client is often **created automatically** with the pool,
and its OAuth settings (callback URLs, grants, scopes) are edited afterward on the
**Login pages** tab — which only appears once a domain exists. So the practical order is:
check/create the client here, then do step 3 (domain), then come back to set the URLs.

**2a. Find or create the app client**

1. Open the pool → left nav **Applications → App clients**.
2. If a client already exists (auto-created with the pool), click it and skip to 2b.
3. Otherwise **Create app client**:
   - Application type: **Traditional web application** (the confidential, server-side
     type that gets a client secret — equivalent to the old "Confidential client").
   - App client name: `marketplace-listing-web`.
   - Client secret: keep the auto-generated default.
   - Create.
4. On the app client page, copy the **Client ID** and reveal/copy the **Client secret**
   (**Show client secret**) — you'll need both in step 5.

**2b. Set the OAuth / callback settings** *(do this after step 3 if the section is missing)*

1. App client page → **Login pages** tab → **Managed login pages configuration** → **Edit**.
2. **Allowed callback URLs:**
   ```
   http://localhost:8000/auth/callback
   ```
3. **Allowed sign-out URLs:**
   ```
   http://localhost:8000/
   ```
4. **Identity providers:** select **Cognito user pool** (required, or the hosted login won't work).
5. **OAuth 2.0 grant types:** **Authorization code grant** only.
6. **OpenID Connect scopes:** `openid`, `email`, `profile` (`phone` is harmless if already set;
   the app only needs `openid`+`email`).
7. **Save changes.**

---

## 3. Create the Cognito domain

1. On the user pool → left nav **Branding → Domain** → **Create Cognito domain**.
2. Cognito domain prefix: `ijor-marketplace`.
   - This creates the login URL:
     `https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/`
3. **Create Cognito domain**.

Wait for the domain to be **Active** (a few seconds).

> Now that the domain exists, **go back to step 2b** to set the callback/sign-out URLs on
> the app client's Login pages tab if you couldn't earlier.

---

## 4. Create a test user

1. On the user pool → left nav **User management → Users** → **Create user**.
2. Username: `gopalthakur71` (or another email).
3. Email address: `gopalthakur71@gmail.com` (or your test email).
4. Mark email as verified: **check**.
5. Temporary password: enter a strong password (you'll change it on first login, or set a permanent one here).
   - To skip the temp password, toggle **Send an invitation email** off and set a **Permanent password** instead.
6. **Create user**.

---

## 5. Collect values and set environment variables

From Cognito, gather:

1. **COGNITO_POOL_ID**: on the user pool → **General settings** → **Pool ID** (format:
   `ap-south-1_xxxxxxxxx`).
2. **COGNITO_CLIENT_ID**: from step 2 → copy the **Client ID**.
3. **COGNITO_CLIENT_SECRET**: from step 2 → copy the **Client secret**.
4. **COGNITO_DOMAIN**: `ijor-marketplace` (the prefix you set in step 3; the full domain is
   `ijor-marketplace.auth.ap-south-1.amazoncognito.com`).
5. **COGNITO_REDIRECT_URI**: `http://localhost:8000/auth/callback`.

Set these in your `.env` file (or shell) for a local dev run:

```bash
export COGNITO_POOL_ID="ap-south-1_xxxxxxxxx"
export COGNITO_CLIENT_ID="<your-client-id>"
export COGNITO_CLIENT_SECRET="<your-client-secret>"
export COGNITO_DOMAIN="ijor-marketplace"
export COGNITO_REDIRECT_URI="http://localhost:8000/auth/callback"
unset AUTH_DISABLED
```

**Why unset AUTH_DISABLED?**

- `AUTH_DISABLED=1` enables a dev bypass: the app skips Cognito entirely and logs in a synthetic
  user `dev@local`. Unset it (or set to `0`) to enforce real Cognito authentication.
- The app's settings loader (`src/web/settings.py`) reads `AUTH_DISABLED` from the environment and
  falls back to Cognito if unset.

---

## 6. Verify the setup

> **Important — what works today vs. what is deferred:**
>
> The `/auth/callback` OAuth callback route (which exchanges the authorization code for tokens and
> sets the `id_token` cookie) is **not yet built**. It is deferred to the deploy phase. Until that
> route exists, there is no in-app way to obtain a token via the Cognito hosted-UI round-trip.
>
> **For local development now:** use `AUTH_DISABLED=1`. The app will accept a synthetic
> `dev@local` user and skip all JWT checks — no Cognito interaction needed.
>
> **When the callback route lands (deploy phase):** the JWT-validation path in
> `src/web/auth.py` will be exercised in production once the `/auth/callback` route is added and
> the app is deployed behind the configured callback URL.

To smoke-test the Cognito pool configuration itself (pool exists, client is correct, domain is
active) without the callback route:

1. Run the web app with `AUTH_DISABLED=1`:

   ```bash
   AUTH_DISABLED=1 uvicorn src.web.main:app --reload
   ```

2. Open `http://localhost:8000/` — you should see the Marigold Ops dashboard logged in as
   `dev@local`. This confirms the app starts and static assets load.

3. To manually verify Cognito is reachable, open the hosted-UI login URL directly in a browser:
   `https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/login?client_id=<CLIENT_ID>&response_type=code&redirect_uri=http://localhost:8000/auth/callback`

   You should see the Cognito login page. Sign-in will attempt to redirect to
   `/auth/callback` — that redirect will 404 until the callback route is implemented.

If you see Cognito pool/client errors, check that:

- `COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`, and `COGNITO_CLIENT_SECRET` are set correctly.
- The callback URL in step 2 matches exactly (`http://localhost:8000/auth/callback`).

---

## 7. Teardown (only if you need to undo this)

- **App client**: on the user pool → **Applications → App clients** → select the app →
  **Delete app client**.
- **Cognito domain**: on the user pool → **Branding → Domain** → **Delete Cognito domain**.
- **User pool**: on Cognito → **User pools** → select the pool → **Delete user pool**
  (this also deletes all users and app clients in it).

---

## Port note — local (8000) vs. container (8080)

| Context | Port | How it starts |
|---|---|---|
| Local `uvicorn` (plain) | **8000** | `uvicorn src.web.main:app` uses uvicorn's default |
| Docker container | **8080** | `CMD ["uvicorn", "...", "--port", "8080"]` in `Dockerfile` |

The callback and sign-out URLs in this runbook (`http://localhost:8000/auth/callback`,
`http://localhost:8000/`) are correct for **local uvicorn** runs.

When the app is deployed in a container (or behind a load-balancer/reverse-proxy), the callback
URL registered in Cognito and the `COGNITO_REDIRECT_URI` environment variable **must match the
host and port the container is actually reachable on** — typically port 8080 internally, or a
standard 443 HTTPS URL externally. A mismatch between the registered callback URL and
`COGNITO_REDIRECT_URI` will cause Cognito to reject the redirect with an `invalid_redirect_uri`
error.

## Next: Production Callback URL

When deploying to production, add the production callback and sign-out URLs to the app client:

1. App client → **Edit** → edit the **Allowed callback URLs** and **Allowed sign-out URLs** to
   include both dev and prod (e.g., `https://marigold-ops.example.com/auth/callback`).
2. Set `COGNITO_REDIRECT_URI` in production to the prod URL.
3. Cognito serves both URLs; the client sends whichever matches the deployment.
