from src.web.settings import load_settings, ledger_store, LocalJsonStore


def test_env_takes_precedence_over_ssm():
    env = {"S3_BUCKET": "from-env", "S3_REGION": "ap-south-1", "AUTH_DISABLED": "1"}
    calls = []

    def fake_ssm(name):
        calls.append(name)
        return "from-ssm"

    s = load_settings(env=env, ssm=fake_ssm, secrets=lambda n: "secret")
    assert s.s3_bucket == "from-env"      # env wins
    assert s.auth_disabled is True
    assert calls == []                    # SSM never consulted when env is set


def test_falls_back_to_ssm_and_secrets_when_env_missing():
    env = {"AUTH_DISABLED": "1"}
    ssm_values = {"/marketplace-listing/s3_bucket": "bkt",
                  "/marketplace-listing/s3_region": "ap-south-1"}
    s = load_settings(
        env=env,
        ssm=lambda name: ssm_values.get(name),
        secrets=lambda name: "the-client-secret",
    )
    assert s.s3_bucket == "bkt"
    assert s.cognito_client_secret == "the-client-secret"


def test_ledger_store_local_when_path_set(tmp_path):
    env = {"AUTH_DISABLED": "1", "LEDGER_LOCAL_PATH": str(tmp_path / "led.json")}
    s = load_settings(env=env, ssm=lambda n: None, secrets=lambda n: None)
    store = ledger_store(s)
    assert isinstance(store, LocalJsonStore)
    assert store.get_json("anything") is None
    store.put_json("state/myntra_groupid.json", {"next_style_group_id": 5})
    assert store.get_json("state/myntra_groupid.json")["next_style_group_id"] == 5
