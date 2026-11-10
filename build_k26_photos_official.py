#!/usr/bin/env python3
"""Official portraits for Knesset-26 MKs who have no photo yet.

The 16:00 swearing-in refresh (2026-11-10) also published portraits: the
MK-lobby API (GetMkLobbyData120, www.knesset.gov.il) now returns the K26
roster with an `ImagePath` per MK on fs.knesset.gov.il
(globaldocs/MK/{siteId}/1_{siteId}_3_{docId}.jpeg, versioned ?v=20261110).

Targets: K26 serving MKs (open stints in data/k26_tenure.json) with no
photo in mk_photos.json (the K25 roster file) and no entry in
data/k25_official_photos.json — at first run 57 of 120, i.e. exactly the
new faces; returning MKs keep their existing portraits. Files land at
the same deterministic path as the K25 builder,
data/photos/mk_{md5(surname-first name)[:8]}.jpg, and the manifest
(data/k26_official_photos.json) is merged by the UI over the K25 one.

Usage: python3 build_k26_photos_official.py   (safe no-op pre-tenure)
Stdlib only. Official Knesset-published portraits only.
"""
import hashlib, json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he"
UA = {"User-Agent": "Mozilla/5.0 mandat121-photos/1.0"}
DATA = Path(__file__).parent / "data"

def photo_path(name):
    return DATA / "photos" / f"mk_{hashlib.md5(name.encode()).hexdigest()[:8]}.jpg"

def norm(s):
    # Mirror index.html normName: geresh == apostrophe, and strip Hebrew
    # combining marks (the lobby feed ships 'פינק ַיאיא' with a stray hataf).
    s = str(s).replace("׳", "'").replace("״", '"')
    s = re.sub(r"[\u05b0-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7]", "", s)
    return " ".join(s.split())

def main():
    tenure_path = DATA / "k26_tenure.json"
    if not tenure_path.exists():
        print("K26: no k26_tenure.json yet (run build_k25_tenure.py --knesset 26) — skipping photos")
        return
    tenure = json.loads(tenure_path.read_text("utf-8"))["tenure"]
    serving = sorted(n for n, st in tenure.items() if any(not x[1] for x in st))

    covered = set(json.loads((DATA / "mk_photos.json").read_text("utf-8")))
    try:
        covered.update(json.loads((DATA / "k25_official_photos.json").read_text("utf-8"))["photos"])
    except Exception:
        pass
    targets = [n for n in serving if n not in covered]
    print(f"K26 serving: {len(serving)}, already have a photo: {len(serving) - len(targets)}, to fetch: {len(targets)}")
    if not targets:
        return

    req = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.load(r)["mks"]
    by_norm = {}
    for row in rows:
        if row.get("ImagePath"):
            key = norm(f"{row['Lastname']} {row['Firstname']}")
            by_norm.setdefault(key, row)

    def download(name):
        row = by_norm.get(norm(name))
        if not row:
            return name, None, "no match in MkLobby API"
        url = row["ImagePath"].split("?")[0]
        dest = photo_path(name)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
        except Exception as e:
            return name, row, f"download failed: {e}"
        if len(data) < 3000 or not (data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG"):
            return name, row, f"not an image ({len(data)}b)"
        dest.write_bytes(data)
        return name, row, f"ok {len(data)}b"

    (DATA / "photos").mkdir(exist_ok=True)
    manifest, failures = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, row, status in ex.map(download, targets):
            print(f"{status:>22}  {name}", flush=True)
            if row and status.startswith("ok"):
                manifest[name] = {"mk_site_id": row["MkId"],
                                  "source": row["ImagePath"].split("?")[0],
                                  "file": str(photo_path(name).relative_to(DATA.parent))}
            else:
                failures.append((name, status))
    (DATA / "k26_official_photos.json").write_text(
        json.dumps({"_note": "Official Knesset portraits for K26 MKs absent from the K25 photo files "
                             "(MkLobby GetMkLobbyData120 ImagePath, fs.knesset.gov.il), fetched at build time.",
                    "photos": manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nDONE {len(manifest)}/{len(targets)} downloaded, failures: {failures}", file=sys.stderr)

if __name__ == "__main__":
    main()
