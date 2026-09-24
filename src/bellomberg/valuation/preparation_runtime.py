"""Local, explicit spending policy for the shared valuation preparer.

This factory neither starts a worker nor migrates the portfolio database. A new
installation is disabled. Application code, never a model/tool argument, chooses
the policy path and trigger. No pilot authorization is inherited. One explicit
authorization UUID owns one immutable lifetime cap and durable response journal;
there is no calendar reset or automatic refill.
"""
from copy import deepcopy
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from uuid import UUID
import sqlite3


TRIGGERS = frozenset({"committee", "portfolio", "watchlist", "filing_diff", "startup", "manual_refresh", "price", "guidance"})


def read_policy(path):
    """Read only; malformed/ambiguous configuration is an error, never a default."""
    try:
        body = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate authorization field: " + key)
            result[key] = value
        return result
    policy = json.loads(body, object_pairs_hook=unique)
    if (not isinstance(policy, dict) or type(policy.get("version")) is not int
            or policy["version"] != 1 or type(policy.get("enabled")) is not bool):
        raise ValueError("valuation authorization version1 and explicit enabled boolean required")
    expected = {"version", "enabled"}
    if policy["enabled"]:
        expected |= {"authorization_id", "authorized_usd", "triggers"}
    if set(policy) != expected:
        raise ValueError("valuation authorization fields differ from policy schema")
    if not policy["enabled"]:
        return policy
    identity = policy["authorization_id"]
    try:
        if not isinstance(identity, str) or str(UUID(identity)) != identity:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as exc:
        raise ValueError("explicit authorization_id UUID required") from exc
    value = policy["authorized_usd"]
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("amount type")
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal(2**63 - 1) / Decimal(10**9):
            raise ValueError("amount outside positive journal range")
        nano = amount * 10**9
        if nano != nano.to_integral_value():
            raise ValueError("precision exceeds journal units")
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("positive finite authorized_usd in nanoUSD precision required") from exc
    triggers = policy["triggers"]
    if (not isinstance(triggers, list) or not triggers or any(type(item) is not str for item in triggers)
            or len(set(triggers)) != len(triggers) or not set(triggers) <= TRIGGERS):
        raise ValueError("explicit supported unique authorization triggers required")
    return {**policy, "authorized_usd": str(amount), "triggers": sorted(triggers)}


class PreparationRuntime:
    def __init__(self, policy_path, *, data_root, archive_root, output_dir, proposer_factory=None):
        self.policy_path = Path(policy_path).resolve()
        self.data_root = Path(data_root).resolve()
        self.archive_root, self.output_dir = Path(archive_root).resolve(), Path(output_dir).resolve()
        self.proposer_factory = proposer_factory

    def status(self):
        policy = read_policy(self.policy_path)
        if policy is None:
            return {"status": "disabled", "reason": "configuration_absent"}
        if not policy["enabled"]:
            return {"status": "disabled", "reason": "explicitly_disabled"}
        return {"status": "configured", "reason": "explicit_local_authorization",
                "authorization_id": policy["authorization_id"], "authorized_usd": policy["authorized_usd"],
                "triggers": policy["triggers"], "budget_period": "authorization_lifetime"}

    def _require(self, trigger):
        policy = read_policy(self.policy_path)
        if (trigger not in TRIGGERS or not policy or not policy["enabled"]
                or trigger not in policy["triggers"]):
            raise PermissionError("valuation preparation authorization absent for trigger: " + str(trigger))
        return policy

    def _unchanged(self, trigger, policy):
        if self._require(trigger) != policy:
            raise PermissionError("valuation preparation authorization changed; rebind explicitly")

    def budget_audit(self):
        """Read the current authorization journal without creating or repairing it."""
        policy = read_policy(self.policy_path)
        if not policy or not policy["enabled"]:
            return {"state": "absent", "reason": "authorization_disabled"}
        journal = self.data_root / "valuation_ai_budgets" / (policy["authorization_id"] + ".sqlite3")
        if not journal.is_file():
            return {"state": "absent", "reason": "budget_journal_absent"}
        import time
        deferred, retry_limit = False, False
        with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True)) as conn:
            cap = conn.execute("SELECT cap FROM authorization WHERE id=1").fetchone()
            rows = conn.execute("SELECT state,cost,receipt FROM requests").fetchall()
            conn.row_factory = sqlite3.Row
            from .preparation_rejections import retry_allowed, verify_history, validate_proof, MAX_ATTEMPTS
            for row in conn.execute("SELECT * FROM requests WHERE state='rejected'"):
                if not retry_allowed(conn, row, check_deadline=False):
                    raise ValueError('unverified stored rejection receipt')
                deferred |= time.time() < validate_proof(json.loads(row['receipt']))['retry_at']
                retry_limit |= len(verify_history(conn, row['key'])) >= MAX_ATTEMPTS
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_rejections'").fetchone():
                for (key,) in conn.execute('SELECT DISTINCT key FROM request_rejections'):
                    verify_history(conn, key)
        expected = int(Decimal(policy["authorized_usd"]) * 10**9)
        if cap is None or type(cap[0]) is not int or cap[0] != expected:
            raise ValueError("authorization journal cap mismatch")
        if any(cost is not None and (type(cost) is not int or cost < 0) for _, cost, _ in rows):
            raise ValueError("invalid stored request cost")
        from .preparation_ai import _nano
        for state, cost, receipt in rows:
            if state not in ("received", "reserved", "unknown", "overrun", "rejected"):
                raise ValueError("invalid stored request state")
            if state == "received":
                proof = json.loads(receipt)
                if not proof.get("response_id") or _nano(proof.get("cost_usd")) != cost:
                    raise ValueError("stored request cost differs from provider receipt")
        unknown = sum(cost is None or state not in ('received', 'rejected') for state, cost, _ in rows)
        amount = sum(cost for _, cost, _ in rows if cost is not None)
        if amount > expected:
            raise ValueError("authorization journal total exceeds cap")
        state = 'unresolved' if unknown else 'retry_limit' if retry_limit else 'deferred' if deferred else 'reconciled'
        return {"state": state, "request_count": len(rows),
                "unresolved_requests": unknown, "known_cost_usd": str(Decimal(amount) / Decimal(10**9)),
                "authorized_usd": policy["authorized_usd"], "authorization_id": policy["authorization_id"]}

    def proposer_for(self, trigger, *, opening_excerpt_manifest=None, forecast_excerpt_manifest=None,
                     opening_drivers_per_stage=None):
        from .preparation_ai import configured_proposer, StagedProposer
        if trigger == "price":
            raise PermissionError("price authorization cannot invoke AI preparation")
        policy = self._require(trigger)
        journal = (self.data_root / "valuation_ai_budgets" / (policy["authorization_id"] + ".sqlite3")).resolve()
        if not journal.is_relative_to(self.data_root):
            raise ValueError("valuation budget journal outside runtime data root")
        proposer = (self.proposer_factory or configured_proposer)(
            journal, authorized_usd=policy["authorized_usd"])
        def guarded(dossier, contract):
            self._unchanged(trigger, policy)
            return proposer(dossier, contract)
        project_context = getattr(proposer, 'prepare_context', None)
        if callable(project_context):
            def prepare_context(dossier, contract, **options):
                self._unchanged(trigger, policy)
                return project_context(dossier, contract, **options)
            guarded.prepare_context = prepare_context
        return StagedProposer(guarded, opening_excerpt_manifest=opening_excerpt_manifest,
            forecast_excerpt_manifest=forecast_excerpt_manifest, opening_drivers_per_stage=opening_drivers_per_stage)

    def preparer_for(self, trigger, *, filing_results=(), opening_excerpt_manifest=None,
                     forecast_excerpt_manifest=None, opening_drivers_per_stage=None):
        """Bind the existing bundle->workbook service; disabled state is explicit."""
        if self.status()["status"] == "disabled":
            return None
        policy = self._require(trigger)
        proposer = self.proposer_for(trigger, opening_excerpt_manifest=opening_excerpt_manifest,
            forecast_excerpt_manifest=forecast_excerpt_manifest, opening_drivers_per_stage=opening_drivers_per_stage)
        filings = deepcopy(filing_results)
        def prepare(bundle):
            self._unchanged(trigger, policy)
            from .preparation_service import collect_and_prepare
            return collect_and_prepare(bundle, archive_root=self.archive_root, output_dir=self.output_dir,
                                       filing_results=deepcopy(filings), propose=proposer)
        return prepare


def installation_runtime():
    """The subprocess and API resolve the same private policy, never caller input."""
    from bellomberg.core.paths import DATA_DIR, REPORT_DIR
    return PreparationRuntime(DATA_DIR / "valuation_automation.json", data_root=DATA_DIR,
                              archive_root=DATA_DIR / "filing_archive", output_dir=REPORT_DIR)


def bind_installation_preparer(trigger):
    """Declare configuration failures without disabling the rest of a committee run."""
    try:
        runtime = installation_runtime()
        state = runtime.status()
        if state["status"] == "disabled":
            return {"preparer": None, "state": state}
        if trigger not in state["triggers"]:
            return {"preparer": None, "state": {"status": "disabled", "reason": "trigger_not_authorized"}}
        return {"preparer": runtime.preparer_for(trigger), "state": {**state, "status": "enabled"}}
    except Exception as exc:
        return {"preparer": None, "state": {"status": "error", "reason": type(exc).__name__ + ": " + str(exc)[:500]}}


def preparation_status_text(state, *, language=None):
    """State read at binding; it is not a promise of data coverage or an Excel."""
    from bellomberg.core.language import text
    label = text("PREPARAZIONE AUTOMATICA MODELLI", "AUTOMATIC MODEL PREPARATION", language=language)
    if state.get("status") == "enabled":
        detail = text(
            "Preparatore automatico abilitato entro il budget locale. Ipotesi non approvate dal PM; "
            "fonti, costo e validazione possono fermare ogni richiesta. Un Excel esiste solo se il tool ne restituisce il file valido.",
            "Automatic preparation enabled within the local budget. Assumptions are not PM-approved; "
            "sources, cost and validation may block each request. An Excel exists only when the tool returns its valid file.",
            language=language)
    else:
        detail = text(
            "Preparatore automatico non disponibile: ", "Automatic preparation unavailable: ", language=language
        ) + str(state.get("reason") or "state_unknown") + ". " + text(
            "I modelli esistenti e gli input espliciti restano utilizzabili; non promettere un nuovo Excel senza esito valido.",
            "Existing models and explicit inputs remain usable; do not promise a new Excel without a valid result.",
            language=language)
    return label + ": " + detail
