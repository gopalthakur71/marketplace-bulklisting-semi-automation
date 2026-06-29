import json
import os
from dataclasses import dataclass

SSM_PREFIX = "/marketplace-listing/"
LEDGER_KEY = "state/myntra_groupid.json"

# env var name -> (settings attr, ssm param leaf, is_secret)
_FIELDS = [
    ("S3_BUCKET", "s3_bucket", "s3_bucket", False),
    ("S3_REGION", "s3_region", "s3_region", False),
    ("S3_PREFIX", "s3_prefix", "s3_prefix", False),
    ("COGNITO_POOL_ID", "cognito_pool_id", "cognito_pool_id", False),
    ("COGNITO_CLIENT_ID", "cognito_client_id", "cognito_client_id", False),
    ("COGNITO_DOMAIN", "cognito_domain", "cognito_domain", False),
    ("COGNITO_REDIRECT_URI", "cognito_redirect_uri", "cognito_redirect_uri", False),
    ("COGNITO_CLIENT_SECRET", "cognito_client_secret", "cognito_client_secret", True),
]


@dataclass
class Settings:
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_prefix: str = "myntra/"
    cognito_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""
    cognito_redirect_uri: str = ""
    auth_disabled: bool = False
    ledger_local_path: str | None = None


def _ssm_getter():
    """Lazy + fail-soft: build the client on first call; return None on any boto
    error (missing param, no creds, no region) so import never crashes offline."""
    def get(name):
        try:
            import boto3
            client = boto3.client("ssm")
            r = client.get_parameter(Name=name, WithDecryption=True)
            return r["Parameter"]["Value"]
        except Exception:
            return None
    return get


def _secrets_getter():
    """Lazy + fail-soft (see _ssm_getter)."""
    def get(name):
        try:
            import boto3
            client = boto3.client("secretsmanager")
            return client.get_secret_value(SecretId=name)["SecretString"]
        except Exception:
            return None
    return get


def load_settings(env=None, ssm=None, secrets=None) -> Settings:
    """Resolve each value from env first, else from SSM/Secrets. Pass ssm/secrets
    callables in tests; in production they default to real AWS getters (lazy).

    Env takes precedence as a whole: if env supplies *any* non-secret field,
    it is treated as the authoritative source and SSM/Secrets are not consulted
    for the remaining fields (dataclass defaults apply instead). SSM/Secrets are
    only consulted when env supplies none of the non-secret fields (e.g. a clean
    container boot where config lives in SSM Parameter Store)."""
    env = os.environ if env is None else env
    s = Settings()
    s.auth_disabled = env.get("AUTH_DISABLED", "") in ("1", "true", "True")
    s.ledger_local_path = env.get("LEDGER_LOCAL_PATH") or None

    use_aws_fallback = not _any_env(env)
    ssm = ssm if ssm is not None else _ssm_getter()
    secrets = secrets if secrets is not None else _secrets_getter()

    for env_name, attr, leaf, is_secret in _FIELDS:
        val = env.get(env_name)
        if val is None and use_aws_fallback:
            val = (secrets if is_secret else ssm)(SSM_PREFIX + leaf)
        if val is not None:
            setattr(s, attr, val)
    return s


def _any_env(env):
    """True if env supplies at least one non-secret field (signals env is authoritative)."""
    return any(env.get(n) for n, _, _, secret in _FIELDS if not secret)


class LocalJsonStore:
    """Dev/offline ledger store: a single JSON file on disk."""
    def __init__(self, path):
        self.path = path

    def get_json(self, key):
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def put_json(self, key, data):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


def ledger_store(settings: Settings):
    if settings.ledger_local_path:
        return LocalJsonStore(settings.ledger_local_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))
