#!/usr/bin/env python3
"""Global protocol search index (Sprint 1B).

Reads the parsed committee protocols (data/protocols/*.json, 15M+ chars —
too much to ship/search raw in the browser) and emits:

  data/protocol_search.json — {sid: {committee, date,
                              words: {word: count}}}  top-N content words
                              per protocol, stopwords removed. The UI
                              matches a query word against these keys
                              (exact or prefix) and ranks by count.
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
from collections import Counter
from pathlib import Path

PROTO_DIR = Path("data/protocols")
TOP_N = 120

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
    for f in files:
        doc = json.load(open(f, encoding="utf-8"))
        sid = str(doc["id"])
        counts = Counter()
        for t in doc["turns"]:
            if t["role"] in ("speaker", "chair", "guest"):
                counts.update(words_of(t["text"]))
        top = counts.most_common(TOP_N)
        search[sid] = {"committee": doc.get("committee", ""), "date": doc.get("date", ""),
                       "words": dict(top)}
        entry = index.get(sid, {})
        entry.pop("top_words", None)  # word clouds removed — purge stale keys
        entry.update({"turns": len(doc["turns"]), "speakers": len(doc.get("speakers", {})),
                      "committee": doc.get("committee", ""), "date": doc.get("date", "")})
        index[sid] = entry
    Path("data/protocol_search.json").write_text(
        json.dumps(search, ensure_ascii=False), encoding="utf-8")
    Path("data/protocols_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {len(search)} protocols indexed")


if __name__ == "__main__":
    main()
