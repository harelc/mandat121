#!/usr/bin/env python3
"""Upload the protocol-search shards (data/search_c, data/search_p) to
Cloudflare R2 so the inverted indexes don't have to live in git.

Layout in the bucket (public read via the r2.dev base):
  shards/<corpus-version>/search_c/<bigram>.json
  shards/<corpus-version>/search_p/<bigram>.json
  manifest.json  {"version":1,"corpora":{"c":{"version":…,"shards":
                 {file: sha256}},"p":{…}}}  — PUT last, no-cache.

The client reads manifest.json once, then fetches shards under the
version prefix — a deploy can never mix two generations of shards.
A corpus version is a hash of its (filename, sha256) list, so an
unchanged corpus uploads nothing and keeps its version.

Credentials come from the environment ONLY (GitHub Actions secrets in
CI; never committed — CWE-798): R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_ENDPOINT (https://<account>.r2.cloudflarestorage.com).
Optional: R2_BUCKET (default mandat121-search), R2_PUBLIC_BASE
(default the site's r2.dev URL). Stdlib only, AWS SigV4 signing inline.
"""
import hashlib
import hmac
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUCKET = os.environ.get("R2_BUCKET", "mandat121-search")
PUBLIC_BASE = os.environ.get(
    "R2_PUBLIC_BASE",
    "https://pub-a65c4e6efb634d8da9873e4f51835a60.r2.dev").rstrip("/")
CORPORA = {"c": ROOT / "data" / "search_c", "p": ROOT / "data" / "search_p"}


def _sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def s3_request(method, key, body=b"", content_type=None, cache_control=None):
    endpoint = os.environ["R2_ENDPOINT"].rstrip("/")
    access = os.environ["R2_ACCESS_KEY_ID"]
    secret = os.environ["R2_SECRET_ACCESS_KEY"]
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    host = endpoint.split("://", 1)[1]
    uri = f"/{BUCKET}/{key}"
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash,
               "x-amz-date": amzdate}
    if content_type:
        headers["content-type"] = content_type
    if cache_control:
        headers["cache-control"] = cache_control
    signed = ";".join(sorted(headers))
    canon_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canon = "\n".join([method, uri, "", canon_headers, signed, payload_hash])
    scope = f"{datestamp}/auto/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                         hashlib.sha256(canon.encode()).hexdigest()])
    k = _sign(("AWS4" + secret).encode(), datestamp)
    k = _sign(k, "auto")
    k = _sign(k, "s3")
    k = _sign(k, "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed}, Signature={sig}")
    req = urllib.request.Request(endpoint + uri, data=body or None,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read()


def local_corpus(d):
    files = {}
    for f in sorted(d.glob("*.json")):
        if f.name == "manifest.json":
            continue
        files[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    version = hashlib.sha256(
        "".join(f"{n}:{h}" for n, h in sorted(files.items())).encode()
    ).hexdigest()[:16]
    return files, version


def main():
    for v in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"):
        if not os.environ.get(v):
            print(f"missing env {v} — set it from the R2 token (never commit it)")
            return 1
    try:
        req = urllib.request.Request(PUBLIC_BASE + "/manifest.json",
                                     headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            old = json.loads(r.read())
    except Exception:
        old = {"corpora": {}}
    manifest = {"version": 1, "corpora": {}}
    jobs = []  # (key, path, content_type, cache_control)
    for corpus, d in CORPORA.items():
        if not d.exists():
            print(f"search_{corpus}: no local dir, skipped")
            continue
        files, version = local_corpus(d)
        manifest["corpora"][corpus] = {"version": version, "shards": files}
        old_c = (old.get("corpora") or {}).get(corpus) or {}
        old_shards = old_c.get("shards") or {}
        changed = [n for n, h in files.items() if old_shards.get(n) != h]
        print(f"search_{corpus}: version {version}, {len(files)} shards, "
              f"{len(changed)} to upload")
        for n in changed:
            jobs.append((f"shards/{version}/search_{corpus}/{n}", d / n,
                         "application/json",
                         "public, max-age=86400, immutable"))
    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(s3_request, "PUT", k, p.read_bytes(), ct, cc): k
                for k, p, ct, cc in jobs}
        for fut in as_completed(futs):
            try:
                fut.result(); done += 1
            except Exception as e:  # noqa: BLE001
                failed.append((futs[fut], str(e)[:100]))
    print(f"uploaded {done}/{len(jobs)} shards")
    if failed:
        print(f"FAILED: {failed[:5]} — manifest NOT updated")
        return 1
    if jobs or old.get("corpora") != manifest["corpora"]:
        body = json.dumps(manifest, separators=(",", ":")).encode()
        s3_request("PUT", "manifest.json", body, "application/json", "no-cache")
        print("manifest.json updated")
    else:
        print("nothing changed — manifest untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
