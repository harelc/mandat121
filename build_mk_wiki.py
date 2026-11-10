#!/usr/bin/env python3
"""Wikipedia abstracts + Wikidata-discovered X handles for MKs (ROADMAP P2).

Owner-approved sources (2026-11-10): A=Wikidata, B=official Knesset pages,
C=hand-curation fallback. This builder uses A for both new layers; B already
exists in build_mk_contacts.py and is never overwritten here.

Inputs (on disk):
  data/mk_wiki.json     surname-first name -> Hebrew Wikipedia article title
                        (the hand-curated mapping, source C — 120 entries)
  data/mk_contacts.json official Knesset X/Twitter URLs, for cross-checking

Network (public APIs, no auth, no secrets):
  he.wikipedia.org/w/api.php  action=query, prop=extracts|pageprops|info,
                              exintro+explaintext -> lead abstract + QID + URL
  www.wikidata.org/w/api.php  wbgetentities, props=claims -> P2002 (X/Twitter
                              username), plus P2003/P2013/P7085 as bonus

Output: data/mk_wiki_abstracts.json, keyed by surname-first name:
  {wiki_title, wiki_url, wikidata_id, abstract,
   x_handles:[...], x_discovered: bool (no official Knesset X on file),
   x_matches_official: bool|None, instagram/facebook/tiktok (Wikidata)}

Licence: Wikipedia abstracts are CC BY-SA 4.0 — the UI must attribute and
link the article. Wikidata is CC0. Abstracts are stored verbatim (lead
section only), never paraphrased, so attribution stays honest.

Handles are validated against ^[A-Za-z0-9_]{1,15}$ before any URL is built
(CWE-20: untrusted API strings must not become hrefs unchecked). Every other
field is rendered through esc() in the UI.
Stdlib only.
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {"User-Agent": "mandat121-build/1.0 (https://mandat121.netlify.app; Wikipedia/Wikidata enrichment)"}
HEWIKI = "https://he.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
OUT = Path("data/mk_wiki_abstracts.json")
KNESSET_MEMBER_QID = "Q4047513"  # Wikidata P39 value "Knesset member" (verified 2026-11-10)
# Live-checked 2026-11-10: these Wikidata P2002 values return 404 on x.com
# (dead/renamed accounts; 50 of 52 discovered handles returned 200 that day).
# Hand-curation (source C): filtered out so the UI never links to a dead
# page. Recheck periodically — a rename, not malice, is the usual cause, and
# do NOT guess a replacement handle.
X_DEAD_HANDLES = {"moshesolomonr", "kahanamatan"}
# Same person, different surname-first spelling in the vote/tenure data vs
# the curated mapping. Hand-curated (source C) — deterministic, no guessing.
ALIASES = {
    "גנץ בנימין": "גנץ בני",
    "סטרוק אורית מלכה": "סטרוק אורית",
    "פינדרוס יצחק": "פינדרוס יצחק זאב",
    "מלקו צגה": "מלקו צגה צגנש",
    "סגלוביץ' יואב": "סגלוביץ יואב",
}


def get_json(base, params):
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(s):
    return " ".join(str(s or "").split())


def handle_from_url(url):
    """'https://twitter.com/#!/Foo?lang=he' -> 'foo' ('' if unparseable)."""
    u = str(url or "")
    if "#!/" in u:  # legacy hashbang profile URLs the Knesset still publishes
        u = u.split("#!/", 1)[1]
    tail = re.sub(r"[?#].*$", "", u).rstrip("/").split("/")[-1]
    tail = tail.lstrip("@").lower()
    return tail if HANDLE_RE.match(tail) else ""


def claim_values(entity, prop):
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        try:
            v = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def claim_ids(entity, prop):
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return out


def given_first(name):
    """surname-first key -> given-first display order (first token = surname)."""
    t = name.split()
    return " ".join(t[1:] + t[:1]) if len(t) > 1 else name


def main():
    mapping = {norm(k): norm(v) for k, v in json.loads(Path("data/mk_wiki.json").read_text(encoding="utf-8")).items()}
    contacts = {}
    cp = Path("data/mk_contacts.json")
    if cp.exists():
        contacts = {norm(k): v for k, v in json.loads(cp.read_text(encoding="utf-8")).get("by_name", {}).items()}

    # Universe of everyone who served in K25 (current 120 + former MKs), from
    # the tenure + per-MK vote-result layers. Names not in the curated mapping
    # are resolved below: alias -> search, but a search hit is only accepted
    # if Wikidata verifies the person actually held the Knesset-member
    # position (P39=Q4047513) — a same-named non-MK must never slip in.
    universe = set(mapping)
    tp = Path("data/k25_tenure.json")
    if tp.exists():
        tj = json.loads(tp.read_text(encoding="utf-8"))
        for sect in ("tenure", "factions"):
            universe.update(norm(k) for k in tj.get(sect, {}))
    vp = Path("data/k25_vote_results.json")
    if vp.exists():
        universe.update(norm(n) for n in json.loads(vp.read_text(encoding="utf-8")).get("names", []))
    universe.update(contacts)  # MkLobby spellings can differ from tenure/vote spellings

    # Knesset 26: everyone serving (open stints) in the official roster.
    # Same acceptance rules apply — a search hit needs Wikidata P39
    # verification or the abstract itself saying חבר/ת הכנסת, so a new MK
    # with no article (or a same-named non-MK) stays unresolved, honestly.
    tp26 = Path("data/k26_tenure.json")
    if tp26.exists():
        tj = json.loads(tp26.read_text(encoding="utf-8"))
        for sect in ("tenure", "factions"):
            universe.update(norm(k) for k in tj.get(sect, {}))
    cp26 = Path("data/k26_contacts.json")
    if cp26.exists():
        universe.update(norm(k) for k in
                        json.loads(cp26.read_text(encoding="utf-8")).get("by_name", {}))

    resolved = {}   # name -> (title, method)
    for n, t in mapping.items():
        resolved[n] = (t, "curated_mapping")
    for n, target in ALIASES.items():
        if n in universe and target in mapping and n not in resolved:
            resolved[n] = (mapping[target], "alias_of_mapped")
    search_candidates = {}  # name -> [candidate titles in rank order]
    for name in sorted(universe - set(resolved)):
        cands = []
        # Both orders: given-first rotation mangles multi-word surnames
        # (אבו אל היג'א, שאשא ביטון), the as-is order sometimes works better.
        for term in (given_first(name), name):
            d = get_json(HEWIKI, {"action": "query", "list": "search",
                                  "srsearch": term, "srnamespace": "0",
                                  "srlimit": "5", "format": "json", "formatversion": "2"})
            for x in d.get("query", {}).get("search", []):
                if norm(x["title"]) not in cands:
                    cands.append(norm(x["title"]))
        search_candidates[name] = cands

    # 1) Hebrew Wikipedia: lead abstract + Wikidata QID + canonical URL.
    # Batches of 20: the extracts module caps exlimit at 20 per query, so a
    # 50-title batch silently returns empty extracts for the rest.
    titles = list(dict.fromkeys(
        [t for t, _ in resolved.values()] +
        [t for cands in search_candidates.values() for t in cands]))
    by_title = {}
    for i in range(0, len(titles), 20):
        d = get_json(HEWIKI, {
            "action": "query", "prop": "extracts|pageprops|info", "inprop": "url",
            "exintro": "1", "explaintext": "1", "exlimit": "max", "redirects": "1",
            "titles": "|".join(titles[i:i + 20]), "ppprop": "wikibase_item",
            "format": "json", "formatversion": "2",
        })
        q = d.get("query", {})
        redir = {norm(x["from"]): norm(x["to"]) for x in q.get("redirects", [])}
        for p in q.get("pages", []):
            by_title[norm(p.get("title"))] = p
        for src, dst in redir.items():
            if dst in by_title:
                by_title[src] = by_title[dst]

    # 2) Wikidata: social handles for every QID found (50/batch)
    qids = sorted({p["pageprops"]["wikibase_item"] for p in by_title.values()
                   if p.get("pageprops", {}).get("wikibase_item")})
    entities = {}
    for i in range(0, len(qids), 50):
        d = get_json(WIKIDATA, {
            "action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
            "props": "claims", "format": "json", "formatversion": "2",
        })
        entities.update(d.get("entities", {}))

    out, missing_abstract, mismatches, unresolved = {}, [], [], []

    def make_rec(name, title, p, method):
        abstract = norm(p.get("extract"))
        qid = p.get("pageprops", {}).get("wikibase_item", "")
        ent = entities.get(qid, {})
        x_handles = [h for h in dict.fromkeys(
            h.lstrip("@") for h in claim_values(ent, "P2002"))
            if HANDLE_RE.match(h) and h.lower() not in X_DEAD_HANDLES]
        official = handle_from_url((contacts.get(name) or {}).get("twitter"))
        rec = {
            "wiki_title": p.get("title") or title,
            "wiki_url": p.get("canonicalurl") or f"https://he.wikipedia.org/wiki/{urllib.parse.quote(title)}",
            "wikidata_id": qid,
            "abstract": abstract,
            "resolution": method,
            "x_handles": x_handles,
            # discovered = Wikidata has an X handle the Knesset lobby doesn't publish
            "x_discovered": bool(x_handles) and not official,
            "x_matches_official": (official in [h.lower() for h in x_handles]) if (official and x_handles) else None,
            "instagram": claim_values(ent, "P2003"),
            "facebook": claim_values(ent, "P2013"),
            "tiktok": claim_values(ent, "P7085"),
        }
        if rec["x_matches_official"] is False:
            mismatches.append((name, official, x_handles))
        return rec

    for name, (title, method) in resolved.items():
        p = by_title.get(title)
        if not p or p.get("missing") or not norm(p.get("extract")):
            missing_abstract.append(name)
            continue
        out[name] = make_rec(name, title, p, method)

    def tokens(s):
        return {t for t in re.split(r"[\s\-־]+", s) if t}

    for name, cands in search_candidates.items():
        name_tokens = tokens(name)
        accepted = None
        for title in cands:
            p = by_title.get(title)
            if not p or p.get("missing") or not norm(p.get("extract")):
                continue
            qid = p.get("pageprops", {}).get("wikibase_item", "")
            ent = entities.get(qid, {})
            base = norm(re.sub(r"\s*\(.*\)\s*$", "", p.get("title") or title))
            # Compare against ALL name tokens (hyphen-split): token order and
            # multi-word surnames (שאשא ביטון, אבו אל-היג'א) then don't matter.
            overlap = len(name_tokens & tokens(base)) / max(len(name_tokens), 1)
            is_mk = KNESSET_MEMBER_QID in claim_ids(ent, "P39")
            if is_mk and overlap >= 0.5:
                accepted = (title, p, "search_p39_verified")
                break
            if overlap >= 0.75 and ("חבר הכנסת" in p["extract"] or "חברת הכנסת" in p["extract"]):
                accepted = (title, p, "search_abstract_verified")
                break
        if accepted:
            out[name] = make_rec(name, accepted[0], accepted[1], accepted[2])
        else:
            unresolved.append(name)

    payload = {
        "_meta": {
            "source": "he.wikipedia.org API (abstracts, CC BY-SA 4.0) + Wikidata wbgetentities P2002/P2003/P2013/P7085 (CC0)",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mapped": len(mapping), "universe": len(universe),
            "with_abstract": sum(bool(v["abstract"]) for v in out.values()),
            "by_resolution": {m: sum(v["resolution"] == m for v in out.values())
                              for m in sorted({v["resolution"] for v in out.values()})},
            "unresolved": unresolved,
            "with_wikidata_x": sum(bool(v["x_handles"]) for v in out.values()),
            "x_discovered_new": sum(v["x_discovered"] for v in out.values()),
            "x_discovered_current_roster": sum(v["x_discovered"] for k, v in out.items() if k in contacts),
            "x_mismatch_vs_knesset": len(mismatches),
            "missing_abstract": missing_abstract,
            "note": "x_discovered handles come from Wikidata, NOT the Knesset; UI must label provenance and never merge them into mk_contacts.json.",
        },
        "by_name": out,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {OUT} — abstracts {payload['_meta']['with_abstract']}/{len(universe)} universe "
          f"({payload['_meta']['by_resolution']}), "
          f"Wikidata X {payload['_meta']['with_wikidata_x']} "
          f"(new, not on Knesset site: {payload['_meta']['x_discovered_new']}), "
          f"mismatches {len(mismatches)}, missing {len(missing_abstract)}, unresolved {len(unresolved)}")
    for name, off, wd in mismatches:
        print(f"  MISMATCH {name}: knesset=@{off} wikidata={['@'+h for h in wd]}")
    for name in missing_abstract:
        print(f"  NO ABSTRACT {name} (title={resolved[name][0]!r})")
    for name in unresolved:
        print(f"  UNRESOLVED {name} (searched {given_first(name)!r}, candidates={search_candidates.get(name)})")


if __name__ == "__main__":
    main()
