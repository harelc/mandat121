"""Era registry — the one place era facts live (builders side).

Design rule for this codebase: "which Knesset" is data, never a code
path. Supporting Knesset 27, or backfilling Knesset 24 and earlier,
must mean: run the same builders with a different --knesset, add one
ERAS entry in index.html, and bump CURRENT_KNESSET in the daily
workflow. Anything that needs a new branch per era is a bug.

Era facts therefore come from the official feed itself wherever it
publishes them (KNS_KnessetDates.PlenumStart is the era's first
plenum day). The small map below is an offline fallback for eras
whose dates are already known, used only when the caller could not
fetch KNS_KnessetDates (tests, feed outage) — never as a gate: an
era missing from it still resolves via the feed.

Stdlib only.
"""
import argparse

# Offline fallback era windows (UTC), keyed by Knesset number. The
# authoritative source is KNS_KnessetDates.PlenumStart in OData.
ERA_FALLBACK_START = {
    25: "2022-11-15T00:00:00Z",
    26: "2026-10-01T00:00:00Z",
}


def knesset_type(s):
    """argparse type for --knesset: any Knesset number, named explicitly."""
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError(f"knesset must be >= 1, got {s!r}")
    return n


def era_window_utc(knesset_num, plenum_start_iso=None):
    """Era start (UTC ISO) for date-windowed entities (plenum votes
    carry no KnessetNum, so they are scoped by this window).

    plenum_start_iso: the era's KNS_KnessetDates.PlenumStart as fetched
    by the caller, when available. Returns None when neither the feed
    value nor the fallback map knows the era — callers must then treat
    the era as not-yet-started rather than guessing a window (a guessed
    window would count the previous era's votes as this era's)."""
    if plenum_start_iso:
        return plenum_start_iso[:10] + "T00:00:00Z"
    return ERA_FALLBACK_START.get(knesset_num)
