from src.web.settings import (
    Settings, load_settings, ledger_store, hsn_store, LocalJsonStore,
    explanation_store, correction_log_store)


def test_env_takes_precedence_over_ssm():
    env = {"S3_BUCKET": "from-env", "S3_REGION": "ap-south-1"}
    calls = []

    def fake_ssm(name):
        calls.append(name)
        return "from-ssm"

    s = load_settings(env=env, ssm=fake_ssm)
    assert s.s3_bucket == "from-env"      # env wins
    assert "/marketplace-listing/s3_bucket" not in calls   # env-provided field skips SSM
    assert "/marketplace-listing/s3_region" not in calls   # env-provided field skips SSM


def test_falls_back_to_ssm_when_env_missing():
    env = {}
    ssm_values = {"/marketplace-listing/s3_bucket": "bkt",
                  "/marketplace-listing/s3_region": "ap-south-1",
                  "/marketplace-listing/gemini_api_key": "the-gemini-key"}
    s = load_settings(env=env, ssm=lambda name: ssm_values.get(name))
    assert s.s3_bucket == "bkt"
    assert s.gemini_api_key == "the-gemini-key"


def test_ledger_store_local_when_path_set(tmp_path):
    env = {"LEDGER_LOCAL_PATH": str(tmp_path / "led.json")}
    s = load_settings(env=env, ssm=lambda n: None)
    store = ledger_store(s)
    assert isinstance(store, LocalJsonStore)
    assert store.get_json("anything") is None
    store.put_json("state/myntra_groupid.json", {"next_style_group_id": 5})
    assert store.get_json("state/myntra_groupid.json")["next_style_group_id"] == 5


def test_hsn_local_path_parsed_from_env():
    s = load_settings(env={"HSN_LOCAL_PATH": "/tmp/hsn.json"}, ssm=lambda name: None)
    assert s.hsn_local_path == "/tmp/hsn.json"


def test_hsn_store_uses_local_path_when_set(tmp_path):
    s = Settings(hsn_local_path=str(tmp_path / "hsn.json"))
    store = hsn_store(s)
    assert isinstance(store, LocalJsonStore)
    store.put_json("state/hsn_kb.json", {"classifications": {}})
    assert store.get_json("state/hsn_kb.json") == {"classifications": {}}


def test_sku_registry_local_path_and_store(tmp_path):
    from src.web.settings import sku_registry_store
    s = load_settings(env={"SKU_REGISTRY_LOCAL_PATH": str(tmp_path / "reg.json")},
                      ssm=lambda n: None)
    assert s.sku_registry_local_path == str(tmp_path / "reg.json")
    store = sku_registry_store(s)
    assert isinstance(store, LocalJsonStore)
    store.put_json("state/sku_registry.json", {"S1": {"content_hash": "h"}})
    assert store.get_json("state/sku_registry.json")["S1"]["content_hash"] == "h"




def test_gemini_and_store_paths_from_env():
    env = {
        "GEMINI_API_KEY": "k-123",
        "GEMINI_MODEL": "gemini-2.5-flash",
        "EXPLAIN_WITH_GEMINI": "1",
        "EXPLANATION_STORE_PATH": "/tmp/expl.json",
        "CORRECTION_LOG_PATH": "/tmp/corr.json",
    }
    s = load_settings(env=env, ssm=lambda name: None)
    assert s.gemini_api_key == "k-123"
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.explain_with_gemini is True
    assert isinstance(explanation_store(s), LocalJsonStore)
    assert explanation_store(s).path == "/tmp/expl.json"
    assert isinstance(correction_log_store(s), LocalJsonStore)


def test_gemini_defaults_off():
    s = load_settings(env={}, ssm=lambda name: None)
    assert s.explain_with_gemini is False
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.gemini_api_key == ""


def test_ssm_values_are_whitespace_stripped():
    # A trailing "\n" hand-saved into an SSM value once broke login; load_settings
    # must strip it off.
    ssm_values = {"/marketplace-listing/s3_bucket": "bkt\n"}
    s = load_settings(env={}, ssm=lambda name: ssm_values.get(name))
    assert s.s3_bucket == "bkt"


def test_cognito_params_are_never_read():
    # Phase 5: the Cognito SSM params are deleted; asking for them would only log warnings.
    calls = []
    load_settings(env={}, ssm=lambda name: calls.append(name))
    assert not [c for c in calls if "cognito" in c]
