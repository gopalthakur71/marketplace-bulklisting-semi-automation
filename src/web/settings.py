import json
import logging
import os
from dataclasses import dataclass

_log = logging.getLogger(__name__)

SSM_PREFIX = "/marketplace-listing/"

# env var name -> settings attr. Every field (incl. the Cognito client secret,
# which is an SSM SecureString) resolves from SSM Parameter Store — no Secrets
# Manager. The SecureString is decrypted by the getter's WithDecryption=True.
_FIELDS = [
    ("S3_BUCKET", "s3_bucket"),
    ("S3_REGION", "s3_region"),
    ("S3_PREFIX", "s3_prefix"),
    ("COGNITO_POOL_ID", "cognito_pool_id"),
    ("COGNITO_CLIENT_ID", "cognito_client_id"),
    ("COGNITO_DOMAIN", "cognito_domain"),
    ("COGNITO_REDIRECT_URI", "cognito_redirect_uri"),
    ("COGNITO_CLIENT_SECRET", "cognito_client_secret"),
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
    cookie_secure: bool = False
    ledger_local_path: str | None = None
    hsn_local_path: str | None = None


def _ssm_getter():
    """Lazy + fail-soft: build the client on first call; return None on any boto
    error (missing param, no creds, no region) so import never crashes offline.
    Failures are LOGGED — a silent empty return once masked a NoRegionError that
    blanked every Cognito value in production."""
    def get(name):
        try:
            import boto3
            client = boto3.client("ssm")
            r = client.get_parameter(Name=name, WithDecryption=True)
            return r["Parameter"]["Value"]
        except Exception as exc:
            _log.warning("SSM read failed for %s: %s", name, exc)
            return None
    return get


def load_settings(env=None, ssm=None) -> Settings:
    """Resolve each value from env first, else from SSM Parameter Store. Pass an
    `ssm` callable in tests; in production it defaults to the real AWS getter (lazy).

    Fallback is per-field, not all-or-nothing: each field independently uses its
    env value if present, otherwise consults SSM. This means a deploy that sets
    only some env vars still resolves the rest (including the Cognito client
    secret, stored as an SSM SecureString) from AWS."""
    env = os.environ if env is None else env
    s = Settings()
    s.auth_disabled = env.get("AUTH_DISABLED", "") in ("1", "true", "True")
    s.cookie_secure = env.get("COOKIE_SECURE", "") in ("1", "true", "True")
    s.ledger_local_path = env.get("LEDGER_LOCAL_PATH") or None
    s.hsn_local_path = env.get("HSN_LOCAL_PATH") or None

    ssm = ssm if ssm is not None else _ssm_getter()

    for env_name, attr in _FIELDS:
        val = env.get(env_name)
        if val is None:
            val = ssm(SSM_PREFIX + attr)
        if val is not None:
            # Strip stray whitespace/newlines — a trailing "\n" hand-saved into
            # the SSM redirect_uri once broke Cognito login with redirect_mismatch.
            setattr(s, attr, val.strip())
    return s


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


def hsn_store(settings: Settings):
    """Store for the HSN knowledge base. Mirrors ledger_store, but MUST use its
    own local path — LocalJsonStore writes one file per path, so sharing the
    ledger's path would clobber it."""
    if settings.hsn_local_path:
        return LocalJsonStore(settings.hsn_local_path)
    import boto3
    from src.myntra.groupid_ledger import S3JsonStore
    return S3JsonStore(settings.s3_bucket, boto3.client("s3", region_name=settings.s3_region))
