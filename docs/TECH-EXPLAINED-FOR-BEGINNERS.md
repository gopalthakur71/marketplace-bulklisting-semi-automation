# How this app is built — the tech, explained like you've never coded

This is a plain-English tour of the technology behind your Myntra app, written for someone who
runs the business, not the codebase. No prior tech knowledge assumed. Every tool is explained
with a real-world analogy and *why it's here*. For what the app does for users, see
[APP-FEATURES-GUIDE.md](APP-FEATURES-GUIDE.md).

> **The one-sentence version:** your app is a small Python program that reads spreadsheets, hosts
> images in the cloud, and shows a web page — packed into a shipping container, stored in Amazon's
> warehouse, and run on a rented Amazon computer that the internet can reach.

---

## 1. The big picture: what "an app" even is here

Your app isn't something installed on a phone. It's a **program running on a computer in the
cloud** that shows **web pages** in a browser. You (or your staff) open a link, see the Generate
and Fix screens, upload files, and download results. Everything happens on that cloud computer;
the browser is just the window.

Think of it as a **very smart Excel macro that lives on a website** — one that also talks to
Amazon's cloud to store images and remember things.

---

## 2. The language it's written in: Python

**Python** is the programming language the whole app is written in. It's popular because it reads
almost like English and is excellent at exactly this kind of work: reading spreadsheets,
processing images, following rules. When you hear "the code," it's Python code.

**Analogy:** Python is the language the recipe is written in. The recipe itself is your app's logic.

---

## 3. The three "layers" of logic (the recipe)

The code is organised so the fussy business rules are separated from the website. This matters
because it means the tricky Myntra logic can be tested on its own, without a browser.

1. **The core pipeline** — reads your Shopify file, groups products, processes images, fills the
   Myntra sheet. This is the heart.
2. **The error-correction backend** — reads Myntra's rejection files, classifies each error, and
   applies the fixes it safely can.
3. **The web app** — the screens you actually click. This layer has **no business logic of its
   own**; it just calls layers 1 and 2 and shows the results.

**Analogy:** Layer 1 is the kitchen, layer 2 is the returns-and-repairs desk, and layer 3 is the
waiter who takes your order and brings the dish. The waiter doesn't cook.

There's also a "config" folder of **settings files** (brand name, address, image sizes, colour
rules, error rules). These are written in a simple format called **YAML** so the app's behaviour
can be changed by editing settings — *without touching code*.

---

## 4. The web part: FastAPI, htmx, and templates

- **FastAPI** is the toolkit that turns Python into a website — it handles "someone opened this
  page", "someone uploaded this file", "send them this result". It's fast and modern; that's the
  "Fast" in the name.

- **Jinja templates** are the HTML page designs with blanks in them, like a mail-merge letter.
  The app fills the blanks (your product count, the error list) and sends the finished page to the
  browser. The files ending in `.html` are these templates.

- **htmx** is a small helper that lets parts of a page update *without a full reload* — that's how
  the Generate progress bar ticks along live, and how the Fix review appears instantly after you
  upload. It keeps the app feeling snappy without heavy, complicated front-end machinery.

- **uvicorn** is the little engine that actually *runs* the FastAPI website and listens for
  visitors. When you start the app, you're starting uvicorn.

**Analogy:** FastAPI is the restaurant's front-of-house system, Jinja templates are the printed
menus with your name filled in, htmx is the waiter who refills your water without clearing the
whole table, and uvicorn is the "OPEN" sign and the front door.

---

## 5. Where images and memory live: Amazon S3

Myntra needs your product photos as **public web links**, not attached files. So the app uploads
each photo to **Amazon S3** — Amazon's cloud file storage — which gives every image a public
`.jpg` link that Myntra can read. Your bucket (folder) is named `ijorethnicpartners`.

S3 also stores the app's **long-term memory** as small files: the style-group counter, the HSN
tax-code knowledge base, and the record of which SKUs you've already generated. These need to
survive even when the app restarts, so they live in S3, not in the app's temporary memory.

**Analogy:** S3 is a rented cloud warehouse. Photos go on the public shelf (anyone with the link
can view). The app's notebooks (counters, learned tax codes) go on a private shelf only the app
can reach.

---

## 6. Logging in: Amazon Cognito

**Cognito** is Amazon's ready-made login service. Rather than building password handling from
scratch (risky), the app hands login off to Cognito, which manages your email/password and proves
who you are with a secure digital token. When testing on your own computer, this can be turned
off so you skip straight in.

**Analogy:** Cognito is the security desk that checks IDs and issues visitor badges, so the app
itself never has to store or guard passwords.

---

## 7. Shipping the app: Docker, ECR, and EC2

This is the part that gets the app from your laptop onto the internet. Three pieces:

- **Docker** packages the whole app — Python, all its parts, the exact versions — into one sealed
  **container** (like a shipping container). "It works on my machine" stops being a problem because
  the container carries everything it needs, so it runs identically anywhere.

- **ECR** (Elastic Container Registry) is Amazon's **warehouse for those containers**. Every time
  the app is updated, a fresh container is built and stored in ECR, tagged `latest`.

- **EC2** (Elastic Compute Cloud) is a **rented Amazon computer** that's always reachable from the
  internet. It pulls the `latest` container from ECR and runs it. This is the computer your live
  app actually runs on. To save money it's a tiny machine (`t3.micro`) and is often **switched off
  when not in use** — idle it costs under a dollar a month.

**Analogy:** Docker is packing your app into a shipping container; ECR is the port warehouse where
containers are stored; EC2 is the truck that picks up the latest container and drives it around so
customers can reach it.

> **One quirk to remember:** every time you stop and start the EC2 computer, its public internet
> address (IP) changes. That's why you reach it through an "SSH tunnel" (a secure private
> connection) rather than a fixed web address — there's no proper web domain/HTTPS set up yet.

---

## 8. Auto-updating: GitHub, GitHub Actions, and CI/CD

- **Git** is a time machine for code — it records every change so you can see history and undo
  mistakes. **GitHub** is the website that stores that history online (your repo lives there).

- **CI/CD** stands for *Continuous Integration / Continuous Deployment* — jargon for "automatically
  test, build, and ship the app whenever the code changes." You don't do these steps by hand.

- **GitHub Actions** is the robot that runs that pipeline. Every time new code reaches the `main`
  version, it automatically:
  1. **Tests** — runs 170+ automated checks to make sure nothing broke.
  2. **Builds & stores** — if tests pass, it packs a fresh Docker container and pushes it to ECR.
  3. **Deploys** — tells the EC2 computer to restart on the new container (only works if the
     computer is currently switched on).

**Analogy:** GitHub is the master logbook of every recipe change. GitHub Actions is an automatic
quality inspector + delivery service: it taste-tests every change, and if it passes, boxes it and
ships it to the restaurant — no manual work from you.

> **Why "deploy" sometimes fails:** if the EC2 computer is switched off, step 3 has nothing to
> restart, so it errors. That's harmless — the new container is safely in ECR, and simply
> **switching the computer on later loads the latest version automatically** (booting = deploying).

---

## 9. Keeping secrets and settings: SSM Parameter Store

The app needs settings that shouldn't be baked into the code — your S3 bucket name, Cognito
details, and one genuine **secret** (the Cognito client password). These live in **AWS SSM
Parameter Store**, a small secure cloud key-value list. The secret is stored **encrypted**. The
live app reads these at startup so nothing sensitive is ever written in the code or the container.

**Analogy:** SSM is a small labelled key cabinet at the venue. The app picks up the keys it needs
when it opens for the day; they're never left taped to the code where anyone could copy them.

---

## 10. Automated tests: why "171 tests pass" matters

The project has 170+ **automated tests** — tiny programs that check the real app still behaves
correctly (right colour picked, right sheet produced, rejected errors classified properly). They
run automatically before any change ships. This is the safety net that lets changes go out
confidently without manually re-checking everything by hand each time.

**Analogy:** tests are a checklist of "taste every dish before it leaves the kitchen." If any dish
fails the check, the whole shipment is stopped before it reaches a customer.

---

## 11. Putting it all together — one full journey

Here's what actually happens, end to end, when the app is updated and you use it:

1. A change is made to the Python code → saved to **Git** → pushed to **GitHub**.
2. **GitHub Actions** runs the **tests**. If green, it builds a **Docker** container and stores it
   in **ECR**, then tells **EC2** to restart on it.
3. The **EC2** computer runs the container. **uvicorn** serves the **FastAPI** website.
4. You open the link, **Cognito** logs you in.
5. You upload a Shopify file. **Python** (layers 1–2) reads it, uploads photos to **S3**, reads
   settings/secrets from **SSM**, and builds your Myntra sheet.
6. **htmx** shows you live progress; you download the finished `.xlsx`.

Every buzzword in this app fits into that one sentence at the very top. Now you know what each one
is doing and why it's there.

---

## Mini-glossary

| Term | In one line |
|---|---|
| **Python** | The language the app is written in. |
| **FastAPI** | Turns Python into a website. |
| **uvicorn** | The engine that runs the website. |
| **htmx / Jinja** | Live page updates / fill-in-the-blank HTML pages. |
| **S3** | Amazon cloud storage for images + the app's memory files. |
| **Cognito** | Amazon's login/password service. |
| **Docker** | Packs the app into a portable sealed container. |
| **ECR** | Amazon's warehouse for those containers. |
| **EC2** | The rented Amazon computer that runs the live app. |
| **Git / GitHub** | The change-history time machine, stored online. |
| **GitHub Actions / CI-CD** | The robot that tests, builds and ships changes automatically. |
| **SSM Parameter Store** | Secure cloud cabinet for settings and the one secret. |
| **YAML** | Simple settings-file format used to change behaviour without code. |
| **HSN / styleGroupId** | Myntra's tax code / unique product-group number the app manages. |
| **SSH tunnel** | A secure private connection used to reach the app (no web domain yet). |
