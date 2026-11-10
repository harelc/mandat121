#!/usr/bin/env python3
"""Global protocol search index (Sprint 1B, sharded 2026-11-10).

Reads the parsed committee protocols (data/protocols/*.json, 15M+ chars —
too much to ship/search raw in the browser) and emits:

  data/protocol_search.json — {sid: {committee, date}}  metadata only.
  data/search_c/<c0>_<c1>.json — sharded inverted index:
      {word: "sid:count,delta:count,…"} for EVERY content word (stopwords
      removed), sharded by the word's first two characters (named by
      char code, "1506_1511.json", so filenames stay ASCII). The old
      top-120-words-per-protocol index silently missed any word that
      wasn't among a sitting's 120 most frequent — a word said twice in
      a long sitting was unfindable. A single all-words file would run
      to tens of MB; sharding means the browser fetches only the few
      shards a query's prefixes can start with (~tens of KB, cached).
  protocols_index.json      — enriched in place per sid with
                              {turns, speakers, committee, date}
                              for richer session cards.

(Word clouds were built on top of this in 1B and removed the same night —
owner call: frequency clouds were noise, not insight. Do not re-add.)

Hebrew notes: tokens are stripped of surrounding punctuation/quotes;
prefix particles (ב/ל/מ/ה/ו/כ/ש) are NOT stemmed — prefix search in the UI
covers the common variants without a stemmer's false merges.
"""
import json
import re
import shutil
from collections import Counter
from pathlib import Path

PROTO_DIR = Path("data/protocols")
SEARCH_C_DIR = Path("data/search_c")
SEARCH_P_DIR = Path("data/search_p")


def shard_filename(word):
    """ASCII shard name from the word's first two char codes.

    Must match the UI's fetchShard(): `${charCodeAt(0)}_${charCodeAt(1)}`.
    Words are >=3 chars (see words_of), so two chars always exist."""
    return f"{ord(word[0])}_{ord(word[1])}.json"


def load_shard(shard_dir, filename, cache):
    if filename not in cache:
        p = shard_dir / filename
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        # On disk postings are delta-encoded strings; in memory [[sid, c]].
        cache[filename] = {w: decode_postings(enc) for w, enc in raw.items()}
    return cache[filename]


def decode_postings(enc):
    postings, sid = [], 0
    if isinstance(enc, str):
        for part in enc.split(","):
            d, c = part.split(":")
            sid += int(d)
            postings.append([sid, int(c)])
        return postings
    return enc


def add_counts_to_shards(shard_dir, sid, counts, cache, dirty):
    """Merge one document's word counts into the on-disk shards (in memory).

    Used incrementally by build_plenum_search.py; flush with flush_shards()."""
    for w, c in counts.items():
        fn = shard_filename(w)
        shard = load_shard(shard_dir, fn, cache)
        shard.setdefault(w, []).append([int(sid), c])
        dirty.add(fn)


def encode_postings(postings):
    """[[sid, count], …] -> "sid:count,delta:count,…" (delta from the
    previous sid). Session ids are dense, so deltas run 1–4 digits where
    the raw id is 7 — this nearly halves shard size vs JSON pairs.
    Must match the decoder in the UI's searchCorpus()."""
    parts, prev = [], 0
    for sid, c in sorted(postings):
        parts.append(f"{sid - prev}:{c}")
        prev = sid
    return ",".join(parts)


def flush_shards(shard_dir, cache, dirty):
    shard_dir.mkdir(parents=True, exist_ok=True)
    for fn in sorted(dirty):
        words = cache[fn]
        out = {w: encode_postings(postings)
               for w, postings in sorted(words.items())}
        (shard_dir / fn).write_text(
            json.dumps(out, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")


def write_search_shards(shard_dir, counters_by_sid):
    """Full rebuild of a shard directory from {sid: Counter}. Returns
    (shard files, total postings)."""
    if shard_dir.exists():
        shutil.rmtree(shard_dir)
    cache, dirty = {}, set()
    postings = 0
    for sid in sorted(counters_by_sid, key=lambda s: int(s)):
        add_counts_to_shards(shard_dir, sid, counters_by_sid[sid], cache, dirty)
        postings += len(counters_by_sid[sid])
    flush_shards(shard_dir, cache, dirty)
    return len(dirty), postings

STOP = set("""של על אל עם כי אם או גם רק לא מה זה את אני הוא היא אנחנו הם הן
יש אין היה היתה היו להיות אחד אחת שני כל כך אבל כאשר לפי אחר אחרי לפני תוך כדי
בין עבור נגד בעד אותו אותה אותם אותן הזה הזאת האלה אלו מי מהו מהי אינו אינה אינם
הואיל מאחר שבו שבה הזהו כן לו לה להם להן בו בה בם בהם לי לך לנו לכם ממנו ממנה
אז עוד היי שלום תודה בבקשה אוקיי רגע הנה שם פה כאן איך למה כמה איזה איזו אילו
יו"ר יושב ראש חבר חברת הכנסת חברים ועדה ישיבה הצעת חוק
אתה אתם אתן אנחנו אתהן צריך צריכה צריכים רוצה רוצים רוצות מאוד יכול יכולים יכולה
אומר אומרת אומרים אמר אומרים להגיד אדוני גברתי בבקשה סליחה רגע רגע אחד רגע שנייה רגע
שהוא שהיא שהם נו טוב בסדר אה אוהו רגעי רגע כן כן נכון אמת דבר דברים עניין עניינים
אנשים אדם מישהו משהו כזה כזאת כאלה באמת למעשה בעצם אולי יודע יודעת יודעים שזה זאת
נותן נותנת לתת עושה עושים עושות עשיתי עשינו עשה עשתה עשו הולך הולכים בא באים
אותי אותך אותנו אלי אליי אליך אליכם שלנו שלכם שלהם אליו אליה אליי מקום זמן יום היום
שני שנייה שלוש ארבע חמש ראשון שני שלישי רביעי חמישי שישי
עכשיו יותר שלא שיש אפשר יהיה כמו לעשות האם חושב חושבת פעם כבר הרבה לגבי בכל וזה לזה קודם
שאני שאנחנו שאתה שלאנחנו ולא ואני וגם שלו שלי שלכם שלנו אלינו עליו עליה עלינו עליהם
יהיה תהיה יהיו נהיה צריך שיכול שיהיה שצריך שגם שכן שלא שזאת שזו מזה בזה את זה על זה
דובר היו"ר המשך קודם כל בסדר גמור אוקיי רגע בבקשה תודה רבה סליחה רבותיי חבריי
אחת מהם מהן ממנו מזה מיד ממש טיפה קצת הרבה פעמים שנה שנים חודש שבוע ימים שעות דקות""".split())
TOKEN = re.compile(r"[א-תa-zA-Z0-9\"״']+")


def words_of(text):
    out = []
    for tok in TOKEN.findall(text.lower()):
        tok = tok.strip("\"'״")
        if len(tok) < 3 or tok in STOP or tok.isdigit():
            continue
        out.append(tok)
    return out


def main():
    search, index = {}, {}
    try:
        index = json.load(open("data/protocols_index.json", encoding="utf-8"))
    except Exception:
        index = {}
    files = sorted(PROTO_DIR.glob("*.json"))
    counters = {}
    for f in files:
        doc = json.load(open(f, encoding="utf-8"))
        sid = str(doc["id"])
        counts = Counter()
        for t in doc["turns"]:
            if t["role"] in ("speaker", "chair", "guest"):
                counts.update(words_of(t["text"]))
        counters[sid] = counts
        search[sid] = {"committee": doc.get("committee", ""), "date": doc.get("date", "")}
        entry = index.get(sid, {})
        entry.pop("top_words", None)  # word clouds removed — purge stale keys
        entry.update({"turns": len(doc["turns"]), "speakers": len(doc.get("speakers", {})),
                      "committee": doc.get("committee", ""), "date": doc.get("date", "")})
        index[sid] = entry
    n_shards, n_postings = write_search_shards(SEARCH_C_DIR, counters)
    Path("data/protocol_search.json").write_text(
        json.dumps(search, ensure_ascii=False), encoding="utf-8")
    Path("data/protocols_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {len(search)} protocols indexed "
          f"({n_postings} word postings in {n_shards} shards -> {SEARCH_C_DIR})")


if __name__ == "__main__":
    main()
