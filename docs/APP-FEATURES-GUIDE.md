# What this app does — every feature, in plain English

This is the everyday guide to the **Myntra Bulk-Listing app** ("Marigold Ops"). It explains
what each feature is *for* and how you use it — no code, no jargon. If you want to know how the
machinery underneath works, read [TECH-EXPLAINED-FOR-BEGINNERS.md](TECH-EXPLAINED-FOR-BEGINNERS.md).

---

## The problem this app solves

Selling on Myntra means filling a giant, fussy Excel sheet for every product — the right HSN
tax code, the exact colour word Myntra allows, image links, size charts, a unique "style group"
number, manufacturer address, and dozens more columns. Do one cell wrong and Myntra rejects the
whole upload with a cryptic error.

You already have all your product data in **Shopify**. This app is the bridge: it reads your
Shopify export and produces a **ready-to-upload Myntra sheet** — filled correctly, images hosted,
numbers assigned. And when Myntra *does* reject something, it reads the rejection and tells you
in plain English what to fix, fixing whatever it safely can for you.

There are **two main screens**: **Generate** (make a new listing sheet) and **Fix** (repair a
rejected one). Everything else supports these two.

---

## Logging in

The app is private to your team. When you open it you sign in with your email and password
(handled by Amazon's login service, "Cognito"). Once in, you land on the home dashboard with the
two main actions. On your own computer during testing, login can be switched off so you go
straight in.

---

## Screen 1 — Generate a Myntra listing sheet

**What it's for:** turn a Shopify export into a finished Myntra upload sheet.

**How you use it:**

1. In Shopify, export your products to a CSV file.
2. On the Generate screen, upload that CSV.
3. The app works in the background and shows a **step-by-step progress bar** (reading products,
   processing images, filling the sheet…).
4. When it's done, you **download the finished `.xlsx`**.
5. Open it in Excel and **choose the 12 attributes the app deliberately left blank** (see "The
   attributes you choose yourself" below) — every one is a dropdown, so you just pick.
6. Optional but recommended: upload the filled file to **Preview** (Screen 3) to see the listing
   before Myntra does. Then upload the same file to Myntra.
7. After Myntra accepts it, you click **Confirm** so the app locks in the style-group numbers it
   used (see "Style group numbers" below).

**What the app does for you behind that one upload:**

- **Groups your products correctly** — Shopify splits one product across many rows (one per size,
  one per photo). The app stitches them back into single products with an ordered image gallery.
- **Fills every Myntra column** from your data plus your saved defaults (brand, sizes, season,
  manufacturer address, etc.).
- **Hosts your images** — Myntra needs public image links, not files. The app converts your
  photos to the right format and uploads them to your own cloud storage, then puts those links in
  the sheet.
- **Leaves the attributes that decide your product's public name to you** — with the dropdowns
  live, so you pick from Myntra's own words (see below).
- **Never invents data it isn't sure about** — anything questionable is flagged in a report so you
  can eyeball it, instead of silently shipping a wrong value that Myntra would reject.

### The attributes you choose yourself

Here's the thing that surprises everyone: **Myntra ignores the product name and description you
send it.** It writes its own, out of the attribute columns. So the way you control what shoppers
see is not by writing a title — it's by choosing the right attributes.

Because a wrong attribute becomes a wrong public title, and because "is this silk or a silk
blend?" or "does this count as a border?" is a judgment call about *your* product, the app does
not guess them. It leaves these **12 columns blank, each with its Myntra dropdown attached**:

Prominent Colour · Second Prominent Colour · Third Prominent Colour · Saree Fabric ·
Blouse Fabric · Type · Ornamentation · Border · Pattern · Print or Pattern Type · Wash Care ·
Usage

You fill them **either inside the app** (the "Fill attributes" screen below — usually easier,
because you can see the photo and the resulting listing while you choose) **or in Excel** by
picking from the dropdowns. Either way, only Myntra's own words are offered — nothing invented,
and no "NA" option where Myntra doesn't actually allow one. Anything you leave blank just stays
blank; nothing breaks.

### Screen 1b — "Fill attributes" (choosing them inside the app)

When a sheet finishes building, a **✎ Fill attributes** button appears next to the download
button. It opens a page with **one expandable row per product**. Open one and you get three
things side by side:

1. **The product photo**, pulled from the Shopify file you uploaded, so you're looking at the
   actual saree while you decide.
2. **The 12 dropdowns.** The choices are read straight out of Myntra's own template, so you can
   only pick something Myntra accepts. If a value somehow isn't on Myntra's list, the app refuses
   the whole save and writes nothing rather than risk a rejected sheet.
3. **A live preview of the listing** — the title and "Design Details" Myntra will most likely
   generate, plus the exact specifications — which **updates as you change a dropdown**. That's
   the point: you can see the public wording change and check it against the photo.

Then press **Save attributes** once, at the bottom, and every product you filled is written into
the sheet the app already built for you.

A few things worth knowing:

- **It's optional.** Fill all of them, some of them, or none. Whatever you skip stays blank.
- **You can come back.** Reopen the screen and everything you saved is already selected, with an
  "n/12 filled" count on each row so you can see what's left.
- **The Excel dropdowns still work afterwards.** Saving in the app does not kill them — download
  the file and you can still finish the rest in Excel if you'd rather.
- **The preview here is the same one as Screen 3**, so the two can never tell you different
  things. Specifications are exact; title and Design Details are our best reconstruction and are
  labelled as approximate.

### Supporting features on the Generate screen

- **Style group numbers ("styleGroupId").** Myntra wants a unique number tying together the sizes
  of one product. The app keeps a running counter so numbers never clash or repeat. Important
  subtlety: when you *start* a batch the app only **reserves** numbers; it **confirms** (permanently
  uses them up) only after you tell it Myntra accepted the upload. So an abandoned upload doesn't
  waste numbers. The Generate screen shows you the next number before you start.

- **HSN tax-code memory ("HSN knowledge base").** Every product needs an 8-digit HSN tax code, and
  it depends on the product's category + fabric. Instead of you looking it up every time, the app
  **learns it once** per category-and-fabric combination on a review screen, then reuses it
  automatically forever after. You can also seed/correct it.

- **Duplicate-upload guard.** If you accidentally re-upload products you've already generated, the
  app **notices and warns you** ("these were already generated") instead of creating clashing
  duplicates. It can then rebuild a sheet for just those SKUs, reusing their original style number
  and HSN so nothing conflicts.

- **Small quality-of-life fixes** that came from real usage:
  - **Country-of-origin auto-fill** so you don't type "India" on every row.
  - **Undo "mark as uploaded"** in case you clicked confirm by mistake.
  - **A verify notice** reminding you to check the sheet before sending it to Myntra.
  - **Manual style-number seeding** for when you need to start the counter at a specific value.

---

## Screen 2 — Fix Myntra errors

**What it's for:** Myntra rejected some of your listings and sent back a file full of error
messages. This screen reads that file, explains each error in plain English, and repairs what it
safely can.

**How you use it:**

1. Myntra gives you a rejection file. The app accepts **three formats**: a per-SKU rejection
   `.xlsx`, a file-level `.csv`, or the "MDirect Listings Report".
2. Drop that file in the box and click **Check errors**.
3. The app sorts every problem into two groups (see below) and shows you plain-English
   explanations — no more decoding Myntra's raw error text.

### Group 1 — "We can fix these" (the app repairs them for you)

These are problems the app can correct deterministically and safely — for example an incomplete
**manufacturer/packer address** (filled from your saved settings), a missing pincode, or a blank
price it can back-fill. Some need a quick answer from you (it shows a text box and **checks your
answer against Myntra's allowed values before writing it**, so you can't introduce a new error).
Others are simply "already listed" SKUs you can tick to drop from the file.

When you're happy, you click **"Download now to fix →"** and the app hands you a corrected sheet
containing **only these fixable SKUs**, ready to re-upload.

### Group 2 — "You must fix these yourself first" (the app explains, but can't fix)

These need real human work the app can't do from a spreadsheet — **bad photos, low image quality,
wrong tax codes**. The app is honest about this: it **explains** each one clearly but writes
nothing, because faking a fix would just get rejected again.

For this group there's a guided path:

1. Fix the real problem in **Shopify first** (e.g. re-shoot and upload better photos onto the
   product).
2. **Re-export just those SKUs** from Shopify.
3. Upload that fresh export here and click **"Download listing file for these SKUs →"**.
4. The app **rebuilds a ready-to-upload Myntra sheet for only those SKUs**, using your new images
   but **keeping the same HSN and style group** as the first attempt — so Myntra sees a corrected
   version, not a brand-new clashing listing.

### The self-learning part

Every time Myntra sends a new kind of error, the app records its "signature" and how it was
handled, building up a **dictionary of known errors**. Over time it recognises more and more
rejections instantly. (For the plain-English explanations of unfamiliar errors it can consult
Google's Gemini AI — but it only sends a *normalised description* of the error, never your raw
business data.)

---

## Screen 3 — Preview the Myntra listing

**What it's for:** seeing what your listing will actually look like **before** Myntra sees it.

**How you use it:** after you've filled the attribute dropdowns in Excel and saved, upload that
same file on the Preview screen. You get one card per product. Then upload that same file to
Myntra — the app never changes your file, it only reads it.

(If you filled the attributes on Screen 1b instead, you have already seen this preview live while
choosing. This screen is still useful as a final look at a file you've since edited in Excel.)

**What's on a card, and how much to trust it:**

- **Specifications — exact.** These are the values you entered, shown the way Myntra will list
  them. What you see is what Myntra gets.
- **Title and "Design Details" — our best reconstruction, not a promise.** Myntra writes these
  itself from the attributes. We worked out its pattern from real Ijor listings and rebuild it
  here, and the screen says plainly that it's approximate. Two useful things we learned doing
  that: the **colour never appears in a saree title** (it shows in Design Details and the colour
  filter instead), and the "Zari Border Border" doubling you sometimes see on Myntra is **Myntra's
  own display quirk** — the dropdown words are clean, and it isn't something we can fix.
- **"Not filled" warnings.** Any of the 12 attributes you missed is called out on the card, so you
  catch it here instead of after a rejection.

Use it as a last look: if the title reads wrong, the fix is to change an *attribute*, not a name.

---

## The report you get

Alongside every generated sheet, the app produces an **audit report**: per product, how many
fields were filled, what was left blank, which colour/vocabulary values it flagged for your
review, and whether each image passed. This is your "check before you send to Myntra" list.

---

## What the app deliberately does *not* do

- It won't **guess** a Myntra colour or vocabulary word — it flags instead, because a wrong guess
  means a rejected upload.
- It won't **choose the 12 attributes that decide your public title** — those are yours, picked
  from Myntra's own dropdowns. It also won't pretend to know Myntra's generated wording
  word-for-word; the preview says it's approximate because it is.
- It won't **fake** fixes it can't really make (bad images, quality issues) — it tells you the
  truth and points you to the real fix.
- It won't **advance your style-group counter** until you confirm Myntra accepted the batch, so
  abandoned attempts cost you nothing.

That honesty is the whole point: the app does the tedious, mechanical 90% perfectly and is
straight with you about the 10% that needs your eyes.

---

## The typical end-to-end day

1. Export products from Shopify → **Generate** → **fill the 12 attributes** (in the app on
   **Fill attributes**, or in Excel after downloading) → **Preview** to check the listing →
   upload to Myntra → **Confirm**.
2. Myntra rejects a few → **Fix** → read the plain-English explanations.
3. Auto-fixable ones → **Download now to fix** → re-upload.
4. Image/quality ones → fix in Shopify → re-export those SKUs → **Download listing file** →
   re-upload.

That's the entire loop.
