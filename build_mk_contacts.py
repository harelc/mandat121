#!/usr/bin/env python3
"""Official MK contact / social / live-presence snapshot.

Source: the Knesset's own MK-lobby app API (the same endpoint that powers
main.knesset.gov.il/apps/mklobby/main/mks-attendance):
    GET https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he

Per MK it publishes, officially: office Email (@knesset.gov.il), Phone, Fax,
Facebook, Twitter/X, Instagram ("Instegram" in their payload), YouTube,
WebsiteUrl (often TikTok/Telegram), birth year — and `IsPresent`: whether
the MK is currently in the Knesset building. That last field is a live
signal, so this file is a *snapshot*: `fetched_at` is stored and the UI
must label it as such, never as history. There is no OData presence entity
(verified against $metadata 2026-11-10) and no presence history in this
API — do not fabricate a time series from it.

Output: data/mk_contacts.json keyed by surname-first name (our MK key),
plus `_meta`. Only official, Knesset-published channels are kept.
Stdlib only. No auth, no secrets.
"""
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://www.knesset.gov.il/WebSiteApi/knessetapi/MkLobby/GetMkLobbyData120?lang=he"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 mandat121-build"}
OUT = Path("data/mk_contacts.json")


def main():
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = {}
    present = 0
    for m in data.get("mks", []):
        key = " ".join(f"{m.get('Lastname') or ''} {m.get('Firstname') or ''}".split())
        if not key:
            continue
        present += bool(m.get("IsPresent"))
        out[key] = {
            "mk_id": m.get("MkId"),
            "present": bool(m.get("IsPresent")),
            "email": (m.get("Email") or "").strip(),
            "phone": (m.get("Phone") or "").strip(),
            "fax": (m.get("Fax") or "").strip(),
            "facebook": (m.get("Facebook") or "").strip(),
            "twitter": (m.get("Twitter") or "").strip(),
            "instagram": (m.get("Instegram") or "").strip(),
            "youtube": (m.get("Youtube") or "").strip(),
            "website": (m.get("WebsiteUrl") or "").strip(),
            "birth_year": m.get("YearDate"),
        }
    payload = {
        "_meta": {
            "source": URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "IsPresent is a live snapshot from the Knesset MK-lobby app; "
                    "contact/social fields are as officially published there.",
            "mks": len(out), "present_now": present,
        },
        "by_name": out,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {OUT} — {len(out)} MKs, {present} present at fetch time, "
          f"twitter {sum(bool(v['twitter']) for v in out.values())}, "
          f"facebook {sum(bool(v['facebook']) for v in out.values())}, "
          f"instagram {sum(bool(v['instagram']) for v in out.values())}, "
          f"email {sum(bool(v['email']) for v in out.values())}")


if __name__ == "__main__":
    main()
