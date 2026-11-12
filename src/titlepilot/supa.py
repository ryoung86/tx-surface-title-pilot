"""A small Supabase client: Storage uploads and signed links, and table writes and searches through the
REST API. Uses requests only, so nothing new needs installing.

Settings (in .env on the machine that runs the program, never in website code):
  SUPABASE_URL           https://<project>.supabase.co
  SUPABASE_SERVICE_KEY   a secret key (sb_secret_...) or the legacy service_role key; it can read and write
                         everything, so it stays on this machine
  SUPABASE_BUCKET        bucket name (default title-packages)
"""
import os
import re
from urllib.parse import quote

import requests

DEFAULT_BUCKET = "title-packages"


class SupabaseError(RuntimeError):
    pass


def safe_key(part: str) -> str:
    """Storage keys reject some characters (#, ?, %, brackets, accents). Keep letters, digits, spaces and
    - _ . ( ) , & ' ; replace the rest with '_'. The original name is kept in the database."""
    return re.sub(r"[^A-Za-z0-9 _.\-(),&']", "_", part).strip() or "_"


def object_key(*parts) -> str:
    return "/".join(safe_key(seg) for p in parts for seg in str(p).replace("\\", "/").split("/") if seg)


class Supabase:
    def __init__(self, url=None, key=None, bucket=None, session=None, timeout=120):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY") or ""
        self.bucket = bucket or os.environ.get("SUPABASE_BUCKET") or DEFAULT_BUCKET
        if not self.url or not self.key:
            raise SupabaseError("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env (Supabase > Project Settings > API)")
        self.http = session or requests.Session()
        self.timeout = timeout

    # ------------------------------------------------------------------------------------------ plumbing
    def _headers(self, extra=None):
        # New secret keys (sb_secret_...) are not JWTs and go on the apikey header only; the legacy
        # service_role key (a JWT) also goes in Authorization.
        h = {"apikey": self.key}
        if not self.key.startswith("sb_"):
            h["Authorization"] = f"Bearer {self.key}"
        h.update(extra or {})
        return h

    def _send(self, method, path, **kw):
        kw["headers"] = self._headers(kw.get("headers"))
        r = self.http.request(method, self.url + path, timeout=self.timeout, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json()
                detail = detail.get("message") or detail.get("error") or detail
            except ValueError:
                detail = r.text[:300]
            raise SupabaseError(f"{method} {path.split('?')[0]} failed ({r.status_code}): {detail}")
        return r

    # ------------------------------------------------------------------------------------------- storage
    def check_bucket(self):
        """Raise a plain-English error if the bucket is missing or the key is wrong."""
        self._send("GET", f"/storage/v1/bucket/{quote(self.bucket)}")

    def upload(self, key: str, data: bytes, content_type="application/octet-stream"):
        self._send("POST", f"/storage/v1/object/{quote(self.bucket)}/{quote(key)}", data=data,
                   headers={"Content-Type": content_type, "x-upsert": "true"})

    def signed_url(self, key: str, seconds=3600) -> str:
        r = self._send("POST", f"/storage/v1/object/sign/{quote(self.bucket)}/{quote(key)}",
                       json={"expiresIn": seconds})
        signed = r.json().get("signedURL") or r.json().get("signedUrl")
        return f"{self.url}/storage/v1{signed}" if signed and signed.startswith("/") else signed

    # -------------------------------------------------------------------------------------------- tables
    def select(self, table, **filters):
        params = {"select": filters.pop("select", "*")}
        params.update({k: f"eq.{v}" for k, v in filters.items()})
        return self._send("GET", f"/rest/v1/{table}", params=params).json()

    def upsert(self, table, rows, on_conflict):
        if not rows:
            return []
        r = self._send("POST", f"/rest/v1/{table}", params={"on_conflict": on_conflict}, json=rows,
                       headers={"Prefer": "resolution=merge-duplicates,return=representation"})
        return r.json()

    def insert(self, table, rows):
        if not rows:
            return []
        return self._send("POST", f"/rest/v1/{table}", json=rows, headers={"Prefer": "return=minimal"})

    def delete(self, table, column, values):
        values = list(values)
        if not values:
            return
        inlist = ",".join('"' + str(v).replace('"', '\\"') + '"' for v in values)
        self._send("DELETE", f"/rest/v1/{table}", params={column: f"in.({inlist})"})

    def rpc(self, fn, **args):
        return self._send("POST", f"/rest/v1/rpc/{fn}", json=args).json()
