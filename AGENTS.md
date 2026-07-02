# AGENTS.md — orientation for any coding agent or developer

> **Read this first.** This is the agent-neutral entry point for the repo. It works for any
> tool (Claude Code, Cursor, Copilot, Aider, Codex, plain humans). Everything an assistant
> needs to understand and safely change this codebase is in the repo itself — **do not rely
> on any tool-specific memory** (e.g. Claude Code's `~/.claude` memory is *not* part of the
> repo and other agents can't see it; the in-repo docs below are the single source of truth).

## What this project is

**Myntra Bulk-Listing Automation** turns a Shopify product export into a validated Myntra
bulk-upload spreadsheet, hosts the product images on S3, and (Phase 2) wraps that pipeline in
a small FastAPI web app so non-technical staff can run it. It's an internal tool for **Ijor**
(an ethnic-wear brand). A secondary, explicit goal is to **showcase cloud / CI-CD skills** —
which is why the deploy/AWS machinery is richer than a one-off script would need.

## The four layers (what lives where)

| Layer | Path | Does |
|---|---|---|
| 1. Core fill pipeline | `src/core/` + `src/myntra/` + `config/myntra/` | Shopify CSV → mapped/validated Myntra `.xlsx` + images → S3. Entry: `run.py`. |
| 2. Error-correction backend | `src/myntra/{groupid_ledger,error_reader,corrector}.py` | styleGroupId ledger; read+classify Myntra rejection files; regenerate a corrected sheet. |
| 3. Web app (FastAPI) | `src/web/` | "Marigold Ops" UI: Flow A *Generate*, Flow B *Fix*. Calls layers 1–2; no business logic of its own. |
| 4. Cloud / CI-CD / deploy | `Dockerfile`, `.github/workflows/ci-cd.yml`, `aws/`, `S3/`, `docs/runbooks/` | Image build, GitHub Actions → ECR via OIDC, **auto-deploy to EC2 via SSM**, Cognito/SSM/Secrets. |

**Full map with data flow, every module, and integration boundaries:**
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — start there for "what comes from where".

## Deployment status (2026-07-02) — LIVE

The app runs on a start/stop **EC2 t3.micro** (`listing-app`), image pulled from ECR. **Real
Cognito auth is enforced** (hosted-UI login → `/auth/callback` → `id_token` cookie). Because
Cognito rejects plain-HTTP callbacks on any non-`localhost` host and there is **no TLS yet**,
the app is reached through an **SSH tunnel to localhost**:
`ssh -i <key>.pem -L 8000:localhost:80 ec2-user@<EC2_IP>` → browse `http://localhost:8000/`.
**CI/CD is full CD:** push to `main` → tests → build/push `:latest` → the `deploy` job restarts
the box via **SSM Run Command** (targets the `Name=listing-app` tag). Every AWS/GitHub resource
is catalogued in [`docs/infra-resources.md`](docs/infra-resources.md). Operate/rebuild via
[`docs/runbooks/web-ec2-deploy-console.md`](docs/runbooks/web-ec2-deploy-console.md). TLS + a
public URL (e.g. CloudFront) is intentionally **deferred** — do not open the SG to `0.0.0.0/0`.

## How to run / test

```bash
# CLI pipeline (reads real files in repo root / input/, writes output/)
python run.py

# Web app locally (no AWS needed; logs in a synthetic dev@local user)
#   bash:
LEDGER_LOCAL_PATH=src/web/runtime/ledger.json AUTH_DISABLED=1 uvicorn src.web.main:app --reload
#   PowerShell:
#   $env:LEDGER_LOCAL_PATH="src/web/runtime/ledger.json"; $env:AUTH_DISABLED="1"; uvicorn src.web.main:app --reload
# → http://localhost:8000/   (container runs on 8080; local uvicorn defaults to 8000)

# Tests (74; this is the CI gate)
python -m pytest -q
```

Python 3.12. Dependencies in `requirements.txt`. No Node / build step — the web UI is plain
CSS + vendored htmx + vendored fonts (no runtime CDN).

## Non-negotiable invariants (these are *why*, not obvious from code)

1. **Dropdown values must match the Myntra template's exact vocabulary spelling.** Any value
   for a dropdown-controlled column is validated via `mapper.validate_value` against the
   template's allowed list; non-matches are **flagged/rejected, never guessed or written**.
2. **In Phase 1 the code decides everything — no LLM in the data path.** All mapping, pricing,
   and validation is deterministic. Invalid values are surfaced for a human.
3. **`reserve()` never advances the styleGroupId counter; only `confirm()` does.** A generated-
   but-not-uploaded batch must not burn ids. (`src/myntra/groupid_ledger.py`.)
4. **Images must be served as `.jpg` from S3.** Myntra rejects Shopify `.webp` URLs by
   extension; the pipeline converts to JPG and writes the public S3 `.jpg` URL into the sheet.
5. **No runtime CDN.** All CSS/JS/fonts are vendored under `src/web/static/`. Don't reintroduce
   `fonts.googleapis.com` or CDN `<script>`/`<link>` tags.
6. **Fix-flow corrections are typed free-text, validated against vocab — not option buttons.**
   A "closest Myntra names" hint is OK; clickable option pickers are not (deliberate UX call).
7. **Secrets never go in git.** `.env` is git-ignored; the deployed app reads all config —
   including the Cognito client secret (an SSM **SecureString**) — from SSM Parameter Store via
   the EC2 instance role. (Secrets Manager was retired 2026-07-02; SSM SecureString is free.)

## Where the answers live

| You need… | Look in |
|---|---|
| What module does what + data flow + integration boundaries | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Usage + the Myntra upload rules (pricing, HSN, colours) | [`README.md`](README.md) |
| **Why** something is built the way it is (design decisions) | [`docs/decisions/`](docs/decisions/) |
| How to set up / operate AWS (Cognito, SSM/Secrets, CI/CD, EC2) | [`docs/runbooks/`](docs/runbooks/) |
| Deep design specs + implementation plans | [`docs/superpowers/specs/`](docs/superpowers/specs/), [`docs/superpowers/plans/`](docs/superpowers/plans/) |
| Chronological history + the full upload-error debugging story | [`docs/journal/`](docs/journal/) |

## Conventions for changing this repo

- **Tests gate everything** — `python -m pytest -q` must stay green; CI runs it on every push/PR.
- **Config over code** — mapping/pricing/colour/HSN behaviour is driven by `config/myntra/*.yaml`;
  prefer editing YAML to hard-coding.
- **The web layer must not duplicate business logic** — it calls `src/myntra` / `src/core`.
- **Record the *why*** — when you make a non-obvious design choice, add an ADR in
  `docs/decisions/` and (if it changes the map) update `docs/ARCHITECTURE.md`.
</content>
