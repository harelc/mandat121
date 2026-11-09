#!/usr/bin/env python3
"""MK analytics for המנדט ה־121 (Sprint 1A).

Inputs (already on disk, no network):
  data/k25_vote_results.json — per-MK results: {"names":[surname-first],
                               "votes":{"<voteId>":[[nameIdx,1|2|3],...]}}
                               1=בעד 2=נגד 3=נמנע. Coverage is whatever the
                               harvest has reached — reported, never hidden.
  data/k25_votes.json        — vote metadata (kind/date) for the same ids.
  data/k25_bills.json        — bills with initiators (initiator=True = lead).

Outputs data/k25_analytics.json:
  coverage   {votes_with_results, votes_total, names}
  per_mk     name -> {voted, for, against, abstain, bills_initiated,
                      rank_votes, rank_bills,
                      allies:[{name,pct,common}], opponents:[...]}
             allies/opponents need >= MIN_COMMON shared votes.
  bloc_stats {bridges:[{name,pct,within_pct,common}],  -- weighted agreement
                      with MKs outside one's own voting cluster
              outlier_pairs:[{a,b,pct,common}]}         -- lowest agreement
                      *within* a cluster (>= 500 shared votes)
             Global closest/furthest pairs are NOT emitted: agreement in
             K25 is bimodal (within-bloc ~99%, cross-bloc ~8%), so both
             lists were thousands of pairs tied at rounded 100/0.
  matrix     {names:[top-N by participation], pct:[[agree% or -1]]}
             for the correlation-matrix heatmap in the UI.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

MIN_COMMON = 50
OUT = Path("data/k25_analytics.json")


def main():
    res = json.load(open("data/k25_vote_results.json", encoding="utf-8"))
    votes_meta = {v["id"]: v for v in json.load(open("data/k25_votes.json", encoding="utf-8"))}
    bills = json.load(open("data/k25_bills.json", encoding="utf-8"))
    names = res["names"]
    n = len(names)

    per = {nm: Counter() for nm in names}
    # pair[common, agree] keyed by (i,j) i<j
    common = defaultdict(int)
    agree = defaultdict(int)
    covered = 0
    for vid, rows in res["votes"].items():
        if int(vid) not in votes_meta:
            continue
        covered += 1
        seen = {}
        for idx, code in rows:
            if idx >= n or code not in (1, 2, 3):
                continue
            seen[idx] = code
            c = per[names[idx]]
            c["voted"] += 1
            c[{1: "for", 2: "against", 3: "abstain"}[code]] += 1
        idxs = sorted(seen)
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                common[(i, j)] += 1
                if seen[i] == seen[j]:
                    agree[(i, j)] += 1

    initiated = Counter()
    for b in bills:
        for ini in b.get("initiators") or []:
            if ini.get("initiator") and ini.get("name"):
                initiated[ini["name"]] += 1

    def pct(i, j):
        c = common.get((i, j) if i < j else (j, i), 0)
        if c < MIN_COMMON:
            return None
        a = agree.get((i, j) if i < j else (j, i), 0)
        return round(a / c * 100), c

    per_mk = {}
    for idx, nm in enumerate(names):
        allies, opps = [], []
        for j, other in enumerate(names):
            if j == idx:
                continue
            p = pct(idx, j)
            if p:
                allies.append({"name": other, "pct": p[0], "common": p[1]})
        allies.sort(key=lambda x: -x["pct"])
        opps = sorted(allies, key=lambda x: x["pct"])
        c = per[nm]
        per_mk[nm] = {
            "voted": c["voted"], "for": c["for"], "against": c["against"],
            "abstain": c["abstain"], "bills_initiated": initiated.get(nm, 0),
            "allies": allies[:5], "opponents": opps[:3],
        }

    by_votes = sorted(per_mk, key=lambda nm: -per_mk[nm]["voted"])
    by_bills = sorted(per_mk, key=lambda nm: -per_mk[nm]["bills_initiated"])
    for r, nm in enumerate(by_votes, 1):
        per_mk[nm]["rank_votes"] = r
    for r, nm in enumerate(by_bills, 1):
        per_mk[nm]["rank_bills"] = r

    pairs = []
    for (i, j), c in common.items():
        if c >= MIN_COMMON:
            pairs.append({"a": names[i], "b": names[j],
                          "pct": round(agree[(i, j)] / c * 100), "common": c})

    # --- Cluster ordering (Jordan-form / block-diagonal matrix) ---------
    # Agglomerative hierarchical clustering, average linkage, over ALL MKs.
    # Distance = 100 - agreement%; pairs with too few shared votes get a
    # neutral 50 so they join late rather than distort a bloc. Cutting the
    # dendrogram where merges exceed CUT leaves the voting blocs (roughly:
    # coalition sub-blocs vs opposition sub-blocs), and the dendrogram leaf
    # order puts each bloc contiguously — so in the matrix the high values
    # sit in squares on the diagonal, Jordan-canonical style.
    def dist(i, j):
        c = common.get((i, j) if i < j else (j, i), 0)
        if c < 30:
            return 50.0
        a = agree.get((i, j) if i < j else (j, i), 0)
        return 100.0 - (a / c * 100)

    # cluster = [member idxs, left tree, right tree]; tree None = leaf
    clusters = [[[i], None, None] for i in range(n)]
    CUT = 20.0  # sub-blocs: split where average agreement drops below ~80%
    cut_snapshot = None
    while len(clusters) > 1:
        best, bd = None, None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                ds = [dist(i, j) for i in clusters[x][0] for j in clusters[y][0]]
                d = sum(ds) / len(ds)
                if bd is None or d < bd:
                    best, bd = (x, y), d
        if cut_snapshot is None and bd > CUT:
            cut_snapshot = [list(c[0]) for c in clusters]
        x, y = best
        merged = [clusters[x][0] + clusters[y][0], clusters[x], clusters[y]]
        clusters = [c for k, c in enumerate(clusters) if k not in (x, y)] + [merged]
    if cut_snapshot is None:
        cut_snapshot = [clusters[0][0]]

    def leaf_order(node):
        if node[1] is None:
            return node[0]
        return leaf_order(node[1]) + leaf_order(node[2])

    cluster_of = {}
    for ci, members in enumerate(cut_snapshot):
        for m in members:
            cluster_of[m] = ci
    ordered_idx = leaf_order(clusters[0])
    # Displayed matrix: EVERY MK who served in the term (147 in K25 — 120
    # seats, plus mid-term replacements), in cluster (Jordan) order. No
    # top-N cut: the bloc structure is the point, and a 147x147 heatmap
    # with tooltip cells stays legible.
    disp_idx = ordered_idx
    top_names = [names[i] for i in disp_idx]
    matrix_pct = []
    for i in disp_idx:
        row = []
        for j in disp_idx:
            if i == j:
                row.append(100)
            else:
                p = pct(i, j)
                row.append(p[0] if p else -1)
        matrix_pct.append(row)
    matrix_clusters = [cluster_of[i] for i in disp_idx]
    cluster_groups = []
    for i in disp_idx:
        nm = names[i]
        if cluster_groups and cluster_groups[-1]["cluster"] == cluster_of[i]:
            cluster_groups[-1]["names"].append(nm)
        else:
            cluster_groups.append({"cluster": cluster_of[i], "names": [nm]})

    # --- Bloc-relative stats (replace global closest/furthest pairs) -----
    # Agreement is bimodal, so global extremes only restate bloc membership:
    # thousands of pairs tie at rounded 100/0 and sorting by the rounded pct
    # sampled them arbitrarily (one MK filled the whole "closest" list).
    # What varies meaningfully *given* the blocs:
    #   bridges       — per-MK weighted agreement with MKs outside their own
    #                   cluster (who actually votes across the divide);
    #   outlier_pairs — the least-agreeing pairs *inside* a cluster (who
    #                   deviates from their own bloc), >= 500 shared votes,
    #                   max 2 pairs per MK so one outlier can't fill the list.
    # Clusters of < 4 are excluded: a lone MK has no bloc to bridge from or
    # deviate within.
    OUTLIER_MIN_COMMON = 500
    bloc_sizes = Counter(cluster_of.values())

    def pair_counts(i, j):
        key = (i, j) if i < j else (j, i)
        return agree.get(key, 0), common.get(key, 0)

    bridges = []
    for i, nm in enumerate(names):
        if bloc_sizes[cluster_of[i]] < 4:
            continue
        cross_a = cross_c = within_a = within_c = 0
        for j in range(n):
            if j == i:
                continue
            a, c = pair_counts(i, j)
            if c < MIN_COMMON:
                continue
            if cluster_of[j] == cluster_of[i]:
                within_a += a; within_c += c
            else:
                cross_a += a; cross_c += c
        if cross_c:
            bridges.append({"name": nm,
                            "pct": round(cross_a / cross_c * 100, 1),
                            "within_pct": round(within_a / within_c * 100, 1) if within_c else None,
                            "common": cross_c})
    bridges.sort(key=lambda x: (-x["pct"], -x["common"]))

    within_pairs = []
    for (i, j), c in common.items():
        if c < OUTLIER_MIN_COMMON or cluster_of[i] != cluster_of[j]:
            continue
        if bloc_sizes[cluster_of[i]] < 4:
            continue
        within_pairs.append({"a": names[i], "b": names[j],
                             "pct": round(agree[(i, j)] / c * 100, 1),
                             "common": c, "_exact": agree[(i, j)] / c})
    within_pairs.sort(key=lambda x: (x["_exact"], -x["common"]))
    outlier_pairs, seen_count = [], Counter()
    for p in within_pairs:
        if seen_count[p["a"]] >= 2 or seen_count[p["b"]] >= 2:
            continue
        seen_count[p["a"]] += 1; seen_count[p["b"]] += 1
        outlier_pairs.append({"a": p["a"], "b": p["b"],
                              "pct": p["pct"], "common": p["common"]})
        if len(outlier_pairs) >= 10:
            break

    out = {
        "generated_from": "k25_vote_results.json + k25_votes.json + k25_bills.json",
        "min_common": MIN_COMMON,
        "coverage": {"votes_with_results": covered,
                     "votes_total": len(votes_meta), "mks": n},
        "per_mk": per_mk,
        "rank_by_votes": [{"name": nm, "voted": per_mk[nm]["voted"]} for nm in by_votes[:25]],
        "rank_by_bills": [{"name": nm, "bills": per_mk[nm]["bills_initiated"]}
                          for nm in by_bills[:25] if per_mk[nm]["bills_initiated"]],
        "bloc_stats": {"bridges": bridges[:12],
                       "outlier_pairs": outlier_pairs,
                       "outlier_min_common": OUTLIER_MIN_COMMON},
        "matrix": {"names": top_names, "pct": matrix_pct,
                   "clusters": matrix_clusters, "groups": cluster_groups,
                   "ordering": "hierarchical clustering (average linkage) — Jordan/block-diagonal"},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"DONE {OUT} — {covered}/{len(votes_meta)} votes, {n} MKs, "
          f"{len(pairs)} pairs >= {MIN_COMMON} common")


if __name__ == "__main__":
    main()
