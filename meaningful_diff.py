#!/usr/bin/env python3
"""Commit gate for the daily data build: is a staged change meaningful?

Builders rewrite their outputs every run, and some carry a build
timestamp (agenda `generated_at`, contacts/wiki `_meta.fetched_at`).
A run where nothing upstream actually changed still produced a data
commit — and every commit triggers a Netlify deploy, which costs
build minutes. This script compares each staged file against HEAD
*semantically*: both sides are parsed as JSON and compared with the
volatile timestamp keys scrubbed and punctuation-only orthography
normalized (see _NOISE_CHARS), so timestamp-only (or key-order /
re-serialization, or geresh/apostrophe) churn does not count as a
change. Files that are not meaningful are restored to HEAD; the caller
then commits only if anything staged remains.

Non-JSON files fall back to byte comparison. Added/deleted files are
always meaningful.

Usage (from the workflow, after `git add data/`):
    python3 meaningful_diff.py            # restore noise, report
    git diff --cached --quiet && exit 0   # nothing meaningful left

Stdlib only.
"""
import json
import re
import subprocess
import sys

# today/this_week: the agenda viewer computes them client-side
# (agRefreshToday in index.html), so a midnight rollover in the build
# stamp is not a data change and must not force a commit/deploy.
VOLATILE_KEYS = {"generated_at", "fetched_at", "today", "this_week"}

# Identity-irrelevant orthography: geresh/gershayim and their ASCII /
# curly-quote spellings (סגלוביץ vs סגלוביץ' vs סגלוביץ׳), Hebrew
# combining marks, and whitespace runs. Every name matcher in this repo
# (and index.html normName) already treats these as the same person, so
# a source feed flipping only them is not a data change — it must not
# become a commit, a Netlify deploy, or a notification issue (that is
# exactly how the סגלוביץ flip-flop generated issue #38). Trade-off: a
# real edit that changes *only* punctuation in a title is also held
# back until it rides along with a substantive change to the file.
_NOISE_CHARS = re.compile(
    r"['\u2018\u2019`\u05f3\"\u201c\u201d\u05f4"
    r"\u05b0-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7]")


def normalize_text(s):
    return " ".join(_NOISE_CHARS.sub("", s).split())


def scrub(obj):
    """Recursively drop volatile timestamp keys from parsed JSON, and
    normalize strings (keys included — names are dict keys in the
    by_mk layers) so punctuation-only churn compares equal."""
    if isinstance(obj, dict):
        return {normalize_text(k) if isinstance(k, str) else k: scrub(v)
                for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return normalize_text(obj)
    return obj


def is_meaningful(old, new):
    """old/new are bytes of the HEAD and staged versions (either may be
    None when the file was added/deleted). True = worth committing."""
    if old is None or new is None:
        return True
    if old == new:
        return False
    try:
        return scrub(json.loads(old)) != scrub(json.loads(new))
    except Exception:
        return True  # unparsable on one side: trust the byte diff


def staged_paths():
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "-z"]).decode("utf-8")
    return [p for p in out.split("\0") if p]


def blob(spec):
    r = subprocess.run(["git", "show", spec], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def main():
    noise = []
    for path in staged_paths():
        old, new = blob(f"HEAD:{path}"), blob(f":{path}")
        if is_meaningful(old, new):
            print(f"meaningful: {path}")
        else:
            noise.append(path)
            print(f"timestamp/format only, restoring: {path}")
    if noise:
        subprocess.check_call(
            ["git", "restore", "--source=HEAD", "--staged", "--worktree", "--"] + noise)
    return 0


if __name__ == "__main__":
    sys.exit(main())
