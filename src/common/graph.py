"""Read-only Microsoft Graph client with delegated sign-in and a local token cache."""
import atexit
import time

import msal
import requests

from common import env

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/Sites.Read.All"]
CACHE_FILE = env.ROOT / ".cache" / "msal_token_cache.json"


def _token(device_code: bool) -> str:
    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text())

    def save():
        if cache.has_state_changed:
            CACHE_FILE.parent.mkdir(exist_ok=True)
            CACHE_FILE.write_text(cache.serialize())

    atexit.register(save)

    app = msal.PublicClientApplication(
        env.get("SPO_CLIENT_ID"),
        authority=f"https://login.microsoftonline.com/{env.get('SPO_TENANT')}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result:
        if device_code:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise SystemExit(f"Device-code sign-in not available: {flow.get('error_description')}")
            print(flow["message"], flush=True)
            result = app.acquire_token_by_device_flow(flow)
        else:
            result = app.acquire_token_interactive(SCOPES, prompt="select_account")
    if "access_token" not in result:
        raise SystemExit(f"Sign-in failed: {result.get('error')}: {result.get('error_description')}")
    save()
    return result["access_token"]


class Graph:
    def __init__(self, device_code: bool = False, timeout: int = 60):
        self.device_code = device_code
        self.timeout = timeout
        self.session = requests.Session()
        self._auth()

    def _auth(self):
        self.session.headers["Authorization"] = f"Bearer {_token(self.device_code)}"
        # Lets Graph filter/page large lists on non-indexed columns.
        self.session.headers["Prefer"] = "HonorNonIndexedQueriesWarningMayFailRandomly"

    def get(self, url: str, params: dict | None = None) -> dict:
        if not url.startswith("http"):
            url = GRAPH + url
        for attempt in range(8):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 401 and attempt == 0:
                self._auth()  # token expired during a long run
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                print(f"  HTTP {resp.status_code}, retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            if not resp.ok:
                raise SystemExit(f"GET {url} -> HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise SystemExit(f"GET {url} kept failing after retries")
