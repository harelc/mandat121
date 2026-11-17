#!/usr/bin/env python3
"""Backfill missing K25 MK photos from the official Knesset MK site.

Source (discovered 2026-11-09): the MK lobby apps (main.knesset.gov.il/mk/apps)
are Radware-walled for scripts, but their data API on www.knesset.gov.il is
open:  WebSiteApi/knessetapi/MkLobby/GetMksPrevious?lang=he  returns every
past/partial MK with a direct official portrait URL on fs.knesset.gov.il
(globaldocs/MK/{siteId}/1_{siteId}_3_{docId}.jpeg). Those files download fine.

Targets: (a) roster MKs in mk_photos.json whose photo_url is not an existing
local file (in practice: hotlinked Wikimedia URLs, some 150px thumbs — these
get repointed to the downloaded official local file), (b) MKs who served in
K25 and left (vote records but no roster entry). Files land at the
deterministic path data/photos/mk_{md5(surname-first name)[:8]}.jpg.
Leavers are recorded in the manifest
(data/k25_official_photos.json) for the UI to pick up separately; their
names must NOT be added to mk_photos.json (its keys define the 120 seats).
"""
import hashlib, json, os, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMksPrevious?lang=he"
UA = {"User-Agent": "Mozilla/5.0 biapia-photos/1.0"}
DATA = Path(__file__).parent / "data"

def photo_path(name):
    return DATA / "photos" / f"mk_{hashlib.md5(name.encode()).hexdigest()[:8]}.jpg"

def norm(s):
    return " ".join(str(s).replace("'", "").replace("׳", "").split())

TARGET_KNESSET = 25

def pick_row(candidates):
    """Pick one API row from all rows matching a name.

    The API returns one row per (person, Knesset term), and distinct
    people can share a name — e.g. כהן אלי is both MkId 714 (b. 1949,
    Knesset 15 only) and MkId 928 (b. 1972, Knessets 20-26, the K25
    minister). Taking the first name match (the old setdefault) served
    the 714 portrait for the K25 MK. Prefer a row from the target
    Knesset, which only the right person can have; fall back to the
    most recent term otherwise.
    """
    if not candidates:
        return None
    in_target = [r for r in candidates if r.get("Knesset") == TARGET_KNESSET]
    pool = in_target or candidates
    return max(pool, key=lambda r: (r.get("Knesset") or 0, r.get("MkId") or 0))

def index_rows(rows):
    by_key, by_norm = {}, {}
    for row in rows:
        if row.get("ImgPath"):
            key = f"{row['LastName']} {row['FirstName']}"
            by_key.setdefault(key, []).append(row)
            by_norm.setdefault(norm(key), []).append(row)
    return by_key, by_norm

def find_row(by_key, by_norm, key):
    pool = list(by_key.get(key, ())) + list(by_norm.get(norm(key), ()))
    if not pool:
        toks = key.split()
        for cand in (" ".join(toks[1:] + toks[:1]), " ".join([toks[-1]] + toks[:-1])):
            pool += by_key.get(cand, ())
            pool += by_norm.get(norm(cand), ())
            if pool:
                break
    return pick_row(pool)

def main():
    req = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.load(r)["mkPrevious"]
    by_key, by_norm = index_rows(rows)

    def find(key):
        return find_row(by_key, by_norm, key)

    mkp = json.load(open(DATA / "mk_photos.json", encoding="utf-8"))
    targets = {}  # name -> kind
    for name, info in mkp.items():
        if info.get("photo_url") and not os.path.exists(info["photo_url"]):
            targets[name] = "roster-missing-file"
    served = set()
    for b in json.load(open(DATA / "bills.json", encoding="utf-8")):
        if b.get("knesset_num") == 25:
            for mv in b.get("mk_votes") or []:
                served.add(mv["mk_name"])
    for name in sorted(served - set(mkp)):
        targets[name] = "leaver"

    def download(item):
        name, kind = item
        row = find(name)
        if not row:
            return name, kind, None, "no match in MkLobby API"
        url = row["ImgPath"].split("?")[0]
        dest = photo_path(name)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
        except Exception as e:
            return name, kind, row, f"download failed: {e}"
        if len(data) < 3000 or not (data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG"):
            return name, kind, row, f"not an image ({len(data)}b)"
        dest.write_bytes(data)
        return name, kind, row, f"ok {len(data)}b"

    manifest, failures = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, kind, row, status in ex.map(download, sorted(targets.items())):
            print(f"{status:>12}  {name} ({kind})", flush=True)
            if row and status.startswith("ok"):
                manifest[name] = {"kind": kind, "mk_site_id": row["MkId"],
                                  "source": row["ImgPath"].split("?")[0],
                                  "file": str(photo_path(name).relative_to(DATA.parent))}
            else:
                failures.append((name, status))
    (DATA / "k25_official_photos.json").write_text(
        json.dumps({"_note": "Official Knesset portraits (MkLobby API, fs.knesset.gov.il), fetched at build time. kind=leaver entries are NOT in mk_photos.json on purpose.",
                    "photos": manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
    # Repoint roster entries from hotlinked remote URLs to the local official file.
    repointed = 0
    for name, info in manifest.items():
        if info["kind"] == "roster-missing-file" and name in mkp:
            mkp[name]["photo_url"] = info["file"]
            repointed += 1
    if repointed:
        (DATA / "mk_photos.json").write_text(json.dumps(mkp, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDONE {len(manifest)}/{len(targets)} downloaded, {repointed} roster entries repointed to local files, failures: {failures}")

if __name__ == "__main__":
    main()
