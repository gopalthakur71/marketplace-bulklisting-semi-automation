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

## 2. Create the app client (confidential, OAuth2)

1. Open the new `marketplace-listing-pool` → **Integrations** → **App integration** →
   **App clients and analytics**.
2. **Create app client**.
3. App type: **Confidential client** (required for OAuth2 code grant with a secret).
4. App client name: `marketplace-listing-web`.
5. Client secret: Cognito will auto-generate (keep the default).
6. **Next**.
7. Allowed callback URLs: paste exactly (one per line):
   ```
   http://localhost:8000/auth/callback
   ```
8. Allowed sign-out URLs:
   ```
   http://localhost:8000/
   ```
9. **Next**.
10. Grant types: uncheck all except **Authorization code** (for web apps).
11. **Create app client**.

Copy and save the **Client ID** and **Client secret** from the success screen — you'll need them in step 5.

---

## 3. Create the Cognito domain

1. On the user pool → **Integrations** → **Domain** → **Create Cognito domain**.
2. Cognito domain prefix: `ijor-marketplace`.
   - This creates the login URL:
     `https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/`
3. **Create Cognito domain**.

Wait for the domain to be **Active** (a few seconds).

---

## 4. Create a test user

1. On the user pool → **Users** → **Create user**.
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

1. Run the web app:

   ```bash
   uvicorn src.web.main:app --reload
   ```

2. Open `http://localhost:8000/` in a browser.
3. You should be **redirected to the Cognito hosted login page** (at
   `https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/login`).
4. Sign in with the test user email and password from step 4.
5. Cognito redirects back to `http://localhost:8000/auth/callback`, sets the `id_token` cookie,
   and redirects to the home page.
6. You should now see the Marigold Ops dashboard.

If instead you see a 401 "login required" error, check that:

- The callback URL in step 2 matches exactly (`http://localhost:8000/auth/callback`).
- `COGNITO_POOL_ID`, `COGNITO_CLIENT_ID`, and `COGNITO_CLIENT_SECRET` are set correctly.
- `AUTH_DISABLED` is **not** set to `1`.

---

## 7. Teardown (only if you need to undo this)

- **App client**: on the user pool → **Integrations** → **App clients and analytics** → select
  the app → **Delete app client**.
- **Cognito domain**: on the user pool → **Integrations** → **Domain** → **Delete Cognito domain**.
- **User pool**: on Cognito → **User pools** → select `marketplace-listing-pool` → **Delete user
  pool** (this also deletes all users and app clients in it).

---

## Next: Production Callback URL

When deploying to production, add the production callback and sign-out URLs to the app client:

1. App client → **Edit** → edit the **Allowed callback URLs** and **Allowed sign-out URLs** to
   include both dev and prod (e.g., `https://marigold-ops.example.com/auth/callback`).
2. Set `COGNITO_REDIRECT_URI` in production to the prod URL.
3. Cognito serves both URLs; the client sends whichever matches the deployment.
