from src.myntra.gemini_client import explain


def test_payload_contains_only_error_text_no_product_data():
    seen = {}

    def fake_client(prompt):
        seen["prompt"] = prompt
        return "This is a plain explanation."

    text = explain("HSN 52081120 does not match present 50072010",
                   client=fake_client)
    assert text == "This is a plain explanation."
    # The prompt must carry the error text but never a manufacturer/packer/address.
    assert "52081120" in seen["prompt"]
    assert "address" not in seen["prompt"].lower()
    assert "pincode" not in seen["prompt"].lower()


def test_returns_none_without_key_or_client():
    assert explain("anything", api_key=None, client=None) is None


def test_retries_then_falls_back_to_none():
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        raise RuntimeError("boom")

    assert explain("x", client=flaky, retries=1) is None
    assert calls["n"] == 2  # initial try + 1 retry
