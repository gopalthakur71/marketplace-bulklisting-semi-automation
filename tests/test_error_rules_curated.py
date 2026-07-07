from src.myntra.error_reader import load_rules, classify


def test_brand_code_null_is_brand_auto_fix():
    rules = load_rules()
    r = classify("Null key returned for cache operation [...getBrandCodeFromBrandName...]", rules)
    assert r["action"] == "auto_fix"
    assert r["category"] == "brand"


def test_incomplete_address_is_address_auto_fix():
    rules = load_rules()
    r = classify("Manufacturer and packer information is incomplete", rules)
    assert r["action"] == "auto_fix"
    assert r["category"] == "address"


def test_hsn_mismatch_is_explain_only():
    rules = load_rules()
    r = classify("HSN given 52081120 does not match the one present 50072010", rules)
    assert r["action"] == "explain_only"
    assert r["category"] == "hsn"


def test_flat_shot_image_is_explain_only():
    rules = load_rules()
    r = classify("Primary image appears to be a flat shot", rules)
    assert r["action"] == "explain_only"
    assert r["category"] == "image"
