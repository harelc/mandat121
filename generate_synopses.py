#!/usr/bin/env python3
"""
Download bill documents, extract text, and generate catchy Hebrew synopses.

Uses Claude API (via macOS Keychain token) for synopsis generation,
with extractive fallback if API is unavailable.
"""

import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

DATA_DIR = Path(__file__).parent / "data"
DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

BILLS_PATH = DATA_DIR / "bills.json"
SYNOPSES_PATH = DATA_DIR / "synopses.json"

# Claude API config
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 300


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
    except Exception as e:
        print(f"  Download failed: {e}")
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
    except Exception as e:
        print(f"  DOCX extraction failed: {e}")
        return ""


def extract_pdf_text(path):
    """Extract text from PDF using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:10]:  # First 10 pages max
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except ImportError:
        print("  pdfplumber not installed, skipping PDF")
        return ""
    except Exception as e:
        print(f"  PDF extraction failed: {e}")
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

    # Try to find "דברי הסבר" (explanatory notes) section
    divrei_match = re.search(r"דברי\s+הסבר", text)
    if divrei_match:
        after = text[divrei_match.end():]
    else:
        # Take text after the first ~20% (skip headers/legal boilerplate)
        start = len(text) // 5
        after = text[start:]

    # Split into sentences and pick meaningful ones
    sentences = re.split(r"(?<=[.!?])\s+", after.strip())
    good = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15 or len(s) > 300:
            continue
        # Skip boilerplate
        if re.search(r"(סעיף\s+\d|תיקון\s+מס|פרק\s+[א-ת]|__)", s):
            continue
        good.append(s)
        if len(good) >= 3:
            break

    return " ".join(good) if good else ""


def generate_synopsis_claude(client, bill_name, text):
    """Generate a catchy Hebrew synopsis using Claude API."""
    # Truncate text to ~4000 chars to keep costs low
    truncated = text[:4000] if len(text) > 4000 else text

    if text and len(text) > 50:
        prompt = f"""אתה כותב תקצירים קליטים בעברית להצעות חוק, עבור אפליקציית חידון הצבעות.

הצעת החוק: {bill_name}

טקסט ההצעה:
{truncated}

כתוב תקציר של 2-3 משפטים קצרים וקליטים בעברית שמסבירים את מהות ההצעה בשפה פשוטה ומעניינת.
המטרה: לגרום למשתמש להבין במהירות על מה ההצעה ולרצות להצביע.
אל תכתוב "הצעת חוק זו" - פשוט תתחיל לתאר.
אל תשתמש בשאלות רטוריות. אל תפנה לקורא. אל תזכיר שמות של חברי כנסת. אל תוסיף כותרת, מספר, או תבליטים — רק את הטקסט."""
    else:
        # No document text — generate from bill name alone
        prompt = f"""אתה כותב תקצירים קליטים בעברית להצעות חוק, עבור אפליקציית חידון הצבעות.

שם הצעת החוק: {bill_name}

בהתבסס על שם ההצעה בלבד, כתוב תקציר של 2-3 משפטים קצרים וקליטים בעברית שמסבירים את מהות ההצעה בשפה פשוטה ומעניינת.
אם אתה לא בטוח מה ההצעה עושה, תן הסבר סביר בהתבסס על שם החוק.
המטרה: לגרום למשתמש להבין במהירות על מה ההצעה ולרצות להצביע.
אל תכתוב "הצעת חוק זו" - פשוט תתחיל לתאר.
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


def main():
    bills = json.loads(BILLS_PATH.read_text("utf-8"))
    print(f"Loaded {len(bills)} bills")

    # Load existing synopses cache
    if SYNOPSES_PATH.exists():
        synopses = json.loads(SYNOPSES_PATH.read_text("utf-8"))
        print(f"Loaded {len(synopses)} cached synopses")
    else:
        synopses = {}

    # Filter to bills with 10+ votes (doc_url optional — will generate from name if needed)
    eligible = [b for b in bills if len(b.get("mk_votes", [])) >= 10]
    print(f"Eligible bills (10+ votes): {len(eligible)}")

    # Set up Claude client
    api_key = get_api_key()
    client = None
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            print("Claude API available — will generate AI synopses")
        except ImportError:
            print("anthropic package not installed, using extractive fallback")
    else:
        print("No API key found, using extractive fallback")

    # Step 1: Download docs and generate synopses
    downloaded = 0
    generated = 0
    failed = 0

    for i, bill in enumerate(eligible):
        bill_id = str(bill["id"])

        # Skip if already have synopsis
        if bill_id in synopses and synopses[bill_id]:
            continue

        url = bill.get("doc_url")
        text = ""

        if url:
            ext = ".docx" if url.lower().endswith(".docx") else ".pdf"
            doc_path = DOCS_DIR / f"{bill_id}{ext}"

            # Download
            if download_doc(url, doc_path):
                downloaded += 1
                text = extract_text(url, doc_path)
            else:
                print(f"  [{i+1}/{len(eligible)}] No doc: {bill['name'][:50]}")

        # Generate synopsis
        synopsis = ""
        if client:
            synopsis = generate_synopsis_claude(client, bill["name"], text)
            label = "AI" if text and len(text) > 50 else "AI-NAME"
            if synopsis:
                print(f"  [{i+1}/{len(eligible)}] {label}: {bill['name'][:40]}... → {synopsis[:60]}...")
            # Rate limiting
            time.sleep(0.3)

        if not synopsis and text:
            synopsis = extractive_synopsis(text, bill["name"])
            if synopsis:
                print(f"  [{i+1}/{len(eligible)}] EXT: {bill['name'][:40]}... → {synopsis[:60]}...")

        if not synopsis:
            # Last resort: use the bill name cleaned up
            name = bill["name"]
            name = re.sub(r"\(תיקון[^)]*\)", "", name).strip()
            name = re.sub(r",\s*הת?שפ\"?[א-ת].*$", "", name).strip()
            synopsis = name
            print(f"  [{i+1}/{len(eligible)}] NAME: {synopsis[:60]}")

        synopses[bill_id] = synopsis
        generated += 1

        # Save cache periodically
        if generated % 10 == 0:
            SYNOPSES_PATH.write_text(json.dumps(synopses, ensure_ascii=False, indent=2), "utf-8")
            print(f"  ... saved cache ({len(synopses)} synopses)")

    # Final save of synopses
    SYNOPSES_PATH.write_text(json.dumps(synopses, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSynopses: {len(synopses)} total, {generated} new, {failed} failed downloads")

    # Step 2: Merge into bills.json
    merged = 0
    for bill in bills:
        bill_id = str(bill["id"])
        if bill_id in synopses and synopses[bill_id]:
            bill["synopsis"] = synopses[bill_id]
            merged += 1

    BILLS_PATH.write_text(json.dumps(bills, ensure_ascii=False, indent=2), "utf-8")
    print(f"Merged {merged} synopses into {BILLS_PATH}")


if __name__ == "__main__":
    main()
