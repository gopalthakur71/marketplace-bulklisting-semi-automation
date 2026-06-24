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


def _shopify_value(product, field_key):
    return getattr(product, field_key, None)


def _set(row, template, header, value):
    """Set a cell, validating against vocab if the header is vocab-controlled."""
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


def map_product(product, template, column_map, constants):
    row = MappedRow(sku=product.sku)

    # 1. constants
    for header, val in constants.items():
        _set(row, template, header, val)

    # 2. direct column-map copies
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

    # 5. record vocab-controlled headers left blank (manual / Phase 2 fill)
    for header in template.vocab_by_header:
        if header not in row.cells:
            row.blanks.append(header)

    return row
