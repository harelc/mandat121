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


def main():
    mapping = {norm(k): norm(v) for k, v in json.loads(Path("data/mk_wiki.json").read_text(encoding="utf-8")).items()}
    contacts = {}
    cp = Path("data/mk_contacts.json")
    if cp.exists():
        contacts = {norm(k): v for k, v in json.loads(cp.read_text(encoding="utf-8")).get("by_name", {}).items()}

    # 1) Hebrew Wikipedia: lead abstract + Wikidata QID + canonical URL.
    # Batches of 20: the extracts module caps exlimit at 20 per query, so a
    # 50-title batch silently returns empty extracts for the rest.
    titles = list(dict.fromkeys(mapping.values()))
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

    out, missing_abstract, mismatches = {}, [], []
    for name, title in mapping.items():
        p = by_title.get(title)
        if not p or p.get("missing"):
            missing_abstract.append(name)
            continue
        abstract = norm(p.get("extract"))
        qid = p.get("pageprops", {}).get("wikibase_item", "")
        ent = entities.get(qid, {})
        x_handles = [h for h in dict.fromkeys(
            h.lstrip("@") for h in claim_values(ent, "P2002")) if HANDLE_RE.match(h)]
        official = handle_from_url((contacts.get(name) or {}).get("twitter"))
        rec = {
            "wiki_title": p.get("title") or title,
            "wiki_url": p.get("canonicalurl") or f"https://he.wikipedia.org/wiki/{urllib.parse.quote(title)}",
            "wikidata_id": qid,
            "abstract": abstract,
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
        out[name] = rec

    payload = {
        "_meta": {
            "source": "he.wikipedia.org API (abstracts, CC BY-SA 4.0) + Wikidata wbgetentities P2002/P2003/P2013/P7085 (CC0)",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mapped": len(mapping), "with_abstract": sum(bool(v["abstract"]) for v in out.values()),
            "with_wikidata_x": sum(bool(v["x_handles"]) for v in out.values()),
            "x_discovered_new": sum(v["x_discovered"] for v in out.values()),
            "x_mismatch_vs_knesset": len(mismatches),
            "missing_abstract": missing_abstract,
            "note": "x_discovered handles come from Wikidata, NOT the Knesset; UI must label provenance and never merge them into mk_contacts.json.",
        },
        "by_name": out,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {OUT} — abstracts {payload['_meta']['with_abstract']}/{len(mapping)}, "
          f"Wikidata X {payload['_meta']['with_wikidata_x']} "
          f"(new, not on Knesset site: {payload['_meta']['x_discovered_new']}), "
          f"mismatches {len(mismatches)}, missing {len(missing_abstract)}")
    for name, off, wd in mismatches:
        print(f"  MISMATCH {name}: knesset=@{off} wikidata={['@'+h for h in wd]}")
    for name in missing_abstract:
        print(f"  NO ABSTRACT {name} (title={mapping[name]!r})")


if __name__ == "__main__":
    main()
