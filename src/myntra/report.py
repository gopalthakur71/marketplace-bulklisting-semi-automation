def write_report(rows, out_path):
    lines = []
    total_flags = sum(len(m.flags) for m, _ in rows)
    total_img_fail = sum(len(i.failed) for _, i in rows)
    lines.append("=== Myntra Phase 1 Fill Report ===")
    lines.append(f"Products: {len(rows)} | vocab flags: {total_flags} | image failures: {total_img_fail}")
    lines.append("")
    for m, img in rows:
        lines.append(f"SKU {m.sku}: {len(m.cells)} fields filled, {len(img.passed)} images OK")
        for f in m.flags:
            lines.append(f"  [FLAG] {f.field}: {f.reason} (value={f.value!r})")
        if m.blanks:
            lines.append(f"  [BLANK] left empty (manual/Phase 2): {', '.join(m.blanks)}")
        for name, reason in img.failed:
            lines.append(f"  [IMAGE FAIL] {name}: {reason}")
        lines.append("")
    text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
