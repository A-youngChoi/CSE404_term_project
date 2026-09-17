"""Heuristic evaluation of system decisions against the synthetic ground truth.

Matching rule (also shown in the dashboard):
  * A ground-truth positive is an event labelled `expected_intervention = true`.
  * A system intervention is a *delivered* prompt.
  * A delivered prompt matches the earliest unmatched positive whose event time t satisfies
    t <= prompt time <= t + MATCH_TOLERANCE_S. Matched pairs are TP, unmatched prompts FP,
    unmatched positives FN; events with neither are TN.
  * delay = prompt time - positive time (seconds and events).
  * prompt-type accuracy is measured on TP pairs.
  * recovery rate = (recovered + 0.5 x partially recovered) / prompts with a decided outcome.
  * decision agreement compares the 4-way decision on events that carry `expected_decision`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from app.presentation.schemas import GroundTruth, StepRecord

MATCH_TOLERANCE_S = 20.0
CRITICAL = {"high", "critical"}
METHOD = {
    "match_tolerance_s": MATCH_TOLERANCE_S,
    "positive": "ground_truth.expected_intervention == true",
    "system_intervention": "delivered prompt (decision INTERVENE_NOW and not withheld)",
    "precision": "TP / (TP + FP)",
    "recall": "TP / (TP + FN)",
    "f1": "2PR / (P + R)",
    "unnecessary_interruption_rate": "FP / delivered prompts",
    "missed_critical_rate": "FN with severity high|critical / positives with severity high|critical",
    "mean_delay": "mean(prompt time - positive time) over TP",
    "recovery_rate": "(recovered + 0.5 x partially_recovered) / prompts with a decided outcome",
    "decision_agreement": "decision == expected_decision on labelled events",
    "event_accuracy": "(TP + TN) / (TP + FP + FN + TN)",
}


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 3) if b else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return round(2 * p * r / (p + r), 3) if (p + r) else 0.0


def evaluate_steps(steps: list[StepRecord], truth: dict[str, GroundTruth], prompts: Iterable[Any],
                   meta: dict[str, Any]) -> dict[str, Any]:
    prompts = [p for p in prompts if p.delivered]
    index = {s.event_id: i for i, s in enumerate(steps)}
    positives = [s for s in steps if truth.get(s.event_id) and truth[s.event_id].expected_intervention]
    matched: dict[str, str] = {}  # prompt_id -> event_id
    rows_by_event: dict[str, dict[str, Any]] = {}
    delays_s, delays_ev = [], []
    for pos in positives:
        cand = next((p for p in prompts if p.prompt_id not in matched
                     and pos.elapsed_time <= p.created_elapsed <= pos.elapsed_time + MATCH_TOLERANCE_S), None)
        if cand:
            matched[cand.prompt_id] = pos.event_id
            delays_s.append(cand.created_elapsed - pos.elapsed_time)
            delays_ev.append(index[cand.event_id] - index[pos.event_id])
    matched_events = set(matched.values())
    prompt_by_event = {p.event_id: p for p in prompts}

    tp = len(matched)
    fp = len(prompts) - tp
    fn = len(positives) - tp
    critical_pos = [s for s in positives if truth[s.event_id].severity in CRITICAL]
    critical_missed = [s for s in critical_pos if s.event_id not in matched_events]
    type_rows: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "predicted": 0, "correct": 0})
    for s in positives:
        if truth[s.event_id].expected_prompt_type:
            type_rows[truth[s.event_id].expected_prompt_type]["expected"] += 1
    for p in prompts:
        type_rows[p.prompt_type]["predicted"] += 1
        ev = matched.get(p.prompt_id)
        if ev and truth[ev].expected_prompt_type == p.prompt_type:
            type_rows[p.prompt_type]["correct"] += 1
    type_correct = sum(r["correct"] for r in type_rows.values())

    decision_labelled = decision_agree = 0
    for s in steps:
        gt = truth.get(s.event_id)
        sys_int = s.event_id in prompt_by_event
        gt_int = bool(gt and gt.expected_intervention)
        if gt_int and s.event_id in matched_events:
            label = "TP"
        elif sys_int and prompt_by_event[s.event_id].prompt_id in matched:
            label = "TP_late"
        elif sys_int:
            label = "FP"
        elif gt_int:
            label = "FN"
        else:
            label = "TN"
        expected_decision = (gt.expected_decision if gt else None) or ("INTERVENE_NOW" if gt_int else None)
        if expected_decision:
            decision_labelled += 1
            decision_agree += int(expected_decision == s.decision.decision)
        p = prompt_by_event.get(s.event_id)
        rows_by_event[s.event_id] = {
            "event_id": s.event_id, "step": s.step, "elapsed": s.elapsed_time,
            "gt_issue": gt.issue if gt else "none", "gt_expected_intervention": gt_int,
            "gt_prompt_type": gt.expected_prompt_type if gt else None, "gt_decision": expected_decision,
            "gt_severity": gt.severity if gt else "none", "gt_expected_outcome": gt.expected_outcome if gt else None,
            "gt_note": gt.note if gt else None, "gt_target": gt.target if gt else None,
            "decision": s.decision.decision, "detected_issue": s.decision.detected_issue,
            "confidence": s.decision.confidence, "prompt_id": p.prompt_id if p else None,
            "prompt_type": p.prompt_type if p else None, "prompt_outcome": p.outcome if p else None,
            "match": label, "matched_event_id": matched.get(p.prompt_id) if p else None,
            "decision_agrees": (expected_decision == s.decision.decision) if expected_decision else None,
            "type_correct": (p.prompt_type == truth[matched[p.prompt_id]].expected_prompt_type)
            if p and p.prompt_id in matched else None,
            "mismatch_notes": _mismatch_notes(label, gt, s, p),
        }
    tn = sum(1 for r in rows_by_event.values() if r["match"] == "TN")

    outcomes = defaultdict(int)
    for p in prompts:
        outcomes[p.outcome] += 1
    decided = outcomes["recovered"] + outcomes["partially_recovered"] + outcomes["not_recovered"]
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    decisions = [s.decision for s in steps]
    return {
        **meta,
        "counts": {"events": len(steps), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "positives": len(positives),
                   "delivered": len(prompts),
                   "suppressed": sum(1 for d in decisions if d.decision == "SUPPRESS_DUE_TO_RECENT_INTERVENTION"),
                   "waits": sum(1 for d in decisions if d.decision == "WAIT_AND_OBSERVE"),
                   "issues_detected": len({d.issue_id for d in decisions if d.issue_id}),
                   "gt_issues": sum(1 for s in steps if truth.get(s.event_id) and truth[s.event_id].issue != "none"),
                   "critical_positives": len(critical_pos), "critical_missed": len(critical_missed),
                   "type_correct": type_correct, "decision_labelled": decision_labelled,
                   "decision_agree": decision_agree, "outcome_decided": decided,
                   "recovered": outcomes["recovered"], "partially_recovered": outcomes["partially_recovered"],
                   "not_recovered": outcomes["not_recovered"], "pending": outcomes["pending"],
                   "delay_sum_s": sum(delays_s), "delay_sum_events": sum(delays_ev), "delay_n": len(delays_s),
                   "confidence_sum": sum(d.confidence for d in decisions)},
        "metrics": _metrics(tp, fp, fn, tn, len(critical_pos), len(critical_missed), type_correct, decision_labelled,
                            decision_agree, outcomes, decided, sum(delays_s), sum(delays_ev), len(delays_s),
                            sum(d.confidence for d in decisions), len(decisions)),
        "prompt_types": {k: dict(v) | {"precision": _ratio(v["correct"], v["predicted"]),
                                       "recall": _ratio(v["correct"], v["expected"])}
                         for k, v in sorted(type_rows.items())},
        "rows": list(rows_by_event.values()),
        "precision": precision, "recall": recall,
    }


def _metrics(tp, fp, fn, tn, crit, crit_missed, type_correct, dec_n, dec_ok, outcomes, decided, delay_s, delay_ev,
             delay_n, conf_sum, n_decisions) -> dict[str, Any]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "precision": precision, "recall": recall, "f1": _f1(precision, recall),
        "false_positives": fp, "false_negatives": fn,
        "unnecessary_interruption_rate": _ratio(fp, tp + fp),
        "missed_critical_rate": _ratio(crit_missed, crit),
        "mean_delay_s": _ratio(delay_s, delay_n), "mean_delay_events": _ratio(delay_ev, delay_n),
        "recovery_rate": _ratio(outcomes["recovered"] + 0.5 * outcomes["partially_recovered"], decided),
        "prompt_type_accuracy": _ratio(type_correct, tp),
        "decision_agreement": _ratio(dec_ok, dec_n),
        "event_accuracy": _ratio(tp + tn, tp + fp + fn + tn),
        "mean_confidence": _ratio(conf_sum, n_decisions),
    }


def _mismatch_notes(label: str, gt: GroundTruth | None, step: StepRecord, prompt: Any) -> list[str]:
    notes = []
    d = step.decision
    if label == "FP":
        notes.append(f"System intervened for {d.detected_issue} but ground truth expected no prompt"
                     + (f" ({gt.expected_decision})." if gt and gt.expected_decision else "."))
    if label == "FN":
        notes.append(f"Ground truth expected {gt.expected_prompt_type} for {gt.issue}; system decided {d.decision}"
                     + (f" on {d.detected_issue}." if d.detected_issue != "none" else " (no issue detected)."))
        if gt.issue != d.detected_issue and d.detected_issue != "none":
            notes.append(f"Issue differs: expected {gt.issue}, detected {d.detected_issue}.")
        if d.decision == "WAIT_AND_OBSERVE":
            notes.append(f"Utility {d.utility:.2f} stayed below the intervention threshold.")
    if prompt is not None and gt and gt.expected_prompt_type and prompt.prompt_type != gt.expected_prompt_type \
            and label == "TP":
        notes.append(f"Prompt type differs: expected {gt.expected_prompt_type}, got {prompt.prompt_type}.")
    if gt and gt.expected_decision and gt.expected_decision != d.decision:
        notes.append(f"Decision differs: expected {gt.expected_decision}, got {d.decision}.")
    if gt and gt.target and d.target and gt.target != d.target and label == "TP":
        notes.append(f"Target differs: expected {gt.target}, got {d.target}.")
    return notes


def aggregate(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Micro-averaged metrics over sessions, plus per-language, per-scenario and per-prompt-type slices."""

    def combine(items: list[dict[str, Any]]) -> dict[str, Any]:
        c = defaultdict(float)
        for s in items:
            for k, v in s["counts"].items():
                c[k] += v
        outcomes = {"recovered": c["recovered"], "partially_recovered": c["partially_recovered"],
                    "not_recovered": c["not_recovered"], "pending": c["pending"]}
        return {"sessions": len(items), "counts": {k: int(v) if float(v).is_integer() else round(v, 3)
                                                   for k, v in c.items()},
                "metrics": _metrics(c["tp"], c["fp"], c["fn"], c["tn"], c["critical_positives"], c["critical_missed"],
                                    c["type_correct"], c["decision_labelled"], c["decision_agree"], outcomes,
                                    c["outcome_decided"], c["delay_sum_s"], c["delay_sum_events"], c["delay_n"],
                                    c["confidence_sum"], c["events"])}

    by_lang = defaultdict(list)
    by_scenario = defaultdict(list)
    types: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "predicted": 0, "correct": 0})
    for s in sessions:
        by_lang[s["language"]].append(s)
        by_scenario[s["scenario_type"]].append(s)
        for t, v in s["prompt_types"].items():
            for k in ("expected", "predicted", "correct"):
                types[t][k] += v[k]
    return {
        "overall": combine(sessions),
        "by_language": {k: combine(v) for k, v in sorted(by_lang.items())},
        "by_scenario": {k: combine(v) for k, v in sorted(by_scenario.items())},
        "by_prompt_type": {t: v | {"precision": _ratio(v["correct"], v["predicted"]),
                                   "recall": _ratio(v["correct"], v["expected"]),
                                   "f1": _f1(_ratio(v["correct"], v["predicted"]), _ratio(v["correct"], v["expected"]))}
                           for t, v in sorted(types.items())},
        "method": METHOD,
    }
