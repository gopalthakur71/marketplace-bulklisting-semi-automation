from src.myntra.signature import normalize


def test_same_error_different_skus_one_signature():
    a, _ = normalize("Seller Sku Code 169SDE326SFSF is already registered for seller 87065")
    b, _ = normalize("Seller Sku Code 165SDE226RSG is already registered for seller 87065")
    assert a == b
    assert a == "seller sku code <sku> is already registered for seller <num>"


def test_different_errors_stay_distinct():
    a, _ = normalize("Seller Sku Code X9A8B7 is already registered")
    b, _ = normalize("HSN given 52081120 does not match present 50072010")
    assert a != b


def test_captures_stripped_values():
    _, cap = normalize("style id 43427259 image https://x.com/a.jpg sku 127SDE826NSB")
    assert "43427259" in cap["NUM"]
    assert "https://x.com/a.jpg" in cap["URL"]
    assert "127SDE826NSB" in cap["SKU"]


def test_letters_only_words_are_kept():
    sig, _ = normalize("getBrandCodeFromBrandName returned null key")
    assert "getbrandcodefrombrandname" in sig
    assert "<sku>" not in sig  # no digits -> not treated as a code
