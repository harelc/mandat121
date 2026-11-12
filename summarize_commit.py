#!/usr/bin/env python3
"""Markdown summary of one commit, for the post-push notification issue.

Stdlib only. Compares a commit against its first parent and reports,
per changed file: add/delete/modify, byte-size delta, line +/-, and —
for JSON files — a semantic count delta (e.g. list length, or the size
of each top-level list/dict value), so a one-line-minified data file
still reads as "bills: 7,490 → 7,491 (+1)" instead of "1 insertion,
1 deletion". Volatile timestamp keys (`generated_at`, `fetched_at`,
`_meta`) are ignored: they never explain why the commit exists.

Usage:
    python3 summarize_commit.py [ref]   # default HEAD; prints Markdown
"""
import json
import subprocess
import sys

VOLATILE_KEYS = {"generated_at", "fetched_at", "_meta"}
MAX_FILES = 60
MAX_DETAILS_PER_FILE = 8


def _size(value):
    return len(value) if isinstance(value, (list, dict)) else None


def describe_change(old_bytes, new_bytes):
    """One-line semantic description of a file's change, or '' if the
    bytes are not both parseable JSON. Either side may be None (the
    file was added/deleted)."""
    if old_bytes is None:
        return "added"
    if new_bytes is None:
        return "deleted"
    try:
        old, new = json.loads(old_bytes), json.loads(new_bytes)
    except Exception:
        return ""
    if isinstance(old, list) and isinstance(new, list):
        delta = len(new) - len(old)
        return f"items {len(old):,} → {len(new):,} ({delta:+,})" if delta else "items unchanged, contents changed"
    if isinstance(old, dict) and isinstance(new, dict):
        parts = []
        for key in sorted(set(old) | set(new)):
            if key in VOLATILE_KEYS:
                continue
            o, n = old.get(key), new.get(key)
            if o == n:
                continue
            o_size, n_size = _size(o), _size(n)
            if o_size is not None and n_size is not None:
                parts.append(f"`{key}` {o_size:,} → {n_size:,} ({n_size - o_size:+,})")
            elif isinstance(o, (int, float, str)) and isinstance(n, (int, float, str)):
                parts.append(f"`{key}` {str(o)[:40]} → {str(n)[:40]}")
            else:
                parts.append(f"`{key}` changed")
            if len(parts) >= MAX_DETAILS_PER_FILE:
                parts.append("…")
                break
        return "; ".join(parts) if parts else "contents changed"
    if old != new:
        return "contents changed"
    return ""


def _git(*args):
    return subprocess.check_output(["git", *args]).decode("utf-8", "replace")


def _blob(spec):
    r = subprocess.run(["git", "show", spec], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def summarize(ref="HEAD"):
    parent = _git("rev-parse", f"{ref}^").strip() if _parent_exists(ref) else None
    subject = _git("log", "-1", "--format=%s", ref).strip()
    lines = [f"**Commit:** `{ref}` — {subject}", ""]
    if parent is None:
        lines.append("(no parent commit to diff against)")
        return "\n".join(lines)

    name_status = _git("diff", "--name-status", parent, ref).splitlines()
    numstat = {}
    for row in _git("diff", "--numstat", parent, ref).splitlines():
        parts = row.split("\t")
        if len(parts) == 3:
            numstat[parts[2]] = (parts[0], parts[1])

    lines.append(f"**{len(name_status)} file(s) changed**\n")
    lines.append("| File | Change | Size | Lines | Detail |")
    lines.append("|---|---|---|---|---|")
    for row in name_status[:MAX_FILES]:
        parts = row.split("\t")
        status, path = parts[0], parts[-1]
        old = _blob(f"{parent}:{path}") if status != "A" else None
        new = _blob(f"{ref}:{path}") if status != "D" else None
        size = ""
        if old is not None and new is not None:
            size = f"{len(old):,} → {len(new):,} B ({len(new) - len(old):+,})"
        elif new is not None:
            size = f"{len(new):,} B"
        add, dele = numstat.get(path, ("", ""))
        lines.append(
            f"| `{path}` | {status} | {size} | +{add}/-{dele} | {describe_change(old, new)} |")
    if len(name_status) > MAX_FILES:
        lines.append(f"\n_…and {len(name_status) - MAX_FILES} more file(s)._")
    return "\n".join(lines)


def _parent_exists(ref):
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^"],
        capture_output=True).returncode == 0


if __name__ == "__main__":
    print(summarize(sys.argv[1] if len(sys.argv) > 1 else "HEAD"))
