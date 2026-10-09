"""Contesto offline e checkpoint immutabile dei controlli fatti weekly."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

POLICY = "weekly-facts/1"
STAGE = "memo_facts_v1"


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def collect_memo_facts_context(run_id, cutoff, request, receipts, *, evidence_followup=False):
    """Solo contenuti congelati. Il checkpoint iniziale NON attesta retry/nudge."""
    parts, missing, issues = [], ["accepted_wire_request_and_continuations"], []
    request = request if isinstance(request, dict) else {}
    for key, role, kind in (("system", "system", "instruction"),
                            ("user_message", "user", "initial_request")):
        text = request.get(key)
        if isinstance(text, str) and text:
            parts.append({"id": "capo_request:" + key, "role": role, "kind": kind,
                          "text": text, "sha256": sha256(text.encode("utf-8")).hexdigest()})
        else:
            missing.append("capo_request:" + key)
    entries = []
    if not isinstance(receipts, list):
        issues.append({"code": "RECEIPTS_UNAVAILABLE"})
        receipts = []
    for index, receipt in enumerate(receipts):
        entries.append({"id": "receipt-" + str(index), "sha256": _digest(receipt),
                        "receipt": deepcopy(receipt), "attribution": "UNAVAILABLE"})
    snapshot = {"version": POLICY, "run_id": run_id, "cutoff": cutoff,
            "context": {"status": "PARTIAL" if parts else "UNAVAILABLE",
                        "attestation": {"status": "UNVERIFIED", "basis": "initial_request_checkpoint",
                                        "request_ids": []},
                        "parts": parts, "missing_parts": missing},
            "receipts": entries, "issues": issues}
    if evidence_followup:
        from bellomberg.core.evidence_followup_policy import project_receipts
        snapshot['evidence_scope_followup'] = project_receipts(receipts, run_id=run_id)
    return snapshot


def checkpoint_memo_facts(store, source_memo):
    """Riusa il risultato dopo crash; integrita store fuori dai catch diagnostici."""
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    contract = store.context.get("contract", {})
    from bellomberg.core.evidence_followup_policy import enabled as followup_enabled
    followup = followup_enabled(contract)
    if "memo_facts_policy" not in contract:
        return ""
    policy = contract["memo_facts_policy"]
    if policy != POLICY:
        raise WeeklyRunBlocked("Policy controllo fatti non compatibile")
    if store.get("memo_validated") is not None:
        return ""
    board = SimpleNamespace(data={}, tool_receipts=None)
    store.restore(board)
    # Il callback del Capo salva la Blackboard, non uno stage capo_request separato.
    request = board.data.get("_capo_request") if isinstance(board.data, dict) else None
    snapshot = collect_memo_facts_context(store.run_id, store.context.get("research_started_at"),
                                          request, board.tool_receipts, **({'evidence_followup': True} if followup else {}))
    language = store.context.get("language", "it")
    input_hash = _digest({"source_memo": source_memo, "snapshot": snapshot,
                          "language": language, "policy": policy})
    saved = store.get(STAGE)
    if saved is not None:
        if saved.get("input_sha256") != input_hash:
            raise WeeklyRunBlocked("Input controllo fatti modificato")
        return saved["block"]
    counters = {"audit_attempted": 0, "audit_completed": 0,
                "render_attempted": 0, "render_completed": 0}
    report, error = None, None
    try:
        counters["audit_attempted"] += 1
        from bellomberg.reporting.memo_facts import audit_memo_facts
        report = audit_memo_facts(source_memo, snapshot, language=language, version=policy)
        if not isinstance(report, dict):
            raise TypeError("Invalid audit report")
        _digest(report)  # JSON finito prima di dichiarare audit completato.
        counters["audit_completed"] += 1
    except Exception as exc:
        error = {"category": "AUDITOR", "exception_type": type(exc).__name__}
    if error is None:
        try:
            counters["render_attempted"] += 1
            from bellomberg.reporting.memo_facts import render_memo_facts
            block = render_memo_facts(report)
            if not isinstance(block, str) or not block.strip():
                raise ValueError("Empty facts rendering")
            counters["render_completed"] += 1
        except Exception as exc:
            error = {"category": "RENDERER", "exception_type": type(exc).__name__}
    if error is not None:
        title = "Controllo fatti" if language == "it" else "Facts check"
        block = ("## " + title + " — CHECK_UNAVAILABLE\n\n"
                 + error["category"] + ": " + error["exception_type"] + ". "
                 + "; ".join(key + "=" + str(value) for key, value in counters.items()))
    payload = {"version": policy, "input_sha256": input_hash, "report": report,
               "block": block, "counters": counters, "error": error}
    store.complete(STAGE, payload)
    return block
