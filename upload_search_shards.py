#!/usr/bin/env python3
"""Upload the protocol-search shards (data/search_c, data/search_p) to
Cloudflare R2 so the inverted indexes don't have to live in git.

Layout in the bucket (public read via the r2.dev base):
  shards/<corpus-version>/search_c/<bigram>.json
  shards/<corpus-version>/search_p/<bigram>.json
  manifest.json  {"version":1,"corpora":{"c":{"version":…,"shards":
                 {file: [sha256, size]}},"p":{…}}}  — PUT last, no-cache.
                 The sizes double as the 2 GB guard's ledger (see below).

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
import urllib.parse
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


def s3_request(method, key, body=b"", content_type=None, cache_control=None,
               query=""):
    # .strip() everything: a secret pasted with a trailing newline
    # otherwise lands in the URL/host and urllib rejects it (probe run
    # 43625340335 failed exactly that way on all 1,743 PUTs).
    endpoint = os.environ["R2_ENDPOINT"].strip().rstrip("/")
    access = os.environ["R2_ACCESS_KEY_ID"].strip()
    secret = os.environ["R2_SECRET_ACCESS_KEY"].strip()
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
    canon = "\n".join([method, uri, query, canon_headers, signed, payload_hash])
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
    req = urllib.request.Request(endpoint + uri + (f"?{query}" if query else ""),
                                 data=body or None,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read()


# --- 2 GB storage guard -------------------------------------------------
# R2's free tier is 10 GB, but this bucket is capped at 2 GB by policy:
# the uploader accounts usage from the previous manifest (the bucket's
# only writer — an object-scoped token cannot LIST anyway), projects
# the post-upload total (and the transient peak before stale
# generations are cleaned up), and REFUSES to write anything if either
# would exceed the cap. After a successful manifest flip it deletes
# stale shard generations, so steady state is one generation + manifest.
CAP_BYTES = int(os.environ.get("R2_CAP_BYTES", 2_000_000_000))


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
        manifest["corpora"][corpus] = {"version": version, "shards": {
            n: [h, (d / n).stat().st_size] for n, h in files.items()}}
        old_c = (old.get("corpora") or {}).get(corpus) or {}
        old_shards = old_c.get("shards") or {}
        def old_hash(meta):
            return meta[0] if isinstance(meta, list) else meta
        changed = [n for n, h in files.items() if old_hash(old_shards.get(n)) != h]
        print(f"search_{corpus}: version {version}, {len(files)} shards, "
              f"{len(changed)} to upload")
        for n in changed:
            jobs.append((f"shards/{version}/search_{corpus}/{n}", d / n,
                         "application/json",
                         "public, max-age=86400, immutable"))
    # 2 GB guard (pre-flight). Accounting is manifest-based, NOT S3 LIST:
    # an object-scoped R2 token gets 403 on ListObjects (probe run
    # 43625429053), and it doesn't need it — this script is the bucket's
    # only writer, so the previous manifest IS the ledger. Shard entries
    # are [sha256, size]; legacy {file: sha256} manifests count size 0
    # (first guarded run re-uploads under the new format anyway).
    def entry_size(meta):
        return meta[1] if isinstance(meta, list) and len(meta) > 1 else 0
    existing = {}  # key -> size, reconstructed from the old manifest
    for cname, oc in (old.get("corpora") or {}).items():
        over = oc.get("version")
        if not over:
            continue
        for fn, meta in (oc.get("shards") or {}).items():
            existing[f"shards/{over}/search_{cname}/{fn}"] = entry_size(meta)
    cur_total = sum(existing.values())
    new_bytes = sum(p.stat().st_size for _, p, _, _ in jobs)
    job_keys = {k for k, _, _, _ in jobs}
    overwritten = sum(s for k, s in existing.items() if k in job_keys)
    versions = {c["version"] for c in manifest["corpora"].values()}
    stale = {k: s for k, s in existing.items()
             if not any(k.startswith(f"shards/{v}/") for v in versions)}
    peak = cur_total - overwritten + new_bytes
    steady = peak - sum(stale.values())
    print(f"R2 guard: bucket now {cur_total/1e9:.3f} GB, peak projection "
          f"{peak/1e9:.3f} GB, steady state {steady/1e9:.3f} GB, "
          f"cap {CAP_BYTES/1e9:.1f} GB")
    if peak > CAP_BYTES or steady > CAP_BYTES:
        print(f"REFUSED: projection exceeds the {CAP_BYTES/1e9:.1f} GB cap — "
              "nothing uploaded. Prune the index or raise R2_CAP_BYTES.")
        return 1
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
    # Cleanup (only after the manifest points at the live generation):
    # delete stale generations so the bucket holds one generation, not
    # one per upload. Failures are warnings, never fatal to the upload.
    if stale:
        deleted = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(s3_request, "DELETE", k): k for k in stale}
            for fut in as_completed(futs):
                try:
                    fut.result(); deleted += 1
                except Exception:  # noqa: BLE001
                    pass
        print(f"cleaned {deleted}/{len(stale)} stale objects "
              f"({sum(stale.values())/1e6:.1f} MB) — bucket steady state "
              f"~{steady/1e9:.3f} GB of {CAP_BYTES/1e9:.1f} GB cap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
