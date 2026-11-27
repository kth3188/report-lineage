# report-lineage

Count **independent sources**, not reports, and follow **corrections** through relays.

When one observation is repeated or passed on, a plain report count makes it look like
several confirmations (circular reporting). This small, dependency-free Python module
annotates a set of reports with:

- `independent_sources` — distinct original observers, following declared relay bases
  (`based_on`). Repeating or relaying one observation does not make it two.
- `independent_sources_min` — a conservative lower bound for **undeclared** relays: a report
  made after its reporter was sent the same content by someone else is treated as possibly
  dependent, unless the reporter entered an observation time earlier than that delivery.
  This follows the usual remedy for "data incest" in distributed fusion: when the pedigree
  is unknown, fuse conservatively (cf. covariance intersection, Julier & Uhlmann, 1997).
- `corrected` — the report, or any report it was based on, has a later correction, with
  `known_for_seconds`: how long the correction has existed while the old report is still
  held (the idea of age of incorrect information, Maatouk et al., 2020).
- `drift_since(saved, ...)` — which reports a saved decision input used were corrected
  later, directly or through a basis.

It never picks a winner among conflicting values and never claims a report is true. The
counts describe the evidence; they are not truth or confidence scores.

## Input

A ledger history is a list of `(version, entry)` in ledger order. Entries:

```python
{"kind": "report", "known_at": 3.0, "payload": {
    "report": {"id": "r2", "reporter_id": "b", "subject_id": "s", "topic": "t", "value": "x",
               "observed_at": 1.5, "time_basis": "participant_reported"},  # time optional
    "based_on": ["v1"],      # optional: versions this report relays
    "supersedes": None}}     # optional: version this report corrects
{"kind": "publication", "known_at": 2.0, "payload": {"report": "v1", "recipients": ["b"]}}
{"kind": "receipt",     "known_at": 2.0, "payload": {"report": "v1", "recipient": "b"}}
```

A context lists the reports a viewer sees and the decision checks that use them:

```python
from report_lineage import annotate_context

ctx = {"as_of": 8.0,
       "reports": [{"id": "r1", "value": "x"}, {"id": "r2", "value": "x"}],
       "decision_checks": [{"evidence_report_ids": ["r1", "r2"]}]}
annotate_context(ctx, history)
ctx["decision_checks"][0]["support"]
# [{"value": "x", "reports": 2, "independent_sources": 1, "independent_sources_min": 1}]
```

`report_filter(payload) -> bool` hides reports a viewer may not see; a hidden basis is
reported only as "a basis was corrected", without its id.

## Measurement (synthetic only)

`python -m bench.synthetic --out <new folder>` generates trials with known ground truth:
4 observers (25% wrong), 4 relays, corrections of 80% of wrong reports, 2,000 trials,
seed 20261127. Question: "is this value backed by two or more separate observations?"

| relays declared | false confirmations: count reports | declared lineage | conservative bound |
|---|---|---|---|
| 100% | 33.5% | 0.0% | 0.0% |
| 70% | 33.5% | 16.3% | 0.0% |
| 30% | 33.5% | 28.3% | 0.0% |
| 0% | 33.5% | 33.5% | 0.0% |

The price of the bound: when half of the genuine observers were sent another report first,
it misses 23.2% of true confirmations if nobody enters an observation time, 9.3% if half
do, and 0% if all do.

Corrections reaching a saved decision (1,006 affected): direct correction links alone
notice 723; following declared bases notices all 1,006.

These numbers test the software rules on generated data. They say nothing about real
reporting habits. Relays heard outside the ledger (by voice or radio) cannot be detected.

## Development

```
pip install pytest
python -m pytest -q
```

Python 3.9+. No runtime dependencies.

## License

[MIT](LICENSE).
