"""Synthetic known-answer check: report counting vs lineage (independent sources).

No model, network or operational data. Each trial has observers who see a true value or
make an error, relays who repeat an existing report (declaring the basis with probability
``declare``), and corrections of erroneous reports. Some genuine observers are sent another
observer's report before they report (``told``); some of those enter their own observation
time (``timed``). Ground truth is known by construction.

Questions measured (see README.md):
1. "Is this value backed by >= 2 separate observations?" — false and missed confirmations
   for three rules: counting reports, declared lineage (``independent_sources``), and the
   conservative lower bound (``independent_sources_min``).
2. "Did a later correction touch what this decision used?" — direct supersedes only (the
   baseline behaviour) vs direct + declared bases.

This tests the software rules on generated data. It is not evidence about real teams, real
reporting habits, or practical benefit.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from report_lineage import annotate_context, drift_since

CLAIM_LEVEL = "synthetic_software_check"
METHODS = ("count", "declared", "conservative")


def trial(rng, *, observers, relays, error, declare, correct, reach, told, timed):
    true, wrong = "ready", "not_ready"
    history, values, root_of, rid_of = [], {}, {}, {}
    t = 0.0

    def tick():
        nonlocal t
        t += 1.0
        return t

    def send(version, actor):
        at = tick()
        payload = {"report": version, "recipients": [actor]}
        history.append((f"p{len(history)}", {"kind": "publication", "known_at": at, "payload": payload}))

    def add(version, rid, reporter, value, based_on=(), supersedes=None, observed_at=None):
        at = tick()
        report = {
            "id": rid,
            "reporter_id": reporter,
            "subject_id": "item",
            "topic": "status",
            "value": value,
            "observed_at": observed_at,
            "time_basis": "participant_reported" if observed_at is not None else "unknown",
        }
        payload = {"report": report, "supersedes": supersedes}
        if based_on:
            payload["based_on"] = list(based_on)
        history.append((version, {"kind": "report", "payload": payload, "known_at": at}))
        values[rid], rid_of[version] = value, rid

    for i in range(observers):
        value = wrong if rng.random() < error else true
        observed_at = None
        if i and rng.random() < told:
            before = t + 0.5
            send(f"v{rng.randrange(i)}", f"obs{i}")
            if rng.random() < timed:
                observed_at = before  # genuinely saw it before being told
        add(f"v{i}", f"r{i}", f"obs{i}", value, observed_at=observed_at)
        root_of[f"v{i}"] = f"v{i}"
    pool = [f"v{i}" for i in range(observers)]
    for j in range(relays):
        src = rng.choice(pool)  # relays may repeat relays (chains)
        send(src, f"relay{j}")
        declared = rng.random() < declare
        add(f"w{j}", f"q{j}", f"relay{j}", values[rid_of[src]], [src] if declared else ())
        root_of[f"w{j}"] = root_of[src]  # generator truth, declared or not
        pool.append(f"w{j}")
    # What the decision maker actually received: every relay, each original with prob reach.
    received = [v for v in pool if v.startswith("w") or rng.random() < reach]
    ctx = {"reports": [{"id": rid_of[v], "value": values[rid_of[v]]} for v in received]}
    ctx["decision_checks"] = [{"evidence_report_ids": [r["id"] for r in ctx["reports"]]}]
    annotate_context(ctx, history)
    support = {s["value"]: s for s in ctx["decision_checks"][0]["support"]}
    got = {}
    for v in received:
        got.setdefault(values[rid_of[v]], set()).add(root_of[v])
    empty = {"reports": 0, "independent_sources": 0, "independent_sources_min": 0}
    out = {}
    for value in (true, wrong):
        s = support.get(value, empty)
        out[value] = dict(
            truth=len(got.get(value, ())) >= 2,  # distinct real observations received
            count=s["reports"] >= 2,
            declared=s["independent_sources"] >= 2,
            conservative=s["independent_sources_min"] >= 2,
        )
    saved = {"now": t, "source_versions": received}
    corrected = set()
    for i in range(observers):
        if values[f"r{i}"] == wrong and rng.random() < correct:
            add(f"c{i}", f"x{i}", f"obs{i}", true, supersedes=f"v{i}")
            corrected.add(f"v{i}")
    touched = any(root_of[v] in corrected for v in received)
    return out, history, saved, touched


def run(n, seed, **kw):
    rng = random.Random(seed)
    tallies = {m: {"false_confirm": 0, "missed_confirm": 0} for m in METHODS}
    negatives = positives = 0
    drift_direct = drift_lineage = drift_truth = 0
    for _ in range(n):
        out, history, saved, touched = trial(rng, **kw)
        for r in out.values():
            positives += r["truth"]
            negatives += not r["truth"]
            for m in METHODS:
                tallies[m]["false_confirm"] += r[m] and not r["truth"]
                tallies[m]["missed_confirm"] += r["truth"] and not r[m]
        if touched:
            drift_truth += 1
            found = drift_since(saved, history)["corrected_since_saved"]
            drift_lineage += bool(found)
            drift_direct += any(v["relation"] == "self" for d in found for v in d["via"])
    rate = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {
        "params": dict(n=n, seed=seed, **kw),
        "negatives": negatives,
        "positives": positives,
        "false_confirm_rate": {m: rate(t["false_confirm"], negatives) for m, t in tallies.items()},
        "missed_confirm_rate": {m: rate(t["missed_confirm"], positives) for m, t in tallies.items()},
        "decisions_touched_by_correction": drift_truth,
        "drift_noticed": {"direct_only": drift_direct, "with_lineage": drift_lineage},
    }


BASE = dict(observers=4, relays=4, error=0.25, correct=0.8, reach=0.5)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261127)
    args = ap.parse_args(argv)
    if args.out.exists():
        raise SystemExit("output exists; choose a new folder")
    grid = [
        run(args.n, args.seed, declare=d, told=0.0, timed=0.0, **BASE)
        for d in (1.0, 0.7, 0.3, 0.0)
    ]
    # Cost of caution: genuine observers who were told first; some enter their own time.
    grid += [
        run(args.n, args.seed, declare=0.0, told=0.5, timed=tm, **BASE) for tm in (0.0, 0.5, 1.0)
    ]
    result = {"claim_level": CLAIM_LEVEL, "runs": grid}
    args.out.mkdir(parents=True)
    (args.out / "report_lineage.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
