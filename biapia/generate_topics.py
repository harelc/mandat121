#!/usr/bin/env python3
"""
Assign 1-3 topic keywords to each bill from a fixed taxonomy of 15 topics.
Uses Claude API for classification, with parallel workers.

Outputs topics.json cache and merges into bills.json.
"""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
BILLS_PATH = DATA_DIR / "bills.json"
TOPICS_PATH = DATA_DIR / "topics.json"

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 10  # bills per API call
WORKERS = 10  # parallel API calls
SAVE_EVERY = 100  # save cache every N classified bills

TAXONOMY = [
    "ביטחון ומלחמה",
    "כלכלה ותקציב",
    "חינוך",
    "בריאות",
    "משפט ועונשין",
    "דת ומדינה",
    "זכויות אזרח",
    "רווחה וביטוח לאומי",
    "תחבורה ותשתיות",
    "סביבה ואקלים",
    "עבודה ותעסוקה",
    "ממשל ודמוקרטיה",
    "חברה ומיעוטים",
    'דיור ונדל"ן',
    "טכנולוגיה ומידע",
]

TAXONOMY_STR = "\n".join(f"{i+1}. {t}" for i, t in enumerate(TAXONOMY))


def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def classify_batch(client, bills_batch):
    """Classify a batch of bills in one API call."""
    bill_list = "\n".join(
        f"[{b['id']}] {b['name']}" + (f" — {b['synopsis'][:150]}" if b.get('synopsis') else "")
        for b in bills_batch
    )

    prompt = f"""סווג כל הצעת חוק ל-1-3 נושאים מהרשימה הבאה (ציין את המספרים בלבד):

נושאים:
{TAXONOMY_STR}

הצעות חוק:
{bill_list}

עבור כל הצעה, החזר שורה בפורמט:
[ID] 1,5,7

ללא הסברים, רק ID ומספרי נושאים מופרדים בפסיקים."""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  API error: {e}")
        return ""


def parse_response(text, valid_ids):
    """Parse the classification response into {bill_id: [topic_names]}."""
    results = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "[" not in line:
            continue
        try:
            id_part = line.split("]")[0].replace("[", "").strip()
            nums_part = line.split("]")[1].strip()
            nums = [int(n.strip()) for n in nums_part.split(",") if n.strip().isdigit()]
            topics = [TAXONOMY[n - 1] for n in nums if 1 <= n <= len(TAXONOMY)]
            if id_part in valid_ids and topics:
                results[id_part] = topics[:3]
        except (ValueError, IndexError):
            continue
    return results


def process_batch(client, batch):
    """Classify one batch and return parsed results."""
    valid_ids = {str(b["id"]) for b in batch}
    resp_text = classify_batch(client, batch)
    if resp_text:
        return parse_response(resp_text, valid_ids)
    return {}


def main():
    bills = json.loads(BILLS_PATH.read_text("utf-8"))
    print(f"Loaded {len(bills)} bills")

    # Load existing topics cache
    if TOPICS_PATH.exists():
        all_topics = json.loads(TOPICS_PATH.read_text("utf-8"))
        print(f"Loaded {len(all_topics)} cached topic assignments")
    else:
        all_topics = {}

    # Filter to bills that need topics
    need_topics = [b for b in bills if str(b["id"]) not in all_topics]
    print(f"Bills needing topics: {len(need_topics)}")

    if not need_topics:
        print("All bills already have topics!")
    else:
        api_key = get_api_key()
        if not api_key:
            print("No API key, cannot classify")
            return

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        print(f"Claude API ready ({WORKERS} workers, batch size {BATCH_SIZE})")

        # Split into batches
        batches = [need_topics[i:i + BATCH_SIZE] for i in range(0, len(need_topics), BATCH_SIZE)]
        classified = 0

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(process_batch, client, batch): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                parsed = future.result()
                all_topics.update(parsed)
                classified += len(parsed)

                if classified % SAVE_EVERY < BATCH_SIZE:
                    TOPICS_PATH.write_text(json.dumps(all_topics, ensure_ascii=False, indent=2), "utf-8")
                    print(f"  {classified}/{len(need_topics)} classified, saved cache", flush=True)
                elif classified % 50 < BATCH_SIZE:
                    print(f"  {classified}/{len(need_topics)} classified...", flush=True)

    # Save final cache
    TOPICS_PATH.write_text(json.dumps(all_topics, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nTotal topic assignments: {len(all_topics)}")

    # Merge into bills.json
    merged = 0
    for bill in bills:
        bid = str(bill["id"])
        if bid in all_topics:
            bill["topics"] = all_topics[bid]
            merged += 1

    BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")
    print(f"Merged topics into {merged} bills")

    # Print distribution
    from collections import Counter
    topic_counts = Counter()
    for topics in all_topics.values():
        for t in topics:
            topic_counts[t] += 1
    print("\nTopic distribution:")
    for topic, count in topic_counts.most_common():
        print(f"  {topic}: {count}")


if __name__ == "__main__":
    main()
