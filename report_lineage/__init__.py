"""Report lineage: independent sources and corrected bases (see README.md).

Pure functions over ledger history entries ``[(version, entry), ...]``.

What it adds to a decision context, and only describes:
- ``independent_sources``: distinct original observers behind a set of reports. A report
  with no declared basis counts its reporter as the observer; a relay counts the observers
  of the reports it declared in ``based_on``. Repeating, or relaying, one observation does
  not make it two observations (circular reporting).
- ``independent_sources_min``: a conservative lower bound for undeclared relays. In
  distributed data fusion, counting the same information twice through an unknown path is
  the "data incest" problem; when the pedigree is unknown the standard remedy is to fuse
  conservatively. Here a report without a declared basis is treated as possibly dependent
  on an earlier report with the same subject/topic/value from someone else, if the ledger
  shows it was sent to or acknowledged by this reporter before the report was made, unless
  the reporter entered an observation time earlier than that delivery.
- ``corrected``: the report itself, or a report it was based on, has a later correction in
  the ledger, with ``known_for_seconds`` = how long that correction has existed while the
  viewer still holds the old report (the age-of-incorrect-information idea). The correction
  content is not shown here; delivery rules stay in the ledger.

What it never does: pick a winner among conflicting values, change a decision state, or
claim that a report is true. The lower bound marks possible dependence only: a reporter who
was told first and then genuinely saw the same thing is merged too (the price of caution).
"""

from __future__ import annotations

MEANING = (
    "independent_sources counts distinct original observers via declared relay bases; "
    "independent_sources_min also merges reports made after the reporter was sent the same "
    "content (possible undeclared relay). Neither is a truth or confidence score."
)


def _reports(history):
    return {v: e for v, e in history if e["kind"] == "report"}


def _rid(reports, version):
    return reports[version]["payload"]["report"]["id"]


def _heard(history):
    """Per actor: earliest time each report version was sent to or acknowledged by them.

    The result also carries a per-version cache of possible bases, so each report is examined
    once against only the deliveries its own reporter received.
    """
    heard = {}
    for _, e in history:
        p = e["payload"]
        if e["kind"] == "publication":
            who = p.get("recipients", [])
        elif e["kind"] == "receipt":
            who = [p.get("recipient")]
        else:
            continue
        for actor in who:
            seen = heard.setdefault(actor, {})
            report = p.get("report")
            seen[report] = min(seen.get(report, e["known_at"]), e["known_at"])
    return {"by_actor": heard, "cache": {}}


def _possible_bases(version, reports, heard):
    """Earlier same-content reports by others that this reporter was sent before reporting."""
    cache = heard["cache"]
    if version in cache:
        return cache[version]
    if "order" not in heard:
        heard["order"] = {v: i for i, v in enumerate(reports)}
    order = heard["order"]
    cache[version] = result = _find_bases(version, reports, heard["by_actor"], order)
    return result


def _find_bases(version, reports, by_actor, order):
    entry = reports[version]
    if entry["payload"].get("based_on"):
        return []
    r = entry["payload"]["report"]
    own_seen = r.get("observed_at") if r.get("time_basis") == "participant_reported" else None
    found = []
    for v, at in by_actor.get(r.get("reporter_id"), {}).items():
        # Only earlier ledger entries, so the flag and the lower bound always agree.
        if v not in reports or order[v] >= order[version]:
            continue
        q = reports[v]["payload"]["report"]
        if v == version or q.get("reporter_id") == r.get("reporter_id"):
            continue
        if any(q.get(f) != r.get(f) for f in ("subject_id", "topic", "value")):
            continue
        if at > entry["known_at"]:
            continue
        if own_seen is not None and own_seen < at:
            continue  # the reporter says they saw it before being told
        found.append((at, v))
    return [v for _, v in sorted(found)]


def _roots(version, reports, memo, heard=None):
    """Original observers. With ``heard``, also follow possible undeclared relays.

    Bases are always earlier ledger entries (declared bases must pre-exist; a delivery of a
    report follows it), so roots are filled once in ledger order without recursion. A basis
    that is missing or not earlier counts as its own unknown source (cycle-safe).
    """
    if not memo:
        for v, entry in reports.items():
            bases = entry["payload"].get("based_on") or []
            if not bases and heard is not None:
                bases = _possible_bases(v, reports, heard)[:1]
            if not bases:
                memo[v] = frozenset({("observer", entry["payload"]["report"]["reporter_id"])})
            else:
                memo[v] = frozenset().union(
                    *(memo.get(b, frozenset({("unknown", b)})) for b in bases)
                )
    return memo.get(version, frozenset({("unknown", version)}))


def _corrections(reports):
    found = {}
    for v, e in reports.items():
        old = e["payload"].get("supersedes")
        if old:
            found.setdefault(old, []).append(v)
    return found


def _corrected(version, reports, corrections, *, after=None, visible=None, now=None):
    """Corrections of the report itself or of any declared basis, optionally after a time."""
    out, seen, todo = [], set(), [(version, "self")]
    while todo:
        current, relation = todo.pop()
        if current in seen or current not in reports:
            continue
        seen.add(current)
        for fix in corrections.get(current, []):
            if after is None or reports[fix]["known_at"] > after:
                out.append(
                    {
                        # A hidden basis stays anonymous: only "a basis was corrected".
                        "report": _rid(reports, current) if visible(current) else None,
                        "relation": relation,
                        "correction": _rid(reports, fix) if visible(fix) else None,
                        "known_for_seconds": (
                            None if now is None else max(0.0, now - reports[fix]["known_at"])
                        ),
                        "_fix": fix,  # internal; callers remove it before output
                    }
                )
        todo += [(b, "basis") for b in reports[current]["payload"].get("based_on") or []]
    return sorted(
        out, key=lambda x: (x["relation"], x["report"] or "", x["correction"] or "")
    )


def _visibility(reports, report_filter):
    return lambda v: report_filter is None or report_filter(reports[v]["payload"])


def annotate_context(context, history, *, report_filter=None):
    """Add ``lineage`` per visible report and source counts per decision check, in place."""
    reports = _reports(history)
    by_id = {e["payload"]["report"]["id"]: v for v, e in reports.items()}
    corrections = _corrections(reports)
    visible = _visibility(reports, report_filter)
    heard = _heard(history)
    now = context.get("as_of")
    memo, memo_min, lineage = {}, {}, {}
    for r in context.get("reports", []):
        version = by_id.get(r["id"])
        if version is None:
            continue
        bases = reports[version]["payload"].get("based_on") or []
        lineage[r["id"]] = {
            "based_on": [_rid(reports, b) for b in bases if b in reports and visible(b)],
            "relay": bool(bases),
            "possible_relay_of": [
                _rid(reports, b) for b in _possible_bases(version, reports, heard) if visible(b)
            ],
            "independent_sources": len(_roots(version, reports, memo)),
            "corrected": [
                {k: v for k, v in c.items() if k != "_fix"}
                for c in _corrected(version, reports, corrections, visible=visible, now=now)
            ],
        }
    values = {r["id"]: r["value"] for r in context.get("reports", [])}
    for check in context.get("decision_checks", []):
        groups = {}
        for rid in check.get("evidence_report_ids", []):
            if rid in by_id and rid in values:
                groups.setdefault(values[rid], []).append(by_id[rid])
        support = []
        for value, versions in sorted(groups.items()):
            high = frozenset().union(*(_roots(v, reports, memo) for v in versions))
            low = frozenset().union(*(_roots(v, reports, memo_min, heard) for v in versions))
            support.append(
                {
                    "value": value,
                    "reports": len(versions),
                    "independent_sources": len(high),
                    "independent_sources_min": min(len(low), len(high)),
                }
            )
        check["support"] = support
        check["source_overlap"] = any(s["reports"] > s["independent_sources"] for s in support)
        check["possible_hidden_relay"] = any(
            s["independent_sources_min"] < s["independent_sources"] for s in support
        )
        check["corrected_evidence"] = sorted(
            {
                rid
                for rid in check.get("evidence_report_ids", [])
                if rid in lineage and lineage[rid]["corrected"]
            }
        )
    context["lineage"] = lineage
    context["lineage_meaning"] = MEANING
    return context


def drift_since(saved, history, *, report_filter=None):
    """Reports a saved decision input used that were corrected (directly or via a basis) later."""
    reports = _reports(history)
    corrections = _corrections(reports)
    visible = _visibility(reports, report_filter)
    affected, fixes = [], set()
    for version in dict.fromkeys(saved.get("source_versions", [])):
        if version not in reports:
            continue
        via = _corrected(version, reports, corrections, after=saved["now"], visible=visible)
        if via:
            affected.append(
                {"used_report": _rid(reports, version) if visible(version) else None, "via": via}
            )
            fixes |= {v.pop("_fix") for v in via}
    affected.sort(key=lambda x: x["used_report"] or "")
    return {
        # One entry per saved report that is affected; one correction may affect several.
        "corrected_since_saved": affected,
        "affected_reports": len(affected),
        "corrections": len(fixes),
        "meaning": "Later corrections of reports this saved input used; the input itself is unchanged.",
    }
