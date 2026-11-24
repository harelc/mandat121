#!/usr/bin/env python3
"""Human-readable summary of one data commit, for the notification issue.

The old version printed one row per file with byte/line deltas — useless
for minified one-line JSON (`k26_agenda.json 6,045 → 6,165 B, +1/-1,
committees 9 → 9 (+0)`) and actively misleading where one logical change
lands in several files (the agenda is written both to `k26_agenda.json`
and to its `k26_agenda/YYYY-MM.json` shard, so it was reported twice).

This version leads with *what happened in the Knesset*, derived from the
diff itself:

  ## What changed
  - 🏛️ New committee session: ועדת הכספים — 2026-11-18 09:30 …
  - 📜 New bill (K26): … — <status>

  ### ⚠️ Removals / shrinkage   (only when a dataset lost items — the
  byte table used to hide exactly the regressions worth an email)

  <details>Technical file table — friendly layer names instead of raw
  paths (path kept in small print), grouped shard/photo/state rows, byte
  deltas demoted here where they belong.</details>

Sources, in priority order: the semantic event stream the build itself
appends to `data/events.json` (new_bill / status_change / new_vote /
new_session), then added/removed/updated items in the other JSON layers
(identity by `id`-like keys), then portrait files resolved back to MK
names via `data/mk_photos.json`. Stdlib only.

Usage:
    python3 summarize_commit.py [ref]            # Markdown body (default HEAD)
    python3 summarize_commit.py [ref] --title    # one-line issue title
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

VOLATILE_KEYS = {"generated_at", "fetched_at", "_meta", "today", "this_week"}
MAX_HIGHLIGHTS = 15
MAX_DETAILS_PER_FILE = 6

# ---------------------------------------------------------------- labels
# Raw paths are for machines. Every layer gets a name a person would use;
# unknown files fall back to a prettified stem (never a bare hash/path).
LABELS = {
    "bills.json": "Bills (shared)", "agenda.json": "Agenda — sittings",
    "committees.json": "Knesset 25 — committees & sessions",
    "k25_bills.json": "Knesset 25 — bills", "k26_bills.json": "Knesset 26 — bills",
    "k25_votes.json": "Knesset 25 — plenum votes", "k26_votes.json": "Knesset 26 — plenum votes",
    "k25_vote_results.json": "Knesset 25 — per-MK vote results",
    "k26_vote_results.json": "Knesset 26 — per-MK vote results",
    "k25_analytics.json": "Knesset 25 — voting analytics (agreement & ranks)",
    "k25_committee_members.json": "Knesset 25 — committee membership",
    "k26_committee_members.json": "Knesset 26 — committee membership",
    "k25_committees.json": "Knesset 25 — committees & sessions",
    "k26_committees.json": "Knesset 26 — committees & sessions",
    "k25_tenure.json": "Knesset 25 — MK tenure (who served when)",
    "k26_tenure.json": "Knesset 26 — MK tenure (who is serving)",
    "k25_list_order.json": "Knesset 25 — election list order",
    "k26_list_order.json": "Knesset 26 — election list order",
    "k25_roles.json": "Knesset 25 — leadership roles", "k26_roles.json": "Knesset 26 — leadership roles",
    "k25_bill_history.json": "Knesset 25 — bill sponsor history",
    "k26_bill_history.json": "Knesset 26 — bill sponsor history",
    "k25_bill_docs.json": "Knesset 25 — bill documents",
    "k25_extras.json": "Knesset 25 — extra OData layers",
    "k25_build_meta.json": "Build metadata",
    "k26_agenda.json": "Knesset 26 — agenda (plenum & committee sittings)",
    "k26_contacts.json": "Knesset 26 — MK contacts & lobby presence",
    "mk_contacts.json": "Knesset 25 — MK contacts (frozen roster)",
    "k26_presence.json": "Knesset 26 — MK presence in the building",
    "k26_preview.json": "Knesset 26 — election results & preview",
    "k25_official_photos.json": "Knesset 25 — official portrait index",
    "k26_official_photos.json": "Knesset 26 — official portrait index",
    "mk_photos.json": "MK portrait index", "mk_wiki.json": "MK Wikipedia enrichment",
    "mk_wiki_abstracts.json": "MK Wikipedia abstracts",
    "mk_highlights.json": "MK speech highlights (what each MK talks about)",
    "events.json": "Event stream (new bills / votes / sessions, status changes)",
    "protocol_search.json": "Committee protocol search index",
    "k26_protocol_search.json": "Knesset 26 — committee protocol search index",
    "plenum_search.json": "Plenum speech (דברי הכנסת) search index",
    "k26_plenum_search.json": "Knesset 26 — plenum speech (דברי הכנסת) search index",
    "protocols_index.json": "Committee protocol index",
    "k26_protocols_index.json": "Knesset 26 — committee protocol index",
    "topics.json": "Topic taxonomy", "cmt_hl.json": "Committee speech highlights",
    "committee_attendance.json": "Committee attendance",
}
KEY_LABELS = {
    "by_mk": "MKs with assignments", "by_committee": "committees with member lists",
    "recent_sessions": "recent committee sessions", "committees": "committees",
    "plenum": "plenum sittings", "votes": "votes", "bills": "bills",
    "members": "members", "sessions": "sessions", "items": "items",
}
# Directories written as many small files: report as one grouped row,
# never one row per shard.
GROUP_DIRS = {
    "search_c": "Committee protocol search shards",
    "search_c26": "Knesset 26 — committee protocol search shards",
    "search_p": "Plenum speech search shards",
    "search_p26": "Knesset 26 — plenum speech search shards",
    "plenum_hl": "Plenum highlight shards",
    "protocols": "Committee protocol full texts",
    "photos": "MK portraits",
    "k26_agenda": "Knesset 26 — agenda month shards (same sittings as the agenda file above)",
    "agenda": "Agenda month shards (same sittings as the agenda file above)",
    "docs": "Bill documents",
}
INTERNAL_SUFFIX = "_state.json"  # builder bookkeeping, not site data
# Plenum-speech indexes are dicts keyed by sitting id ({sid: {number,
# date, doc, kn}}), unlike the list layers item_highlights() diffs — a
# brand-new sitting (with its full דברי הכנסת protocol behind it) shows
# up as one new key and was invisible to the summary: the 2026-11-16
# refresh that added K26 sitting 2246403 read "No new bills, votes or
# sessions — a contents/metadata refresh" (issue #36).
_PLENUM_SEARCH_RE = re.compile(r"^(?:k(\d+)_)?plenum_search\.json$")


def is_plenum_search(path):
    return bool(_PLENUM_SEARCH_RE.match(PurePosixPath(path).name))


def _plenum_search_kn(path, entry=None):
    if isinstance(entry, dict) and entry.get("kn"):
        return entry["kn"]
    m = _PLENUM_SEARCH_RE.match(PurePosixPath(path).name)
    return int(m.group(1)) if m and m.group(1) else 25


def friendly_label(path):
    p = PurePosixPath(path)
    if p.name in LABELS:
        return LABELS[p.name]
    if len(p.parts) > 1 and p.parts[-2] in GROUP_DIRS:
        return GROUP_DIRS[p.parts[-2]]
    stem = p.stem
    stem = re.sub(r"^k(25|26)_", r"Knesset \1 — ", stem).replace("_", " ")
    return stem[:1].upper() + stem[1:]


def is_internal(path):
    return path.endswith(INTERNAL_SUFFIX) or PurePosixPath(path).name == "events_state.json"


def is_grouped(path):
    p = PurePosixPath(path)
    return len(p.parts) > 2 and p.parts[-2] in GROUP_DIRS  # data/<dir>/<file>


# ---------------------------------------------------------------- git io
def _git(*args):
    return subprocess.check_output(["git", *args]).decode("utf-8", "replace")


def _blob(spec):
    r = subprocess.run(["git", "show", spec], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _json_at(ref, path):
    if ref is None:
        p = Path(path)
        raw = p.read_bytes() if p.is_file() else None
    else:
        raw = _blob(f"{ref}:{path}")
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def changed_files_from_baseline(baseline_dir, changed_entries):
    """Build the same [(status, path, old_b, new_b)] list from a baseline
    snapshot dir and the live working tree (for R2-only runs with no git commit)."""
    base = Path(baseline_dir)
    out = []
    for status, rel in changed_entries:
        old_p = base / rel
        new_p = Path(rel)
        old_b = old_p.read_bytes() if (status != "A" and old_p.is_file()) else None
        if old_b is None and status != "A":
            old_b = _blob(f"HEAD:{rel}")
        new_b = new_p.read_bytes() if (status != "D" and new_p.is_file()) else None
        out.append((status, rel, old_b, new_b))
    return out


def _parent(ref):
    ok = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^"],
                        capture_output=True).returncode == 0
    return _git("rev-parse", f"{ref}^").strip() if ok else None


def changed_files(ref):
    parent = _parent(ref)
    if parent is None:
        return None, []
    out = []
    for row in _git("diff", "--name-status", parent, ref).splitlines():
        parts = row.split("\t")
        if len(parts) >= 2:
            out.append((parts[0], parts[-1]))
    sizes = {}
    for status, path in out:
        old = _blob(f"{parent}:{path}") if status != "A" else None
        new = _blob(f"{ref}:{path}") if status != "D" else None
        sizes[path] = (status, old, new)
    return parent, [(s, p, *sizes[p][1:]) for s, p in out]


# ------------------------------------------------------- semantic diffing
def _item_key(item):
    if isinstance(item, dict):
        for k in ("id", "vote_id", "session_id", "bill_id", "item_id"):
            if item.get(k) is not None:
                return f"{k}={item[k]}"
        if item.get("name"):
            return f"name={item['name']}"
        return json.dumps(item, ensure_ascii=False, sort_keys=True)[:120]
    return f"v={item}"


def _fmt_dt(s):
    return str(s or "")[:16].replace("T", " ")


def item_label(item):
    if not isinstance(item, dict):
        return str(item)[:80]
    name = item.get("name") or item.get("title") or ""
    if item.get("committee"):  # a sitting: committee + when + first topic
        topic = ""
        items = item.get("items") or []
        if items and isinstance(items[0], dict):
            topic = items[0].get("name") or ""
        label = f"{item['committee']} — {_fmt_dt(item.get('start') or item.get('date'))}"
        return f"{label} · {topic[:70]}" if topic else label
    if name:
        when = _fmt_dt(item.get("date") or item.get("start"))
        tally = ""
        if item.get("for") is not None and item.get("against") is not None:
            tally = f" — {item['for']} for / {item['against']} against"
        return f"{name[:80]}{f' ({when})' if when else ''}{tally}"
    if item.get("for") is not None:  # a vote with no title on the item itself
        return f"vote {_fmt_dt(item.get('date'))} — {item['for']} for / {item['against']} against"
    return json.dumps(item, ensure_ascii=False)[:80]


def _diff_items(old_list, new_list):
    """(added, removed, updated) item lists, identity by id-like keys."""
    old_by = {_item_key(x): x for x in old_list}
    new_by = {_item_key(x): x for x in new_list}
    added = [new_by[k] for k in new_by if k not in old_by]
    removed = [old_by[k] for k in old_by if k not in new_by]
    updated = [new_by[k] for k in new_by if k in old_by and new_by[k] != old_by[k]]
    return added, removed, updated


def _change_note(old_item, new_item):
    """For an updated item, the one field change worth naming, if any."""
    if isinstance(old_item, dict) and isinstance(new_item, dict):
        for k in ("status", "status_id", "name", "status_text"):
            if old_item.get(k) != new_item.get(k) and old_item.get(k) and new_item.get(k):
                return f"{old_item.get('name') or old_item.get('title') or ''}: {old_item[k]} → {new_item[k]}"[:110]
    return ""


def semantic_detail(old_bytes, new_bytes, path=""):
    """Short fragments describing a JSON file's change ([] if unparsable)."""
    if old_bytes is None:
        return ["new file"]
    if new_bytes is None:
        return ["deleted"]
    try:
        old, new = json.loads(old_bytes), json.loads(new_bytes)
    except Exception:
        return []
    if is_plenum_search(path) and isinstance(old, dict) and isinstance(new, dict):
        added = [k for k in new if k not in old]
        removed = [k for k in old if k not in new]
        parts = []
        if added:
            parts.append(f"+{len(added)} sitting(s): " + "; ".join(
                f"{k} ({new[k].get('date', '?')})" if isinstance(new[k], dict) else str(k)
                for k in added[:3]))
        if removed:
            parts.append(f"−{len(removed)} sitting(s): " + ", ".join(removed[:3]))
        changed = [k for k in new if k in old and new[k] != old[k]]
        if changed and not added and not removed:
            parts.append(f"{len(changed)} sitting(s) updated")
        return parts or ["contents changed"]
    parts = []
    if isinstance(old, list) and isinstance(new, list):
        added, removed, updated = _diff_items(old, new)
        if added:
            parts.append(f"+{len(added)}: " + "; ".join(item_label(x) for x in added[:3]))
        if removed:
            parts.append(f"−{len(removed)}: " + "; ".join(item_label(x) for x in removed[:3]))
        if updated and not added and not removed:
            old_by = {_item_key(x): x for x in old}
            notes = [n for n in (_change_note(old_by.get(_item_key(x), {}), x) for x in updated[:3]) if n]
            parts.append(f"{len(updated)} updated" + (f" ({'; '.join(notes)})" if notes else ""))
        return parts or ["contents changed"]
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key in VOLATILE_KEYS or old.get(key) == new.get(key):
                continue
            o, n = old.get(key), new.get(key)
            label = KEY_LABELS.get(key, key)
            if isinstance(o, list) and isinstance(n, list):
                added, removed, updated = _diff_items(o, n)
                bits = [f"{label} {len(o):,} → {len(n):,}"]
                if added:
                    bits.append("+" + "; ".join(item_label(x) for x in added[:2]))
                if removed:
                    bits.append("−" + "; ".join(item_label(x) for x in removed[:2]))
                if updated and not added and not removed:
                    bits.append(f"{len(updated)} updated")
                parts.append(" · ".join(bits))
            elif isinstance(o, dict) and isinstance(n, dict):
                ak, rk = [k for k in n if k not in o], [k for k in o if k not in n]
                bits = [f"{label} {len(o):,} → {len(n):,}"]
                if ak:
                    bits.append("+ " + ", ".join(str(k) for k in ak[:4]))
                if rk:
                    bits.append("− " + ", ".join(str(k) for k in rk[:4]))
                parts.append(" · ".join(bits))
            elif isinstance(o, (int, float, str)) and isinstance(n, (int, float, str)):
                parts.append(f"{key}: {str(o)[:40]} → {str(n)[:40]}")
            else:
                parts.append(f"{label} changed")
            if len(parts) >= MAX_DETAILS_PER_FILE:
                break
        return parts or ["contents changed"]
    return ["contents changed"] if old != new else []


# ------------------------------------------------------------- highlights
def _bill_names(ref, kn):
    rows = _json_at(ref, f"data/k{kn}_bills.json") or []
    return {str(b.get("id")): b.get("name") for b in rows if isinstance(b, dict)}


def event_highlights(ref, files):
    """Format events the build appended to data/events.json in this commit."""
    entry = next((f for f in files if f[1] == "data/events.json"), None)
    if not entry:
        return [], set()
    _, _, old_b, new_b = entry
    try:
        old = {_item_key(e): e for e in json.loads(old_b or b"[]")}
        new = json.loads(new_b or b"[]")
    except Exception:
        return [], set()
    added = [e for e in new if _item_key(e) not in old]
    names = {25: _bill_names(ref, 25), 26: _bill_names(ref, 26)}
    out, session_ids = [], set()
    for e in added:
        kn, t, d = e.get("kn"), e.get("type"), e.get("date", "")
        if t == "new_bill":
            bill_name = e.get('name') or names.get(kn, {}).get(str(e.get('bill_id')), e.get('bill_id'))
            status = e.get('status')
            out.append(("bill", f"📜 New bill (K{kn}): {bill_name}" + (f" — {status}" if status else "")))
        elif t == "new_bill_doc":
            docs = ", ".join(e.get("docs") or [])
            out.append(("bill", f"📄 New bill document (K{kn}): {e.get('name') or e.get('bill_id')}" + (f" — {docs}" if docs else "")))
        elif t == "status_change":
            out.append(("bill", f"📜 Bill status (K{kn}): {e.get('name') or ''} — {e.get('from')} → {e.get('to')}"))
        elif t == "new_vote":
            bill = names.get(kn, {}).get(str(e.get("item_id")))
            out.append(("vote", f"🗳️ New plenum vote (K{kn}, {d}){f': {bill}' if bill else ''}"
                                f" — {e.get('for')} for / {e.get('against')} against"))
        elif t == "new_session":
            session_ids.add(str(e.get("session_id")))
            out.append(("session", f"🏛️ New committee session: {e.get('committee') or e.get('committee_id')} — {d}"))
    return out, session_ids


def plenum_highlights(files):
    """New sittings in the dict-keyed plenum-speech indexes.

    A new key there is a whole sitting whose דברי הכנסת protocol the
    site can now open (the plenum_hl shard and the search_p* shards in
    the same commit are just its index by-products — not separate
    news). A whole index file appearing at once is capped the same
    way: four sittings named, the rest counted.
    """
    out = []
    for _, path, old_b, new_b in files:
        if not is_plenum_search(path) or new_b is None:
            continue
        try:
            old = json.loads(old_b) if old_b else {}
            new = json.loads(new_b)
        except Exception:
            continue
        if not isinstance(new, dict):
            continue
        added = sorted((k for k in new if k not in (old or {})), key=lambda k: str((new[k] or {}).get("date") or k))
        for sid in added[:4]:
            e = new[sid] if isinstance(new[sid], dict) else {}
            kn = _plenum_search_kn(path, e)
            num = f" #{e['number']}" if e.get("number") else ""
            out.append(("plenum", f"🎙️ New plenum sitting (K{kn}){num} — {e.get('date') or ''} "
                                  f"(sitting {sid}; דברי הכנסת protocol published)"))
        if len(added) > 4:
            out.append(("plenum", f"🎙️ …and {len(added) - 4} more new plenum sittings"))
    return out


def portrait_highlights(ref, files):
    photos = [f for f in files if is_grouped(f[1]) and PurePosixPath(f[1]).parts[-2] == "photos"]
    if not photos:
        return []
    by_file = {}
    index = _json_at(ref, "data/mk_photos.json") or {}
    by_file.update({v.get("photo_url"): n for n, v in index.items() if isinstance(v, dict)})
    for kn in (25, 26):  # official-portrait indexes use {photos: {name: {file}}}
        idx = _json_at(ref, f"data/k{kn}_official_photos.json") or {}
        by_file.update({v.get("file"): n for n, v in (idx.get("photos") or {}).items()
                        if isinstance(v, dict)})
    out = []
    for _, path, _, _ in photos:
        name = by_file.get(path)
        out.append(("portrait", f"🖼️ Portrait {'for ' + name if name else 'image'} updated"))
    return out[:4]


def _committee_names(ref):
    names = {}
    for path in ("data/k26_committees.json", "data/committees.json"):
        d = _json_at(ref, path) or {}
        for c in d.get("committees") or []:
            if isinstance(c, dict) and c.get("id") is not None:
                names[str(c["id"])] = c.get("name")
        for s in d.get("recent_sessions") or []:
            if isinstance(s, dict) and s.get("committee_id") is not None:
                names.setdefault(str(s["committee_id"]), s.get("committee"))
    return names


def item_highlights(ref, files, skip_session_ids):
    """Added items from non-event layers (agenda, committees, bills…)."""
    out, seen_ids = [], set(skip_session_ids)
    for status, path, old_b, new_b in files:
        name = PurePosixPath(path).name
        if (name == "events.json" or is_internal(path) or is_grouped(path)
                or name.endswith("_state.json")):
            continue
        if old_b is None or new_b is None:
            if status == "A":
                out.append(("other", f"🆕 New layer: {friendly_label(path)}"))
            continue
        try:
            old, new = json.loads(old_b), json.loads(new_b)
        except Exception:
            continue
        lists = []
        if isinstance(old, list) and isinstance(new, list):
            lists = [("", old, new)]
        elif isinstance(old, dict) and isinstance(new, dict):
            lists = [(k, old.get(k), new.get(k)) for k in set(old) | set(new)
                     if isinstance(old.get(k), list) and isinstance(new.get(k), list)]
            # Dict layers (committee membership!): new keys are new
            # committees (by id) or newly-assigned MKs (by name).
            committees = _committee_names(ref)
            for k in set(old) | set(new):
                o, n = old.get(k), new.get(k)
                if not (isinstance(o, dict) and isinstance(n, dict)):
                    continue
                added_keys = [x for x in n if x not in o]
                if not added_keys:
                    continue
                shown = [committees.get(str(x), str(x)) if k == "by_committee" else str(x)
                         for x in added_keys[:4]]
                out.append(("item", f"👥 {friendly_label(path)}: +{len(added_keys)} "
                                    f"{KEY_LABELS.get(k, k)} ({', '.join(shown)})"))
        for key, o, n in lists:
            added, _, _ = _diff_items(o, n)
            for item in added[:4]:
                if not isinstance(item, dict):
                    continue  # scalar lists (month names etc.) are not news
                if str(item.get("id")) in seen_ids:
                    continue  # already reported (event stream, or agenda+committees both carry a sitting)
                if item.get("id") is not None:
                    seen_ids.add(str(item["id"]))
                icon = "🏛️" if (item.get("committee") or key in ("recent_sessions", "committees", "plenum")) else \
                       "🗳️" if (item.get("for") is not None) else "➕"
                out.append(("item", f"{icon} {friendly_label(path)}: + {item_label(item)}"))
    return out


def shrink_warnings(files):
    """Any dataset that lost items — the regression signal worth an email."""
    out = []
    for _, path, old_b, new_b in files:
        if old_b is None or new_b is None or is_internal(path) or is_grouped(path):
            continue
        try:
            old, new = json.loads(old_b), json.loads(new_b)
        except Exception:
            continue
        if "agenda" in path or path.endswith("events.json"):
            continue  # windowed / capped layers: items leave by design, not by loss
        if is_plenum_search(path) and isinstance(old, dict) and isinstance(new, dict):
            removed = [k for k in old if k not in new]
            if removed:
                out.append(f"- {friendly_label(path)}: −{len(removed)} sitting(s) "
                           f"({', '.join(removed[:3])})")
            continue
        pairs = [("", old, new)] if isinstance(old, list) else \
            [(k, old.get(k), new.get(k)) for k in (old or {})
             if isinstance(old, dict) and k not in VOLATILE_KEYS
             and k in ("bills", "votes", "committees", "members")]
        for key, o, n in pairs:
            if isinstance(o, list) and isinstance(n, list):
                _, removed, _ = _diff_items(o, n)
                if removed:
                    out.append(f"- {friendly_label(path)}"
                               f"{f' · {KEY_LABELS.get(key, key)}' if key else ''}: "
                               f"−{len(removed)} ({'; '.join(item_label(x) for x in removed[:3])})")
    return out


def _collect_from(ref, files):
    ev, sess_ids = event_highlights(ref, files)
    highlights = ev + portrait_highlights(ref, files) + plenum_highlights(files) \
        + item_highlights(ref, files, sess_ids)
    seen, uniq = set(), []
    for kind, text in highlights:  # agenda + committees can both add a sitting
        k = re.sub(r"[^0-9A-Za-zא-ת ]", "", text)[:90]
        if k not in seen:
            seen.add(k)
            uniq.append((kind, text))
    return files, uniq, shrink_warnings(files)


def collect(ref):
    parent, files = changed_files(ref)
    if parent is None:
        return None, [], [], []
    files, uniq, warnings = _collect_from(ref, files)
    return files, uniq, warnings, parent


# ---------------------------------------------------------------- output
def _format_summary(header, files, highlights, warnings):
    lines = [header, "", "## What changed", ""]
    if highlights:
        lines += [f"- {t}" for _, t in highlights[:MAX_HIGHLIGHTS]]
        if len(highlights) > MAX_HIGHLIGHTS:
            lines.append(f"- …and {len(highlights) - MAX_HIGHLIGHTS} more (see file table)")
    else:
        named = [friendly_label(p) for _, p, _, _ in files if not is_internal(p)]
        lines.append("No new bills, votes or sessions in this update — a contents/metadata "
                     "refresh of: " + (", ".join(dict.fromkeys(named[:6])) or "internal files") + ".")
    if warnings:
        lines += ["", "### ⚠️ Removals / shrinkage", ""] + warnings[:8]
    lines += ["", f"**{len(files)} file(s) changed**", ""]

    rows, groups = [], {}
    for status, path, old_b, new_b in files:
        key = None
        if is_grouped(path):
            key = ("group", PurePosixPath(path).parts[-2])
        elif is_internal(path):
            key = ("internal", "state")
        if key:
            g = groups.setdefault(key, {"label": "Builder bookkeeping (internal state files)"
                                        if key[0] == "internal" else GROUP_DIRS[key[1]],
                                        "n": 0, "delta": 0, "paths": []})
            g["n"] += 1
            g["delta"] += (len(new_b or b"") - len(old_b or b""))
            g["paths"].append(path)
            continue
        detail = "; ".join(semantic_detail(old_b, new_b, path))[:220]
        size = (f"{len(old_b or b''):,} → {len(new_b or b''):,} B ({len(new_b or b'') - len(old_b or b''):+,})"
                if old_b is not None and new_b is not None else f"{len(new_b or old_b or b''):,} B")
        rows.append(f"| {friendly_label(path)} | `{path}` | {status} | {size} | {detail} |")
    for g in groups.values():
        rows.append(f"| {g['label']} | `{g['paths'][0]}` … ({g['n']} file(s)) | M | "
                    f"{g['delta']:+,} B total | grouped — see run logs for individual files |")

    lines += ["<details><summary>Technical file table (sizes, grouped shards)</summary>", "",
              "| Layer | File | Change | Size | Detail |", "|---|---|---|---|---|"]
    lines += rows[:60]
    if len(rows) > 60:
        lines.append(f"\n_…and {len(rows) - 60} more row(s)._")
    lines += ["</details>"]
    return "\n".join(lines)


def _format_title(date, files, highlights, warnings):
    counts = {}
    for kind, text in highlights:
        label = {"bill": None, "vote": "new plenum vote(s)", "session": "new committee session(s)",
                 "plenum": "new plenum sitting(s)", "portrait": "portrait update(s)"}.get(kind)
        if label:
            counts[label] = counts.get(label, 0) + 1
        elif text.startswith("📜 New bill"):
            counts["new bill(s)"] = counts.get("new bill(s)", 0) + 1
        elif "status" in text:
            counts["bill status change(s)"] = counts.get("bill status change(s)", 0) + 1
    parts = [f"{n} {label.replace('(s)', 's' if n > 1 else '')}" for label, n in list(counts.items())[:3]]
    if warnings:
        parts.append("⚠️ shrinkage")
    head = ", ".join(parts) if parts else f"contents updated ({len(files)} file(s))"
    return f"data refresh {date} — {head}"


def summarize(ref="HEAD"):
    files, highlights, warnings, parent = collect(ref)
    subject = _git("log", "-1", "--format=%s", ref).strip()
    header = f"**Commit:** `{ref}` — {subject}"
    if parent is None:
        return "\n".join([header, "", "(no parent commit to diff against)"])
    return _format_summary(header, files, highlights, warnings)


def title_for(ref="HEAD"):
    date = _git("log", "-1", "--format=%cs", ref).strip()
    files, highlights, warnings, _ = collect(ref)
    return _format_title(date, files, highlights, warnings)


def summarize_baseline(baseline_dir, changed_entries):
    files = changed_files_from_baseline(baseline_dir, changed_entries)
    files, highlights, warnings = _collect_from(None, files)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _format_summary(
        f"**R2 data refresh:** `{date}` (published directly to Cloudflare R2 — no Netlify deploy)",
        files, highlights, warnings,
    )


def title_for_baseline(baseline_dir, changed_entries):
    files = changed_files_from_baseline(baseline_dir, changed_entries)
    files, highlights, warnings = _collect_from(None, files)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _format_title(date, files, highlights, warnings)


if __name__ == "__main__":
    if "--baseline" in sys.argv:
        bi = sys.argv.index("--baseline")
        ci = sys.argv.index("--changed")
        bdir = sys.argv[bi + 1]
        entries = json.loads(Path(sys.argv[ci + 1]).read_text(encoding="utf-8"))
        print(title_for_baseline(bdir, entries) if "--title" in sys.argv else summarize_baseline(bdir, entries))
    else:
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        ref = args[0] if args else "HEAD"
        print(title_for(ref) if "--title" in sys.argv else summarize(ref))
