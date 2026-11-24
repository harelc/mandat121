#!/usr/bin/env python3
"""Sync live/changing Knesset data between the local working tree and
Cloudflare R2 so daily OData refreshes publish directly to R2 without
committing to git or triggering Netlify deploys.

Reuses the existing `mandat121protocols` bucket under two prefixes:
  data/<rel>        — published client-facing live files (Cache-Control:
                      public, max-age=86400; client busts cache via ?v=<sha16>)
  state/data/<rel>  — builder bookkeeping/checkpoints (_state.json,
                      plenum_hl/*, search_*<N>/manifest.json; no-cache)
  data-manifest.json — PUT last (Cache-Control: no-cache) for atomic flip:
      {"version": 1, "updated_at": "...",
       "files": {"data/k26_bills.json": ["<sha16>", size], ...},
       "state": {"data/events_state.json": ["<sha16>", size], ...}}

Workflow (knesset-daily.yml):
  1. pull --knesset 26 --snapshot-dir /tmp/r2_baseline
     Downloads the latest live files, state files, and K26 search shards
     from R2 into local data/, and snapshots the baseline for diffing.
  2. (builders run unchanged against local data/)
  3. push --knesset 26 --snapshot-dir /tmp/r2_baseline --changed-out /tmp/r2_changed.json
     Filters timestamp/formatting noise via meaningful_diff.is_meaningful,
     validates JSON, enforces the 2 GB R2 cap, uploads changed objects,
     and flips data-manifest.json atomically.

Credentials come from the environment ONLY (CWE-798):
  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT
Optional: R2_BUCKET (default mandat121protocols), R2_PUBLIC_BASE, R2_CAP_BYTES.
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from era_registry import knesset_type
from meaningful_diff import is_meaningful
from upload_search_shards import (
    CAP_BYTES,
    PUBLIC_BASE,
    list_objects,
    s3_request,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MANIFEST_KEY = "data-manifest.json"

# Static / Blobs-owned files under k{N}_* that are NOT refreshed by
# knesset-daily and should stay served from git/Netlify.
STATIC_ERA_SUFFIXES = ("_preview.json", "_presence.json")

# Shared directories containing both frozen K25 files (in git) and new
# live files added incrementally by knesset-daily. Files in these dirs
# enter R2 once created/modified (or already tracked in data-manifest.json).
INCREMENTAL_FILE_DIRS = ("data/protocols", "data/photos")
INCREMENTAL_STATE_DIRS = ("data/plenum_hl",)

CONTENT_TYPES = {
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def safe_data_path(rel: str) -> Path:
    """Validate a manifest relative path stays strictly under ROOT/data (CWE-22)."""
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or len(p.parts) < 2 or p.parts[0] != "data":
        raise ValueError(f"unsafe manifest path: {rel!r}")
    resolved = (ROOT / p).resolve()
    data_root = DATA_DIR.resolve()
    if resolved != data_root and data_root not in resolved.parents:
        raise ValueError(f"path escapes data/: {rel!r}")
    return resolved


def fetch_public(key: str, *, no_cache: bool = False):
    """GET a public object from R2 with retries on 5xx/network errors.
    Returns bytes, or None on 404."""
    url = f"{PUBLIC_BASE}/{key}"
    headers = {"Cache-Control": "no-cache"} if no_cache else {}
    for attempt in range(4):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 or attempt == 3:
                raise
        except Exception:
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))
    return None


def fetch_data_manifest():
    """Return (manifest_dict, existed_bool) from R2 data-manifest.json."""
    raw = fetch_public(MANIFEST_KEY, no_cache=True)
    if raw is None:
        return {"version": 1, "files": {}, "state": {}}, False
    j = json.loads(raw)
    j.setdefault("version", 1)
    j.setdefault("files", {})
    j.setdefault("state", {})
    return j, True


def _entry_hash(meta):
    return meta[0] if isinstance(meta, list) and meta else meta


def _entry_size(meta):
    return int(meta[1]) if isinstance(meta, list) and len(meta) > 1 else 0


def always_live_paths(kn: int):
    """Return (file_paths, state_paths) as sorted POSIX relpaths for era `kn`
    that currently exist under data/."""
    prefix = f"k{kn}_"
    files, state = set(), set()

    for p in sorted(DATA_DIR.glob(f"{prefix}*.json")):
        name = p.name
        if any(name.endswith(s) for s in STATIC_ERA_SUFFIXES):
            continue
        rel = f"data/{name}"
        if name.endswith("_state.json"):
            state.add(rel)
        else:
            files.add(rel)

    ag_dir = DATA_DIR / f"{prefix}agenda"
    if ag_dir.is_dir():
        for p in sorted(ag_dir.glob("*.json")):
            files.add(f"data/{prefix}agenda/{p.name}")

    for rel in ("data/events.json", "data/mk_highlights.json"):
        if (ROOT / rel).is_file():
            files.add(rel)

    for rel in (
        "data/events_state.json",
        "data/protocols_state.json",
        f"data/search_c{kn}/manifest.json",
        f"data/search_p{kn}/manifest.json",
    ):
        if (ROOT / rel).is_file():
            state.add(rel)

    return sorted(files), sorted(state)


def _download_missing(items):
    """items: list of (r2_key, local_path, expected_sha16). Returns count downloaded."""
    todo = []
    for key, dest, exp_hash in items:
        if dest.is_file() and exp_hash and sha16(dest.read_bytes()) == exp_hash:
            continue
        todo.append((key, dest))
    if not todo:
        return 0
    done, failed = 0, []

    def _one(key, dest):
        raw = fetch_public(key, no_cache=True)
        if raw is None:
            raise FileNotFoundError(f"R2 404: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_one, k, d): k for k, d in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                failed.append((futs[fut], str(e)[:120]))
    if failed:
        raise RuntimeError(f"R2 pull failed ({len(failed)}): {failed[:5]}")
    return done


def _pull_search_corpora(kn: int):
    """Sync R2 search shards for c{kn} and p{kn} into local data/search_*
    so incremental search builders merge onto the live R2 corpus."""
    raw = fetch_public("manifest.json", no_cache=True)
    if raw is None:
        return 0
    sm = json.loads(raw)
    corpora = sm.get("corpora") or {}
    items = []
    for corpus in (f"c{kn}", f"p{kn}"):
        entry = corpora.get(corpus)
        if not entry or not entry.get("version"):
            continue
        ver = entry["version"]
        shards = entry.get("shards") or {}
        local_dir = DATA_DIR / f"search_{corpus}"
        local_dir.mkdir(parents=True, exist_ok=True)
        # Remove local shards not in R2 manifest (keep manifest.json).
        for existing in local_dir.glob("*.json"):
            if existing.name == "manifest.json":
                continue
            if existing.name not in shards:
                existing.unlink()
        for fn, meta in shards.items():
            # Search manifest stores full 64-char sha256; compare full sha256.
            exp_full = _entry_hash(meta)
            dest = local_dir / fn
            if dest.is_file() and exp_full and hashlib.sha256(dest.read_bytes()).hexdigest() == exp_full:
                continue
            items.append((f"shards/{ver}/search_{corpus}/{fn}", dest, None))
    return _download_missing(items)


def _snapshot_tree(snapshot_dir: Path, rel_paths):
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        src = safe_data_path(rel)
        if src.is_file():
            dst = snapshot_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def cmd_pull(args):
    kn = args.knesset
    manifest, existed = fetch_data_manifest()
    if existed:
        items = []
        for rel, meta in manifest["files"].items():
            items.append((rel, safe_data_path(rel), _entry_hash(meta)))
        for rel, meta in manifest["state"].items():
            items.append((f"state/{rel}", safe_data_path(rel), _entry_hash(meta)))
        n_data = _download_missing(items)
        print(
            f"R2 pull: {len(manifest['files'])} live file(s), "
            f"{len(manifest['state'])} state file(s) in manifest; "
            f"downloaded {n_data} updated file(s)"
        )
    else:
        print("R2 pull: no data-manifest.json yet (first bootstrap run — using git baseline)")

    n_shards = _pull_search_corpora(kn)
    if n_shards:
        print(f"R2 pull: downloaded {n_shards} search shard(s) for c{kn}/p{kn}")

    if args.snapshot_dir:
        live_f, live_s = always_live_paths(kn)
        all_rels = set(live_f) | set(live_s) | set(manifest["files"]) | set(manifest["state"])
        _snapshot_tree(Path(args.snapshot_dir), sorted(all_rels))
        print(f"R2 pull: snapshotted {len(all_rels)} baseline file(s) to {args.snapshot_dir}")
    return 0


def _git_modified_or_untracked_under_data():
    """Return POSIX relpaths under data/ that differ from git HEAD or are untracked."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain", "-uall", "--", "data/"],
            cwd=ROOT,
        ).decode("utf-8", "replace")
    except Exception:
        return set()
    rels = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        p = line[3:].strip()
        if " -> " in p:
            p = p.split(" -> ", 1)[1].strip()
        p = p.strip('"')
        if p.startswith("data/") and (ROOT / p).is_file():
            rels.add(p)
    return rels


def _is_search_shard(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return (
        len(parts) == 3
        and parts[0] == "data"
        and parts[1].startswith("search_")
        and parts[2] != "manifest.json"
    )


def _classify_extra(rel: str, kn: int):
    """Classify a changed/untracked path under data/ into 'files', 'state', or None (skip)."""
    if _is_search_shard(rel) or rel.startswith("data/docs/"):
        return None
    if any(rel.endswith(s) for s in STATIC_ERA_SUFFIXES) and rel.startswith(f"data/k{kn}_"):
        return None
    if any(rel.startswith(d + "/") for d in INCREMENTAL_STATE_DIRS):
        return "state"
    if rel.endswith("_state.json") or rel.endswith("/manifest.json"):
        return "state"
    return "files"


def _read_baseline(rel: str, snapshot_dir: Path | None):
    if snapshot_dir is not None:
        p = snapshot_dir / rel
        if p.is_file():
            return p.read_bytes()
    # Fallback to git HEAD for files that weren't in the snapshot (e.g. a
    # frozen K25 file modified during a manual target=all run).
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def cmd_push(args):
    for v in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT"):
        if not os.environ.get(v):
            print(f"missing env {v} — set it from the R2 token (never commit it)")
            return 1

    kn = args.knesset
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
    old_manifest, old_existed = fetch_data_manifest()
    old_files = dict(old_manifest.get("files") or {})
    old_state = dict(old_manifest.get("state") or {})

    # Discover all candidate files for this push.
    live_f, live_s = always_live_paths(kn)
    cand_files = set(live_f) | set(old_files)
    cand_state = set(live_s) | set(old_state)

    # Also pick up any new/modified files in shared dirs (protocols/, photos/,
    # plenum_hl/) or manual target=all changes.
    for rel in _git_modified_or_untracked_under_data():
        if rel in cand_files or rel in cand_state:
            continue
        bucket = _classify_extra(rel, kn)
        if bucket == "files":
            cand_files.add(rel)
        elif bucket == "state":
            cand_state.add(rel)

    new_files = dict(old_files)
    new_state = dict(old_state)
    jobs = []       # (r2_key, local_path, content_type, cache_control)
    changed = []    # (status, rel_path) — meaningful changes for summary

    def _process(rel: str, is_state: bool):
        path = safe_data_path(rel)
        old_map = old_state if is_state else old_files
        new_map = new_state if is_state else new_files
        in_r2 = rel in old_map

        if not path.is_file():
            if in_r2:
                # Keep previous entry rather than silently deleting on a skipped layer.
                return
            return

        new_bytes = path.read_bytes()
        # Validate JSON before it can ever reach R2.
        if path.suffix == ".json":
            try:
                json.loads(new_bytes)
            except Exception as e:
                raise ValueError(f"invalid JSON in {rel}: {e}") from e

        old_bytes = _read_baseline(rel, snapshot_dir)
        new_h = sha16(new_bytes)
        r2_h = _entry_hash(old_map.get(rel))

        # Meaningful-diff filter: if already in R2 and only timestamps/orthography
        # changed vs baseline, restore baseline and skip upload.
        if not args.bootstrap and in_r2 and old_bytes is not None:
            if not is_meaningful(old_bytes, new_bytes):
                if new_bytes != old_bytes:
                    path.write_bytes(old_bytes)
                    print(f"timestamp/format only, skipped: {rel}")
                return

        meaningful_vs_baseline = (old_bytes is None) or is_meaningful(old_bytes, new_bytes)
        needs_upload = (not in_r2) or (r2_h != new_h)

        if needs_upload:
            r2_key = f"state/{rel}" if is_state else rel
            ct = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            cc = "no-cache" if is_state else "public, max-age=86400"
            jobs.append((r2_key, path, ct, cc))
            new_map[rel] = [new_h, len(new_bytes)]

        if meaningful_vs_baseline and (old_bytes is None or old_bytes != new_bytes):
            status = "A" if old_bytes is None else "M"
            changed.append((status, rel))

    for rel in sorted(cand_files):
        _process(rel, is_state=False)
    for rel in sorted(cand_state):
        _process(rel, is_state=True)

    if not jobs and old_existed:
        print("No meaningful data changes — R2 untouched.")
        if args.changed_out:
            Path(args.changed_out).write_text("[]", encoding="utf-8")
        return 0

    # 2 GB R2 storage cap guard (shared bucket with search shards).
    try:
        existing = list_objects()
        acct = "S3 LIST"
    except Exception as e:  # noqa: BLE001
        existing = {
            **{k: _entry_size(v) for k, v in old_files.items()},
            **{f"state/{k}": _entry_size(v) for k, v in old_state.items()},
        }
        acct = f"manifest ledger (LIST unavailable: {str(e)[:40]})"

    cur_total = sum(existing.values())
    new_bytes_total = sum(p.stat().st_size for _, p, _, _ in jobs)
    job_keys = {k for k, _, _, _ in jobs}
    overwritten = sum(sz for k, sz in existing.items() if k in job_keys)
    projected = cur_total - overwritten + new_bytes_total
    print(
        f"R2 guard [{acct}]: bucket now {cur_total/1e9:.3f} GB, "
        f"uploading {len(jobs)} file(s) ({new_bytes_total/1e6:.2f} MB), "
        f"projected {projected/1e9:.3f} GB, cap {CAP_BYTES/1e9:.1f} GB"
    )
    if projected > CAP_BYTES:
        print(f"REFUSED: projection exceeds the {CAP_BYTES/1e9:.1f} GB cap — nothing uploaded.")
        return 1

    done, failed = 0, []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(s3_request, "PUT", k, p.read_bytes(), ct, cc): k
            for k, p, ct, cc in jobs
        }
        for fut in as_completed(futs):
            try:
                fut.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                failed.append((futs[fut], str(e)[:120]))

    print(f"uploaded {done}/{len(jobs)} data file(s) to R2")
    if failed:
        print(f"FAILED: {failed[:5]} — {MANIFEST_KEY} NOT updated")
        return 1

    manifest_out = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": dict(sorted(new_files.items())),
        "state": dict(sorted(new_state.items())),
    }
    body = json.dumps(manifest_out, separators=(",", ":")).encode("utf-8")
    s3_request("PUT", MANIFEST_KEY, body, "application/json", "no-cache")
    print(
        f"{MANIFEST_KEY} updated ({len(new_files)} live files, "
        f"{len(new_state)} state files, {len(changed)} meaningful change(s))"
    )

    if args.changed_out:
        Path(args.changed_out).write_text(
            json.dumps(changed, ensure_ascii=False), encoding="utf-8"
        )
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync live Knesset data with Cloudflare R2.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_pull = sub.add_parser("pull", help="Pull live data + state + search shards from R2.")
    p_pull.add_argument("--knesset", type=knesset_type, required=True)
    p_pull.add_argument("--snapshot-dir", default=None, help="Save baseline snapshot for diffing")

    p_push = sub.add_parser("push", help="Diff and push meaningful live data updates to R2.")
    p_push.add_argument("--knesset", type=knesset_type, required=True)
    p_push.add_argument("--snapshot-dir", default=None, help="Baseline snapshot dir from pull")
    p_push.add_argument("--changed-out", default=None, help="Write JSON list of [status, path] changes")
    p_push.add_argument("--bootstrap", action="store_true", help="Seed all live files to R2 regardless of diff")

    args = ap.parse_args(argv)
    if args.cmd == "pull":
        return cmd_pull(args)
    return cmd_push(args)


if __name__ == "__main__":
    sys.exit(main())
