#!/usr/bin/env python3
"""Original candidate-list order for a Knesset's MKs.

Usage: python3 build_k25_list_order.py [--knesset 25|26]  (default 25)

K25 source: Hebrew Wikipedia, "הכנסת העשרים וחמש" — the "חברי הכנסת" table lists
everyone who served, per faction, in original election-list order (current
MKs bolded). gov.il/bechirot CEC pages bot-block scripted fetches (403), so
Wikipedia is the accessible mirror of the official lists.

K26 source: data/k26_preview.json — the CEC candidate lists harvested by
build_k26_preview.py (official list order, elected = first `seats` per
party). No scraping needed, and it works before OData publishes anything.

Output: data/k{N}_list_order.json
  { "<surname-first MK name (mk_photos.json key)>": {"rank": n, "faction": ...}, ... }
Rank is 1-based *within the MK's current faction*, preserving the relative
order from the original joint list (e.g. הציונות הדתית+עוצמה יהודית+נעם ran
as one list in 2022; each splinter faction keeps its members' relative order).
MKs not found in the table get rank null and are listed in _unmatched.
"""
import argparse, json, re, sys, urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"


def main_k26():
    """Rank = 1-based position in the party's official CEC candidate list.

    Derived from `candidates`, not by enumerating `elected`: after
    pre-swearing resignations `elected` is no longer a prefix of the list,
    and "place N on the list" must stay the original CEC position
    (counting candidates who have since resigned) — as in the K25 builder.
    """
    prev_path = DATA / "k26_preview.json"
    if not prev_path.exists():
        print("K26: data/k26_preview.json missing (run build_k26_preview.py) — skipping list-order build")
        return
    prev = json.loads(prev_path.read_text("utf-8"))
    result = {}
    for p in prev.get("parties", []):
        candidates = p.get("candidates", [])
        for name in p.get("elected", []):
            if name in candidates:
                result[name] = {"rank": candidates.index(name) + 1, "faction": p["name"]}
    if not result:
        print("K26: preview has no elected MKs — skipping list-order build")
        return
    json.dump({"_note": "Rank within party list, by official CEC candidate-list order "
                        "(data/k26_preview.json via build_k26_preview.py, fetched at build time).",
               "_unmatched": [], "ranks": result},
              open(DATA / "k26_list_order.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"K26 list order: {len(result)} elected MKs ranked")

WIKI_API = ("https://he.wikipedia.org/w/api.php?action=parse"
            "&page=%D7%94%D7%9B%D7%A0%D7%A1%D7%AA%20%D7%94%D7%A2%D7%A9%D7%A8%D7%99%D7%9D%20%D7%95%D7%97%D7%9E%D7%A9"
            "&prop=wikitext&format=json")

def fetch_wikitext():
    req = urllib.request.Request(WIKI_API, headers={"User-Agent": "biapia-research/1.0 (list-order build)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["parse"]["wikitext"]["*"]

def norm(name):
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)  # disambiguation: ישראל כ"ץ (הליכוד)
    return name.replace("-", "־").replace("–", "־").strip()

def parse_faction_lists(wt):
    """Return {wiki faction label: [names in list order]} from the MK table."""
    start = wt.find("חברי הכנסת")
    seg = wt[start:]
    end = seg.find("\n|}")
    if end > 0:
        seg = seg[:end]
    seg = seg.replace("'''", "")
    factions, current = {}, None
    for line in seg.split("\n"):
        line = line.strip()
        hdr = re.match(r"\|\[\[(?:[^|\]]*\|)?([^\]]+)\]\].*<small>(?:[\d]+ חברים|חבר אחד)</small>", line)
        if hdr:
            current = hdr.group(1)
            factions[current] = []
            continue
        if current and line.startswith("|") and "[[" in line:
            for m in re.finditer(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line):
                label = m.group(2) or m.group(1)
                n = norm(re.sub(r"<[^>]+>", "", label))
                if n and len(n) < 40:
                    factions[current].append(n)
                    if m.group(2):  # alias: link target is the fuller name
                        ALIASES[norm(m.group(1))] = n
    return {f: ns for f, ns in factions.items() if ns}

ALIASES = {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knesset", type=int, default=25, choices=(25, 26))
    if ap.parse_args().knesset == 26:
        main_k26()
        return
    mkp = json.load(open("data/mk_photos.json", encoding="utf-8"))
    wiki_disp = json.load(open("data/mk_wiki.json", encoding="utf-8"))  # surname-first -> display (given-first)
    disp_to_key = {v: k for k, v in wiki_disp.items()}

    factions = parse_faction_lists(fetch_wikitext())
    for f, names in factions.items():
        print(f"{f}: {len(names)} names listed", file=sys.stderr)

    # Global ordered pool: display name -> earliest position across factions.
    # Build per-current-faction relative order from whichever wiki list holds the MK.
    out, unmatched = {}, []
    by_faction = {}
    for key, info in mkp.items():
        by_faction.setdefault(info["faction"], []).append(key)

    flat = []  # (display name, wiki faction, position)
    for f, names in factions.items():
        for i, n in enumerate(names):
            flat.append((n, f, i))

    for key in mkp:
        disp = norm(wiki_disp.get(key, key))
        disp = ALIASES.get(disp, disp)
        hits = [x for x in flat if x[0] == disp]
        if hits:
            out[key] = hits[0][2]  # temp: global position in its wiki list
        else:
            unmatched.append(key)

    # rank = true position in the original list sequence (1-based, counting
    # members who have since left), so "place N on the 2022 list" stays factual.
    # Sorting by it preserves the original relative order.
    result = {}
    for faction, keys in by_faction.items():
        for k in keys:
            result[k] = {"rank": out[k] + 1 if k in out else None}

    # Leavers: MKs who appear in vote records but are not in the current
    # roster. They still get their original list rank, so vote plenums from
    # while they served can seat them in their party wedge. Keys here are
    # surname-first exactly as in bills.json mk_votes; display form is the
    # token flip (surname first -> given first).
    served = set()
    try:
        for b in json.load(open("data/bills.json", encoding="utf-8")):
            if b.get("knesset_num") != 25:
                continue
            for mv in b.get("mk_votes") or []:
                served.add(mv["mk_name"])
    except FileNotFoundError:
        pass
    leavers = sorted(served - set(mkp))
    # compound / multi-token names the simple flip cannot produce
    LEAVER_ALIAS = {"זוהר מכלוף מיקי": "מיקי זוהר", "רגב מירי מרים": "מירי רגב",
                    "שאשא ביטון יפעת": "יפעת שאשא-ביטון", "בן צור יואב": "יואב בן צור",
                    "מושיאשוילי סימון": "סימיון מושיאשוילי"}
    leaver_unmatched = []
    for key in leavers:
        disp = norm(LEAVER_ALIAS.get(key) or " ".join(key.split()[1:] + key.split()[:1]))
        disp = ALIASES.get(disp, disp)
        hits = [x for x in flat if x[0] == disp]
        if hits:
            result[key] = {"rank": hits[0][2] + 1, "left": True}
        else:
            result[key] = {"rank": None, "left": True}
            leaver_unmatched.append(key)
    print(f"leavers: {len(leavers)}, unmatched: {leaver_unmatched}", file=sys.stderr)

    json.dump({"_note": "Rank within current faction, by original 2022 election-list order "
                        "(he.wikipedia 'הכנסת העשרים וחמש' MK table, fetched at build time).",
               "_unmatched": unmatched, "ranks": result},
              open("data/k25_list_order.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    matched = sum(1 for v in result.values() if v["rank"])
    print(f"MATCHED {matched}/{len(result)}  unmatched={len(unmatched)}")
    for u in unmatched:
        print("  UNMATCHED:", u, "->", wiki_disp.get(u))

if __name__ == "__main__":
    main()
