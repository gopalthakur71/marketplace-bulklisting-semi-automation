# Brand Colour (Remarks) — automatic fill on the attribute screen

**Status:** approved by the owner 2026-07-27. Extends Flow D
(`docs/superpowers/specs/2026-07-26-in-app-attribute-entry-design.md`).

## 1. The problem

`Brand Colour (Remarks)` is a **mandatory, free-text** column in the Myntra template (it has no
dropdown vocabulary — `vocab_by_header["Brand Colour (Remarks)"]` is empty). The generate
pipeline never fills it: it is absent from `column_map.yaml`, from `constants.yaml`, and from
`user_filled_attributes`. So every generated sheet leaves it blank, Myntra rejects the rows with
`Brand Colour (Remarks) cannot be null`, and the seller only recovers through Flow B, where
`config/myntra/error_rules.yaml` classifies the error as `manual_choice` on `Prominent Colour`
and `corrector.py:88-90` mirrors the chosen colour in, lowercased.

That is a rejection round-trip for a value the app can already derive.

## 2. Decision

**Brand Colour (Remarks) follows Prominent Colour, and the app fills it automatically.**
(Owner's words: "Brand color will follow prominent color attribute. This thing app fill
automatically.")

Not a dropdown, not a text box — there is nothing for the seller to type. The value is derived at
**save** time on the Fill attributes screen, using the same rule Flow B already applies, so the
two surfaces cannot disagree.

## 3. Rules

| Prominent Colour the seller picked | Brand Colour (Remarks) written |
|---|---|
| a real vocabulary value, e.g. `Blue` | `blue` — the same value, lowercased |
| left on "— choose —" (blank) | blank |
| the literal `NA` | blank |

`NA` and blank both mean "no colour stated", so there is nothing truthful to mirror and the app
does not invent one. Re-saving with a different colour overwrites the cell, so the two columns
can never drift apart.

## 4. Where the logic lives

A pure function in `src/myntra/attribute_entry.py`:

```
BRAND_COLOUR_HEADER = "Brand Colour (Remarks)"
derive_brand_colour(values: dict) -> str | None
```

`values` is the already-validated `{column: value_or_None}` dict. The function returns the
lowercased Prominent Colour when `preview.is_set` accepts it, else `None`. It lives beside
`validate_submitted` rather than in the router so it is unit-testable and reusable if the
pipeline ever needs the same derivation.

The save route (`src/web/routers/attributes.py:attributes_save`) folds the derived value into
each entry's values dict before calling `write_attributes`, and only when the template actually
carries the column — a template without it must not raise. `write_attributes` itself needs **no
change**: it already writes any header it is handed and skips headers the template lacks.

## 5. What the seller sees

Each SKU panel gains one **read-only** line under the dropdowns:

> Brand Colour (Remarks): `blue` — filled automatically from Prominent Colour

It shows the value **currently in the workbook**, read from the sheet like every other
pre-selected value — not a prediction of what a pending selection would produce. So it is blank
the first time the screen is opened and accurate after every save. Automatic, but not silent:
the seller can see what is going into the file before downloading it.

Nothing else moves: Brand Colour is **not** added to `user_filled_attributes`, does **not** count
toward the `n/12 filled` counter, and does **not** appear in the listing preview card. It is not
one of the 12 name-driving attributes and Myntra does not display it as a specification.

## 6. What does not change

- Flow B's existing mirror in `corrector.py` stays exactly as it is.
- The Excel path is unaffected: a seller who skips the screen still gets the blank column, as
  today. This spec removes the rejection only for sheets saved through Flow D.
- `validate_submitted` is untouched — the derived value never passes through vocabulary
  validation, because the column has no vocabulary.

## 7. Tests

| Test | Asserts |
|---|---|
| `derive_brand_colour` on a set colour | `{"Prominent Colour": "Blue"}` → `"blue"` |
| `derive_brand_colour` on blank | `{"Prominent Colour": None}` → `None` |
| `derive_brand_colour` on `NA` | `{"Prominent Colour": "NA"}` → `None` |
| `derive_brand_colour` with the key absent | `{}` → `None` |
| save route, colour picked | the `Brand Colour (Remarks)` cell of that row holds the lowercased colour |
| save route, no colour picked | the cell stays blank |
| save route, colour changed on re-save | the cell holds the new colour, not the old one |
| screen render after save | the panel shows the read-only line with the saved value |

The existing Flow D invariant tests (dropdowns survive, strings stay inline) already cover this
path, since the value goes through the same `write_attributes` save.

## 8. Risk

Low. One derived column, written only on a surface the seller opted into, with the same rule an
already-shipped flow uses. The one thing to keep honest is the `NA` case: `NA` is a real member
of the Prominent Colour vocabulary, so a naive truthiness check would write the string `"na"`
into the sheet. `preview.is_set` is the shared guard that prevents it.
