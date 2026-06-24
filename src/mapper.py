import re

from src.models import MappedRow, Flag


def _fmt_num(x):
    """Format a float as integer string when whole, else trimmed decimal."""
    if x is None:
        return None
    if float(x).is_integer():
        return str(int(x))
    return ("%f" % x).rstrip("0").rstrip(".")


def validate_value(value, vocab):
    if value is None:
        return None
    key = str(value).strip().lower()
    for v in vocab:
        if str(v).strip().lower() == key:
            return v
    return None


def pick_colour_from_text(text, vocab, exclude=()):
    """Earliest whole-word match of a vocab colour in text; longer wins on ties."""
    if not text:
        return None
    low = str(text).lower()
    exclude_low = {str(e).strip().lower() for e in exclude}
    best = None  # (position, -length, canonical)
    for v in vocab:
        if str(v).strip().lower() in exclude_low:
            continue
        m = re.search(r"\b" + re.escape(str(v).lower()) + r"\b", low)
        if m:
            cand = (m.start(), -len(str(v)), v)
            if best is None or cand < best:
                best = cand
    return best[2] if best else None


def pick_colour_synonym(text, synonyms):
    """Earliest whole-word match of a synonym keyword -> its canonical colour."""
    if not text or not synonyms:
        return None
    low = str(text).lower()
    best = None  # (position, canonical)
    for keyword, canonical in synonyms.items():
        m = re.search(r"\b" + re.escape(str(keyword).lower()) + r"\b", low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), canonical)
    return best[1] if best else None


def _shopify_value(product, field_key):
    return getattr(product, field_key, None)


def _set(row, template, header, value):
    """Set a cell, validating against vocab if the header is vocab-controlled.

    Invalid vocab values are flagged and NOT written (deterministic, no guessing).
    """
    if value is None or str(value).strip() == "":
        return
    if header in template.vocab_by_header:
        canon = validate_value(value, template.vocab_by_header[header])
        if canon is None:
            row.flags.append(Flag(sku=row.sku, field=header,
                                  reason="value not in Myntra dropdown", value=str(value)))
            return
        row.cells[header] = str(canon)
    else:
        row.cells[header] = str(value)


def _set_forced(row, template, header, value):
    """Set a user-authoritative constant. Canonicalize to vocab when possible;
    otherwise write the value as given but flag that it is not a dropdown value."""
    if value is None or str(value).strip() == "":
        return
    if header in template.vocab_by_header:
        canon = validate_value(value, template.vocab_by_header[header])
        if canon is not None:
            row.cells[header] = str(canon)
            return
        row.flags.append(Flag(sku=row.sku, field=header,
                              reason="forced value not in Myntra dropdown", value=str(value)))
    row.cells[header] = str(value)


def map_product(product, template, column_map, constants, rules=None):
    rules = rules or {}
    row = MappedRow(sku=product.sku)

    # 1. user-authoritative constants (forced)
    for header, val in constants.items():
        _set_forced(row, template, header, val)

    # 2. direct column-map copies (vocab-validated, flag-don't-guess)
    for field_key, header in column_map.items():
        _set(row, template, header, _shopify_value(product, field_key))

    # 3. derived identity duplicates
    _set(row, template, "SKUCode", product.sku)
    _set(row, template, "vendorArticleNumber", product.sku)
    _set(row, template, "productDisplayName", product.title)

    # 4. deterministic pricing
    mrp = product.compare_at_price if product.compare_at_price else product.price
    _set(row, template, "MRP", _fmt_num(mrp))
    _set(row, template, "ISP", _fmt_num(product.price))

    # 5. HSN by name keyword
    for keyword, hsn in (rules.get("hsn_by_name_keyword") or {}).items():
        if keyword.lower() in (product.title or "").lower():
            _set(row, template, "HSN", hsn)
            break

    # 6. Prominent Colour from name, then description
    if rules.get("prominent_colour_from_name") and "Prominent Colour" in template.vocab_by_header:
        vocab = template.vocab_by_header["Prominent Colour"]
        exclude = rules.get("colour_scan_exclude") or []
        synonyms = rules.get("colour_synonyms") or {}
        colour = (pick_colour_from_text(product.title, vocab, exclude)
                  or pick_colour_from_text(product.body_html, vocab, exclude)
                  or pick_colour_synonym(product.title, synonyms)
                  or pick_colour_synonym(product.body_html, synonyms))
        if colour:
            row.cells["Prominent Colour"] = colour
        else:
            row.flags.append(Flag(sku=row.sku, field="Prominent Colour",
                                  reason="no dropdown colour found in name/description",
                                  value=product.title))

    # 7. record vocab-controlled headers left blank (manual / Phase 2 fill)
    for header in template.vocab_by_header:
        if header not in row.cells:
            row.blanks.append(header)

    return row
