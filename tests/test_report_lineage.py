import time

from report_lineage import annotate_context, drift_since


def rep(rid, reporter, value="x", *, based_on=(), supersedes=None, known_at=1.0,
        observed_at=None, hidden=False):
    report = {"id": rid, "reporter_id": reporter, "subject_id": "s", "topic": "t", "value": value}
    if observed_at is not None:
        report.update(observed_at=observed_at, time_basis="participant_reported")
    payload = {"report": report, "supersedes": supersedes}
    if based_on:
        payload["based_on"] = list(based_on)
    if hidden:
        payload["source"] = {"hidden": True}
    return {"kind": "report", "payload": payload, "known_at": known_at}


def sent(version, actor, known_at):
    return {"kind": "publication", "known_at": known_at,
            "payload": {"report": version, "recipients": [actor]}}


def context(history, ids=None, as_of=None):
    reports = [e["payload"]["report"] for _, e in history if e["kind"] == "report"]
    if ids is not None:
        reports = [r for r in reports if r["id"] in ids]
    ctx = {"as_of": as_of, "reports": [{"id": r["id"], "value": r["value"]} for r in reports]}
    ctx["decision_checks"] = [{"evidence_report_ids": [r["id"] for r in ctx["reports"]]}]
    return annotate_context(ctx, history)


def support(ctx):
    return {s["value"]: s for s in ctx["decision_checks"][0]["support"]}


def test_declared_relay_is_not_a_second_source():
    h = [("v1", rep("r1", "a")), ("v2", rep("r2", "b", based_on=["v1"], known_at=2.0))]
    ctx = context(h)
    assert support(ctx)["x"] == {
        "value": "x", "reports": 2, "independent_sources": 1, "independent_sources_min": 1
    }
    assert ctx["decision_checks"][0]["source_overlap"] is True
    assert ctx["lineage"]["r2"]["based_on"] == ["r1"] and ctx["lineage"]["r2"]["relay"]


def test_repetition_by_the_same_observer_is_one_source():
    h = [("v1", rep("r1", "a")), ("v2", rep("r2", "a", known_at=2.0))]
    assert support(context(h))["x"]["independent_sources"] == 1


def test_undeclared_relay_lowers_only_the_conservative_bound():
    h = [("v1", rep("r1", "a")), ("p1", sent("v1", "b", 2.0)), ("v2", rep("r2", "b", known_at=3.0))]
    ctx = context(h)
    s = support(ctx)["x"]
    assert (s["independent_sources"], s["independent_sources_min"]) == (2, 1)
    assert ctx["decision_checks"][0]["possible_hidden_relay"] is True
    assert ctx["lineage"]["r2"]["possible_relay_of"] == ["r1"]


def test_observation_before_being_told_is_not_a_possible_relay():
    h = [("v1", rep("r1", "a")), ("p1", sent("v1", "b", 2.0)),
         ("v2", rep("r2", "b", known_at=3.0, observed_at=1.5))]
    s = support(context(h))["x"]
    assert (s["independent_sources"], s["independent_sources_min"]) == (2, 2)


def test_conflict_stays_a_conflict_with_counts_per_value():
    h = [("v1", rep("r1", "a", "x")), ("v2", rep("r2", "b", "y", known_at=2.0))]
    s = support(context(h))
    assert set(s) == {"x", "y"}
    assert s["x"]["independent_sources"] == s["y"]["independent_sources"] == 1


def test_corrected_basis_is_flagged_with_its_age_and_saved_input_drifts():
    h = [("v1", rep("r1", "a")), ("v2", rep("r2", "b", based_on=["v1"], known_at=2.0)),
         ("v3", rep("r3", "a", "y", supersedes="v1", known_at=5.0))]
    ctx = context(h, ids={"r2"}, as_of=8.0)
    assert ctx["lineage"]["r2"]["corrected"] == [
        {"report": "r1", "relation": "basis", "correction": "r3", "known_for_seconds": 3.0}
    ]
    drift = drift_since({"now": 3.0, "source_versions": ["v2"]}, h)
    assert drift["affected_reports"] == drift["corrections"] == 1
    assert drift_since({"now": 6.0, "source_versions": ["v2"]}, h)["affected_reports"] == 0


def test_one_correction_affecting_two_saved_reports_is_counted_once():
    h = [("v1", rep("r1", "a")), ("v2", rep("r2", "b", based_on=["v1"], known_at=2.0)),
         ("v3", rep("r3", "a", "y", supersedes="v1", known_at=5.0))]
    drift = drift_since({"now": 3.0, "source_versions": ["v1", "v2"]}, h)
    assert drift["affected_reports"] == 2 and drift["corrections"] == 1


def test_unknown_basis_is_one_unnamed_source():
    h = [("v1", rep("r1", "a", based_on=["missing"])), ("v2", rep("r2", "b", based_on=["v1"]))]
    assert support(context(h))["x"]["independent_sources"] == 1
    assert drift_since({"now": 0, "source_versions": ["v2"]}, h)["corrected_since_saved"] == []


def test_hidden_basis_stays_anonymous_in_correction_flags():
    h = [("v1", rep("r1", "a", hidden=True)), ("v2", rep("r2", "b", based_on=["v1"], known_at=2.0)),
         ("v3", rep("r3", "a", supersedes="v1", known_at=3.0, hidden=True))]
    hide = lambda p: "source" not in p  # noqa: E731
    ctx = {"as_of": 4.0, "reports": [{"id": "r2", "value": "x"}], "decision_checks": []}
    annotate_context(ctx, h, report_filter=hide)
    assert ctx["lineage"]["r2"]["corrected"] == [
        {"report": None, "relation": "basis", "correction": None, "known_for_seconds": 1.0}
    ]
    assert "r1" not in str(ctx) and "r3" not in str(ctx)
    since = drift_since({"now": 2.5, "source_versions": ["v2"]}, h, report_filter=hide)
    assert "r1" not in str(since) and since["corrections"] == 1


def test_cost_stays_near_linear_and_long_chains_do_not_recurse():
    n, h, t = 4000, [], 0.0
    for i in range(n):
        t += 1
        h.append((f"p{i}", sent(f"v{i-1}", f"a{i}", t)))
        h.append((f"v{i}", rep(f"r{i}", f"a{i}", str(i % 3), known_at=t + 0.5)))
    began = time.perf_counter()
    context(h)
    assert time.perf_counter() - began < 2.0
    chain = [(v, rep(e["payload"]["report"]["id"], e["payload"]["report"]["reporter_id"], "0",
                     known_at=e["known_at"])) if e["kind"] == "report" else (v, e) for v, e in h]
    s = support(context(chain))["0"]
    assert s["independent_sources"] == n and s["independent_sources_min"] == 1


def test_synthetic_comparison_known_answers():
    from bench.synthetic import run

    kw = dict(observers=4, relays=4, error=0.25, correct=0.8, reach=0.5)
    full = run(300, 7, declare=1.0, told=0.0, timed=0.0, **kw)
    none = run(300, 7, declare=0.0, told=0.0, timed=0.0, **kw)
    assert full["false_confirm_rate"]["declared"] == 0.0
    assert full["false_confirm_rate"]["count"] > 0.1
    assert none["false_confirm_rate"]["declared"] == none["false_confirm_rate"]["count"]
    assert none["false_confirm_rate"]["conservative"] == 0.0
    assert full["drift_noticed"]["direct_only"] < full["drift_noticed"]["with_lineage"]
    told = run(300, 7, declare=0.0, told=0.5, timed=0.0, **kw)
    timed = run(300, 7, declare=0.0, told=0.5, timed=1.0, **kw)
    assert told["missed_confirm_rate"]["conservative"] > 0.05
    assert timed["missed_confirm_rate"]["conservative"] == 0.0
