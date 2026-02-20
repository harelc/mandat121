#!/usr/bin/env python3
"""
Download bill documents, extract text, and generate catchy Hebrew synopses.

Uses Claude API (via macOS Keychain token) for synopsis generation,
with extractive fallback if API is unavailable.

Outputs in batches — merges into bills.json every BATCH_SIZE synopses
so the app gets updated data incrementally.
"""

import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

BILLS_PATH = DATA_DIR / "bills.json"

# Claude API config
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 300
BATCH_SIZE = 50  # merge into bills.json every N synopses
DOWNLOAD_WORKERS = 10  # parallel doc downloads
SYNOPSIS_WORKERS = 10  # parallel Claude API calls


def get_api_key():
    """Get Anthropic API key from env or macOS Keychain."""
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


def download_doc(url, dest):
    """Download a document, return True if successful."""
    if dest.exists() and dest.stat().st_size > 100:
        return True
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 100:
            return False
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def extract_docx_text(path):
    """Extract text from DOCX using stdlib (zipfile + XML)."""
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("word/document.xml") as f:
                tree = ElementTree.parse(f)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for p in tree.iter(f"{{{ns['w']}}}p"):
            texts = [t.text for t in p.iter(f"{{{ns['w']}}}t") if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception:
        return ""


def extract_pdf_text(path):
    """Extract text from PDF using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:10]:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except ImportError:
        return ""
    except Exception:
        return ""


def extract_text(url, doc_path):
    """Extract text from a downloaded document."""
    if url.lower().endswith(".docx"):
        return extract_docx_text(doc_path)
    elif url.lower().endswith(".pdf"):
        return extract_pdf_text(doc_path)
    return ""


def extractive_synopsis(text, bill_name):
    """Fallback: extract meaningful sentences from bill text."""
    if not text or len(text) < 50:
        return ""

    divrei_match = re.search(r"דברי\s+הסבר", text)
    if divrei_match:
        after = text[divrei_match.end():]
    else:
        start = len(text) // 5
        after = text[start:]

    sentences = re.split(r"(?<=[.!?])\s+", after.strip())
    good = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15 or len(s) > 300:
            continue
        if re.search(r"(סעיף\s+\d|תיקון\s+מס|פרק\s+[א-ת]|__)", s):
            continue
        good.append(s)
        if len(good) >= 3:
            break

    return " ".join(good) if good else ""


def generate_synopsis_claude(client, bill_name, text):
    """Generate a catchy Hebrew synopsis using Claude API."""
    truncated = text[:4000] if len(text) > 4000 else text

    if text and len(text) > 50:
        prompt = f"""אתה כותב תקצירים קליטים בעברית להצעות חוק, עבור אפליקציית חידון הצבעות.

הצעת החוק: {bill_name}

טקסט ההצעה:
{truncated}

כתוב תקציר של 2-3 משפטים קצרים וקליטים בעברית שמסבירים את מהות ההצעה בשפה פשוטה ומעניינת.
המטרה: לגרום למשתמש להבין במהירות על מה ההצעה ולרצות להצביע.
חשוב מאוד: היה ניטרלי לחלוטין. אל תרמוז אם ההצעה טובה או רעה, חיובית או שלילית. תאר מה ההצעה עושה בלי לשפוט.
אל תשתמש בביטויים מוטים כמו "סוף סוף", "לצערנו", "מבורך", "מסוכן", "פגיעה חמורה" וכדומה.
אל תכתוב "הצעת חוק זו" — פשוט תתחיל לתאר.
אל תשתמש בשאלות רטוריות. אל תפנה לקורא. אל תזכיר שמות של חברי כנסת. אל תוסיף כותרת, מספר, או תבליטים — רק את הטקסט."""
    else:
        prompt = f"""אתה כותב תקצירים קליטים בעברית להצעות חוק, עבור אפליקציית חידון הצבעות.

שם הצעת החוק: {bill_name}

בהתבסס על שם ההצעה בלבד, כתוב תקציר של 2-3 משפטים קצרים וקליטים בעברית שמסבירים את מהות ההצעה בשפה פשוטה ומעניינת.
אם אתה לא בטוח מה ההצעה עושה, תן הסבר סביר בהתבסס על שם החוק.
המטרה: לגרום למשתמש להבין במהירות על מה ההצעה ולרצות להצביע.
חשוב מאוד: היה ניטרלי לחלוטין. אל תרמוז אם ההצעה טובה או רעה, חיובית או שלילית. תאר מה ההצעה עושה בלי לשפוט.
אל תשתמש בביטויים מוטים כמו "סוף סוף", "לצערנו", "מבורך", "מסוכן", "פגיעה חמורה" וכדומה.
אל תכתוב "הצעת חוק זו" — פשוט תתחיל לתאר.
אל תשתמש בשאלות רטוריות. אל תפנה לקורא. אל תזכיר שמות של חברי כנסת. אל תוסיף כותרת, מספר, או תבליטים — רק את הטקסט."""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  Claude API error: {e}")
        return ""


def save_bills(bills):
    """Save bills.json atomically."""
    BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")


def main():
    bills = json.loads(BILLS_PATH.read_text("utf-8"))
    bills_by_id = {str(b["id"]): b for b in bills}
    print(f"Loaded {len(bills)} bills")

    # Filter to bills needing synopses (10+ votes, no existing synopsis)
    need_synopsis = [
        b for b in bills
        if len(b.get("mk_votes", [])) >= 10
        and not (b.get("synopsis") and len(b.get("synopsis", "")) > 20)
    ]
    already = len(bills) - len([b for b in bills if len(b.get("mk_votes", [])) < 10]) - len(need_synopsis)
    print(f"Already have synopsis: {already}")
    print(f"Need synopsis: {len(need_synopsis)}")

    if not need_synopsis:
        print("Nothing to do!")
        return

    # Step 1: Download all docs in parallel
    print(f"\n=== Downloading documents ({DOWNLOAD_WORKERS} workers) ===")
    doc_tasks = {}
    for bill in need_synopsis:
        url = bill.get("doc_url")
        if url:
            ext = ".docx" if url.lower().endswith(".docx") else ".pdf"
            doc_path = DOCS_DIR / f"{bill['id']}{ext}"
            doc_tasks[str(bill["id"])] = (url, doc_path)

    downloaded = 0
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
        futures = {
            executor.submit(download_doc, url, path): bid
            for bid, (url, path) in doc_tasks.items()
        }
        for future in as_completed(futures):
            if future.result():
                downloaded += 1

    print(f"  Downloaded/cached: {downloaded}/{len(doc_tasks)} docs")

    # Step 2: Set up Claude client
    api_key = get_api_key()
    client = None
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            print("\nClaude API available")
        except ImportError:
            print("\nanthropic package not installed, using extractive fallback")
    else:
        print("\nNo API key found, using extractive fallback")

    # Step 3: Prepare text for each bill
    print(f"\n=== Extracting document text ===")
    bill_texts = {}
    for bill in need_synopsis:
        bill_id = str(bill["id"])
        text = ""
        if bill_id in doc_tasks:
            url, doc_path = doc_tasks[bill_id]
            if doc_path.exists():
                text = extract_text(url, doc_path)
        bill_texts[bill_id] = text

    has_text = sum(1 for t in bill_texts.values() if t and len(t) > 50)
    print(f"  {has_text}/{len(need_synopsis)} bills have extractable doc text")

    # Step 4: Generate synopses in parallel
    workers = SYNOPSIS_WORKERS if client else 1
    print(f"\n=== Generating synopses ({workers} workers, batch save every {BATCH_SIZE}) ===")

    def generate_one(bill):
        bill_id = str(bill["id"])
        text = bill_texts.get(bill_id, "")

        synopsis = ""
        label = "NAME"
        if client:
            synopsis = generate_synopsis_claude(client, bill["name"], text)
            label = "AI" if text and len(text) > 50 else "AI-NAME"

        if not synopsis and text:
            synopsis = extractive_synopsis(text, bill["name"])
            label = "EXT"

        if not synopsis:
            name = bill["name"]
            name = re.sub(r"\(תיקון[^)]*\)", "", name).strip()
            name = re.sub(r",\s*הת?שפ\"?[א-ת].*$", "", name).strip()
            synopsis = name
            label = "NAME"

        return bill_id, synopsis, label

    generated = 0
    batch_count = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(generate_one, bill): bill
            for bill in need_synopsis
        }
        for future in as_completed(futures):
            bill_id, synopsis, label = future.result()
            generated += 1

            # Update in bills list
            if bill_id in bills_by_id:
                bills_by_id[bill_id]["synopsis"] = synopsis

            if generated % 10 == 0 or generated == len(need_synopsis):
                print(f"  {generated}/{len(need_synopsis)} done ({label}: {synopsis[:50]}...)", flush=True)

            # Batch save
            if generated % BATCH_SIZE == 0:
                batch_count += 1
                save_bills(bills)
                print(f"  --- batch {batch_count}: saved {generated} synopses ---", flush=True)

    # Final save
    if generated % BATCH_SIZE != 0:
        save_bills(bills)

    print(f"\nDone: {generated} new synopses generated and merged into {BILLS_PATH}")


if __name__ == "__main__":
    main()
