#!/usr/bin/env python3
"""
Master data build script. Runs all data generation steps in the correct order
and merges everything into bills.json at the end.

Usage:
    python build_data.py              # full pipeline (fetch + enrich)
    python build_data.py --enrich     # skip fetching, just re-enrich existing data
"""

import json
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
BILLS_PATH = DATA_DIR / "bills.json"
SYNOPSES_PATH = DATA_DIR / "synopses.json"
TOPICS_PATH = DATA_DIR / "topics.json"

PYTHON = str(Path(__file__).parent / ".venv" / "bin" / "python3")
SCRIPTS_DIR = Path(__file__).parent


def run(script, description):
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}")
    result = subprocess.run(
        [PYTHON, str(SCRIPTS_DIR / script)],
        cwd=str(SCRIPTS_DIR),
    )
    if result.returncode != 0:
        print(f"  WARNING: {script} exited with code {result.returncode}")
    return result.returncode == 0


def merge_all():
    """Read bills.json and merge in synopses + topics from their cache files."""
    print(f"\n{'='*60}")
    print(f"  Merging all enrichments into bills.json")
    print(f"{'='*60}")

    bills = json.loads(BILLS_PATH.read_text("utf-8"))

    # Merge synopses
    if SYNOPSES_PATH.exists():
        synopses = json.loads(SYNOPSES_PATH.read_text("utf-8"))
        syn_count = 0
        for bill in bills:
            bid = str(bill["id"])
            if bid in synopses and synopses[bid]:
                bill["synopsis"] = synopses[bid]
                syn_count += 1
        print(f"  Merged {syn_count} synopses")

    # Merge topics
    if TOPICS_PATH.exists():
        topics = json.loads(TOPICS_PATH.read_text("utf-8"))
        top_count = 0
        for bill in bills:
            bid = str(bill["id"])
            if bid in topics:
                bill["topics"] = topics[bid]
                top_count += 1
        print(f"  Merged {top_count} topic classifications")

    BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")

    # Stats
    total = len(bills)
    with_syn = sum(1 for b in bills if b.get("synopsis"))
    with_top = sum(1 for b in bills if b.get("topics"))
    with_doc = sum(1 for b in bills if b.get("doc_url"))
    with_votes = sum(1 for b in bills if len(b.get("mk_votes", [])) >= 20)
    print(f"\n  Final bills.json: {total} bills")
    print(f"    With doc_url:  {with_doc}")
    print(f"    With synopsis: {with_syn}")
    print(f"    With topics:   {with_top}")
    print(f"    With 20+ MKs:  {with_votes}")


def main():
    enrich_only = "--enrich" in sys.argv

    if not enrich_only:
        # Step 1: Fetch votes and bill metadata from Knesset APIs
        run("fetch_data.py", "Step 1: Fetching votes & bill metadata from Knesset")

    # Step 2: Download bill docs and generate synopses
    run("generate_synopses.py", "Step 2: Downloading bill docs & generating synopses")

    # Step 3: Classify bills by topic
    run("generate_topics.py", "Step 3: Classifying bills by topic")

    # Step 4: Fetch MK photos
    run("fetch_photos.py", "Step 4: Fetching MK photos from Wikipedia")

    # Step 5: Final merge (ensures all enrichments are in bills.json)
    merge_all()

    print(f"\n{'='*60}")
    print("  Done! Serve with: python -m http.server 8080")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
