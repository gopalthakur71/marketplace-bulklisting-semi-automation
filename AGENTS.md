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
| 1. Core fill pipeline | `src/core/` + `src/myntra/` + `config/myntra/` | Shopify CSV → mapped/validated Myntra `.xlsx` + images → S3. Entry: `run.py`. `src/myntra/image_replace.py` handles a single owner-uploaded replacement image (prepare + host), separate from the build-time image path in `src/core/images.py`. |
| 2. Error-correction backend | `src/myntra/{groupid_ledger,hsn_kb,sku_registry,error_reader,corrector}.py` | styleGroupId ledger; HSN knowledge base (learn HSN once per category\|fabric signature); per-SKU generation registry (duplicate-upload guard); read+classify Myntra rejection files; regenerate a corrected sheet. |
| 3. Web app (FastAPI) | `src/web/` | "Marigold Ops" UI: Flow A *Generate*, Flow B *Fix*, Flow C *Preview* (upload a filled or corrected sheet — the app **adopts** it as a job and opens it in Flow D for editing, rather than showing read-only cards), Flow D *Fill attributes* (choose the 12 seller-decided attributes in-app with a live listing preview, edit tags/names/HSN, and **replace individual product photos per slot**, instead of doing all of that in Excel). Calls layers 1–2; no business logic of its own. |
| 4. Cloud / CI-CD / deploy | `Dockerfile`, `.github/workflows/ci-cd.yml`, **`infra/terraform/`**, **`infra/ansible/`**, `docs/runbooks/` | Image build, GitHub Actions → ECR via OIDC, **auto-deploy to EC2 via SSM**. **Terraform owns every AWS resource; Ansible owns the server's setup.** (`aws/` and `S3/` hold the original hand-applied JSON policies, kept for history; the live versions are in `infra/terraform/`.) |

**Full map with data flow, every module, and integration boundaries:**
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — start there for "what comes from where".

## Deployment status (2026-11-17) — infrastructure as code, server on demand

**All infrastructure is code.** Terraform ([`infra/terraform/`](infra/terraform/)) owns every
AWS resource (imported from the console 2026-11-17 with zero changes); Ansible
([`infra/ansible/`](infra/ansible/), run from WSL over SSM) owns the server's setup: the
systemd unit, Docker log rotation and the journald cap.

**The EC2 server is disposable and usually doesn't exist.** `terraform apply -var
app_running=true` builds a fresh **Spot**-only server of any t3.small-like type (2 vCPU, 2–4 GB; AWS picks a Spot type with capacity, never above t3.small's on-demand price) (Amazon Linux 2023, no SSH key; never on-demand), `ansible-playbook
site.yml` sets it up, and `app_running=false` deletes it, so idle cost is under $0.50/mo. Nothing
lives on the box: data is in S3, config/secrets in SSM, the image in ECR. **Never `terraform
destroy`**: it targets the data bucket and IAM too. **Never configure the box by hand**: edit
Ansible instead. Step-by-step: [`docs/runbooks/infra-terraform-ansible.md`](docs/runbooks/infra-terraform-ansible.md).
Why: [`docs/decisions/2026-11-17-terraform-ansible-disposable-server.md`](docs/decisions/2026-11-17-terraform-ansible-disposable-server.md).

**Access** is an **SSM port forward** to `http://localhost:8000` (the command is the
`ssm_port_forward_command` Terraform output). No SSH. **The app has no login** (Cognito was
removed in phase 5): AWS IAM, i.e. the `gt` profile that opens the SSM session, is the only
gate. **CI/CD is full CD:** push to `main` → tests → build/push `:latest` → the `deploy`
job restarts the box via **SSM Run Command** (targets the `Name=listing-app` tag, so exactly one
instance may carry it). While the server is off, the deploy job fails harmlessly, and the next
"on" pulls `:latest`.

**All phases done (spec [`docs/superpowers/specs/2026-11-13-terraform-ansible-iac-design.md`](docs/superpowers/specs/2026-11-13-terraform-ansible-iac-design.md)):**
phase 4's SG ingress removal (zero inbound) is done, pulled forward because the app now has
no login. The old `listing-app` key pair is **kept on purpose** (the owner uses it elsewhere);
the server never uses it. Phase 5 (Cognito removal) is done:
code, and the pool, client, domain and SSM params deleted via Terraform. ECR keeps 3 images and the
server is Spot-only, any 2 vCPU / 2–4 GB burstable type (EC2 Fleet).
Every resource is catalogued in [`docs/infra-resources.md`](docs/infra-resources.md). TLS + a
public URL stay **deferred**: never open the SG to `0.0.0.0/0`.

## How to run / test

```bash
# CLI pipeline (reads real files in repo root / input/, writes output/)
python run.py

# Web app locally (no AWS needed; there is no login)
#   bash:
LEDGER_LOCAL_PATH=src/web/runtime/ledger.json HSN_LOCAL_PATH=src/web/runtime/hsn_kb.json SKU_REGISTRY_LOCAL_PATH=src/web/runtime/sku_registry.json uvicorn src.web.main:app --reload
#   PowerShell:
#   $env:LEDGER_LOCAL_PATH="src/web/runtime/ledger.json"; $env:HSN_LOCAL_PATH="src/web/runtime/hsn_kb.json"; $env:SKU_REGISTRY_LOCAL_PATH="src/web/runtime/sku_registry.json"; uvicorn src.web.main:app --reload
# → http://localhost:8000/   (container runs on 8080; local uvicorn defaults to 8000)

# Tests (241; this is the CI gate)
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
   but-not-uploaded batch must not burn ids. (`src/myntra/groupid_ledger.py`.) `cancel()` —
   used when a build is stopped — marks the batch `cancelled` and likewise leaves the counter
   alone, so a stopped run costs nothing and is safe to simply repeat.
4. **Images must be served as `.jpg` from S3.** Myntra rejects Shopify `.webp` URLs by
   extension; the pipeline converts to JPG and writes the public S3 `.jpg` URL into the sheet.
5. **No runtime CDN.** All CSS/JS/fonts are vendored under `src/web/static/`. Don't reintroduce
   `fonts.googleapis.com` or CDN `<script>`/`<link>` tags.
6. **Fix-flow corrections are typed free-text, validated against vocab — not option buttons.**
   A "closest Myntra names" hint is OK; clickable option pickers are not (deliberate UX call).
7. **Secrets never go in git.** `.env` is git-ignored; the deployed app reads all config —
   including the Gemini API key (an SSM **SecureString**) — from SSM Parameter Store via
   the EC2 instance role. (Secrets Manager was retired 2026-07-02; SSM SecureString is free.)
8. **The 12 name-driving attributes are the seller's call, never the machine's.** Myntra
   generates the public title and "Design Details" from the attribute columns, so a guessed
   attribute publishes a wrong title. `config/myntra/rules.yaml:user_filled_attributes` lists
   them; the mapper writes them **blank** and every surface (Excel dropdowns, `/preview`, the
   in-app **Fill attributes** screen) reads that one list. No auto-extraction, no synonym map,
   no pre-fill, and dropdown options come **strictly** from the template's own vocabulary —
   never invent a value or inject `NA` into a list that lacks it. Filling them is **optional**
   in both places: skipped columns stay blank and keep their Excel dropdown.
   (Decided 2026-07-24, in-app entry added 2026-07-27; see `docs/ARCHITECTURE.md` §3.)
9. **Keep the sheet's dropdowns alive and its strings inline.** The pipeline uses the V13
   template because openpyxl preserves its *plain* data-validations (the older template's x14
   ones are silently dropped). And Myntra's upload parser cannot resolve shared strings, so
   `fill.py` rewrites the Sarees sheet's text as inline strings **after** saving — any other
   code that re-saves a built workbook must re-apply that conversion, or Myntra rejects the file.

## Where the answers live

| You need… | Look in |
|---|---|
| What module does what + data flow + integration boundaries | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Usage + the Myntra upload rules (pricing, HSN, colours) | [`README.md`](README.md) |
| A plain-English tour of every feature / every technology (non-technical) | [`docs/APP-FEATURES-GUIDE.md`](docs/APP-FEATURES-GUIDE.md), [`docs/TECH-EXPLAINED-FOR-BEGINNERS.md`](docs/TECH-EXPLAINED-FOR-BEGINNERS.md) |
| **Why** something is built the way it is (design decisions) | [`docs/decisions/`](docs/decisions/) |
| How to turn the server on/off, open the app, change its setup | [`docs/runbooks/infra-terraform-ansible.md`](docs/runbooks/infra-terraform-ansible.md) |
| Every AWS/GitHub resource and which `.tf` file owns it | [`docs/infra-resources.md`](docs/infra-resources.md) |
| Original console setup (history; superseded by `infra/`) | [`docs/runbooks/`](docs/runbooks/) (`*-console.md`) |
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
