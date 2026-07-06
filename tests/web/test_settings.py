from src.web.settings import (
    Settings, load_settings, ledger_store, hsn_store, LocalJsonStore)


def test_env_takes_precedence_over_ssm():
    env = {"S3_BUCKET": "from-env", "S3_REGION": "ap-south-1", "AUTH_DISABLED": "1"}
    calls = []

    def fake_ssm(name):
        calls.append(name)
        return "from-ssm"

    s = load_settings(env=env, ssm=fake_ssm)
    assert s.s3_bucket == "from-env"      # env wins
    assert s.auth_disabled is True
    assert "/marketplace-listing/s3_bucket" not in calls   # env-provided field skips SSM
    assert "/marketplace-listing/s3_region" not in calls   # env-provided field skips SSM


def test_client_secret_resolves_from_ssm_even_when_some_env_set():
    # The Cognito client secret is an SSM SecureString now (no Secrets Manager);
    # it must still fall back to SSM when only some env vars are set.
    env = {"S3_BUCKET": "x", "AUTH_DISABLED": "1"}
    ssm_values = {"/marketplace-listing/cognito_client_secret": "the-secret"}
    s = load_settings(env=env, ssm=lambda name: ssm_values.get(name))
    assert s.s3_bucket == "x"
    assert s.cognito_client_secret == "the-secret"


def test_falls_back_to_ssm_when_env_missing():
    env = {"AUTH_DISABLED": "1"}
    ssm_values = {"/marketplace-listing/s3_bucket": "bkt",
                  "/marketplace-listing/s3_region": "ap-south-1",
                  "/marketplace-listing/cognito_client_secret": "the-client-secret"}
    s = load_settings(env=env, ssm=lambda name: ssm_values.get(name))
    assert s.s3_bucket == "bkt"
    assert s.cognito_client_secret == "the-client-secret"


def test_ledger_store_local_when_path_set(tmp_path):
    env = {"AUTH_DISABLED": "1", "LEDGER_LOCAL_PATH": str(tmp_path / "led.json")}
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


def test_cookie_secure_from_env():
    s = load_settings(env={"AUTH_DISABLED": "1", "COOKIE_SECURE": "1"},
                      ssm=lambda n: None)
    assert s.cookie_secure is True


def test_cookie_secure_defaults_off():
    s = load_settings(env={"AUTH_DISABLED": "1"},
                      ssm=lambda n: None)
    assert s.cookie_secure is False


def test_ssm_values_are_whitespace_stripped():
    # A trailing "\n" hand-saved into SSM redirect_uri broke Cognito login with
    # redirect_mismatch; load_settings must strip it off.
    env = {"AUTH_DISABLED": "1"}
    ssm_values = {"/marketplace-listing/cognito_redirect_uri":
                  "http://localhost:8000/auth/callback\n"}
    s = load_settings(env=env, ssm=lambda name: ssm_values.get(name))
    assert s.cognito_redirect_uri == "http://localhost:8000/auth/callback"
