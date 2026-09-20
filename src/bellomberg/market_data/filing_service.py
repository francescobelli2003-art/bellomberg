"""Orchestrazione I-20: profili revisionati, run persistenti, giudizio e indice derivato."""
import hashlib
import json
from functools import partial
from pathlib import Path

from bellomberg.market_data.filing_judgment import citation_catalog, judge_filing

MAX_INDEX_CHUNKS = 200
MAX_INDEX_TEXT = 2000


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _evidence_key(run, result, judge_identity="disabled"):
    pair = result.get("coppia")
    if not pair:
        return None
    try:
        diff = result.get("confronto_corrente") or result.get("confronto_storico") or {}
        coverage = result.get("copertura") or {}
        freshness = result.get("freschezza") or {}
        raw = {"before": pair["prima"]["sha256"], "after": pair["dopo"]["sha256"],
               "profile": run["profile_sha256"], "version": run["profile_version"],
               "document_language": run["profile"]["lingua"],
               "judgment_language": run["judgment_language"], "judgment_version": 1,
               "judge": judge_identity,
               "context": {"pair_scope": pair.get("ambito"), "pipeline_status": result.get("stato"),
                           "diff_status": diff.get("stato"), "historical": bool(result.get("confronto_storico")),
                           "coverage_status": coverage.get("stato"), "coverage_limits": coverage.get("limiti"),
                           "latest_unverified": result.get("ultimo_non_verificato"),
                           "freshness_status": freshness.get("stato")}}
        if any(not isinstance(v, str) or not v for v in (raw["before"], raw["after"], raw["document_language"], raw["judgment_language"])):
            return None
        return hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()
    except (KeyError, TypeError):
        return None


def default_indexer(run, result, judgment, *, chroma_path=None):
    """Chroma e' solo indice ricostruibile; SQLite conserva l'evidenza intera."""
    if chroma_path is None:
        raise ValueError("chroma_path esplicito obbligatorio: non usare il Chroma vivo implicitamente")
    diff = result.get("confronto_corrente") or result.get("confronto_storico") or {}
    cited = citation_catalog(result) if diff.get("stato") in ("ok", "parziale") else {}
    findings = judgment.get("findings") or []
    if not cited and not findings:
        return {"status": "skipped", "reason": "nessun estratto o finding da indicizzare"}
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(chroma_path),
                                       settings=Settings(anonymized_telemetry=False, allow_reset=False))
    collection = client.get_or_create_collection("filing_diffs")
    ids, documents, metadata = [], [], []
    total = len(cited) + len(findings)
    remaining = MAX_INDEX_CHUNKS
    text_truncated = 0
    for citation_id, item in cited.items():
        if remaining <= 0:
            break
        remaining -= 1
        ids.append(f"filing-diff-{run['id']}-{citation_id}")
        documents.append(item["testo"][:MAX_INDEX_TEXT])
        text_truncated += len(item["testo"]) > MAX_INDEX_TEXT
        metadata.append({"kind": "diff", "ticker": run["ticker"], "run_id": int(run["id"]),
                         "citation_id": citation_id, "url": item["url"],
                         "sha256": item["sha256"], "offset_start": int(item["inizio"]),
                         "offset_end": int(item["fine"]),
                         "evidence_key": run.get("evidence_key") or "n.d."})
    for n, finding in enumerate(findings):
        if remaining <= 0:
            break
        remaining -= 1
        ids.append(f"filing-judgment-{run['id']}-{n}")
        document = finding["category"] + ": " + finding["assessment"]
        documents.append(document[:MAX_INDEX_TEXT])
        text_truncated += len(document) > MAX_INDEX_TEXT
        metadata.append({"kind": "judgment", "ticker": run["ticker"], "run_id": int(run["id"]),
                         "citations": ",".join(finding["citations"]),
                         "evidence_key": run.get("evidence_key") or "n.d."})
    collection.upsert(ids=ids, documents=documents, metadatas=metadata)
    truncated = total > len(ids) or text_truncated > 0
    return {"status": "parziale" if truncated else "ok",
            "reason": "indice limitato: chunk o testi troncati; SQLite conserva contenuto intero" if truncated else None,
            "count": len(ids), "total_chunks": total, "truncated_text_chunks": text_truncated,
            "diff_chunks": sum(m["kind"] == "diff" for m in metadata),
            "judgment_chunks": sum(m["kind"] == "judgment" for m in metadata)}


class FilingService:
    def __init__(self, store, archive_root, pipeline=None, judge=None, indexer=None):
        self.store = store
        self.archive_root = Path(archive_root)
        if not self.archive_root.is_absolute():
            raise ValueError("archive_root deve essere assoluto")
        if pipeline is None:
            from bellomberg.market_data.filing_pipeline import esegui_profilo
            pipeline = esegui_profilo
        self.pipeline = pipeline
        self.judge = judge or judge_filing
        self._default_judge = judge is None
        self.indexer = indexer if indexer is not None else partial(
            default_indexer, chroma_path=Path(store.db_path).resolve().parent / "chroma")

    def queue(self, ticker, trigger="manual", language=None):
        """Claim persistente, atomico. Il profilo e' congelato nel run queued."""
        return self.store.start_run(ticker, trigger, language=language)

    def execute(self, run_id):
        run = self.store.claim_execution(run_id)
        result = None
        judgment = {"status": "skipped", "findings": [], "reason": "giudizio qualitativo disabilitato", "model": None, "usage": None}
        index = {"status": "skipped", "reason": "nessun estratto indicizzabile"}
        evidence_key = None
        try:
            archive = self.archive_root / run["ticker"] / "documents"
            archive.mkdir(parents=True, exist_ok=True)
            result = self.pipeline(run["profile"], archivio=archive)
            if not isinstance(result, dict) or result.get("stato") not in ("ok", "parziale", "errore", "non_disponibile"):
                raise ValueError("pipeline: risultato o stato non valido")
            evidence_key = _evidence_key(run, result)
            if run["qualitative_enabled"]:
                diff = result.get("confronto_corrente") or result.get("confronto_storico")
                if not diff or diff.get("stato") not in ("ok", "parziale"):
                    judgment = {"status": "skipped", "findings": [], "reason": "diff non confrontabile", "model": None, "usage": None}
                elif not diff.get("cambiamenti"):
                    judgment = {"status": "skipped", "findings": [], "reason": "nessun cambiamento testuale", "model": None, "usage": None}
                else:
                    judge_model = None
                    if self._default_judge:
                        from bellomberg.core.llm_client import modello
                        judge_model = modello("action_extractor")
                    evidence_key = _evidence_key(run, result, judge_model or "injected")
                    previous = self.store.find_evidence(evidence_key) if evidence_key else None
                    if previous and previous["judgment"]["status"] in ("ok", "parziale"):
                        judgment = {**previous["judgment"], "reused_from_run": previous["id"]}
                    else:
                        judgment = (self.judge(result, model=judge_model, language=run["judgment_language"])
                                    if self._default_judge else self.judge(result))
                        if not isinstance(judgment, dict) or judgment.get("status") not in ("ok", "parziale", "errore", "skipped"):
                            raise ValueError("judge: risultato non valido")
                    available = set(citation_catalog(result))
                    for finding in judgment.get("findings", []):
                        if not finding.get("citations") or any(c not in available for c in finding["citations"]):
                            raise ValueError("judge: citation_id assente dall'evidenza corrente")
            try:
                index = self.indexer({**run, "evidence_key": evidence_key}, result, judgment)
                if not isinstance(index, dict) or index.get("status") not in ("ok", "parziale", "skipped", "errore"):
                    raise ValueError("indexer: risultato non valido")
            except Exception as exc:
                index = {"status": "errore", "reason": f"{type(exc).__name__}: {exc}"}
            status = result["stato"]
            if judgment["status"] == "errore" or index["status"] in ("errore", "parziale"):
                status = "parziale" if status == "ok" else status
            reason = "; ".join(str(x) for x in result.get("motivi", [])[:5]) or None
            return self.store.finish_run(run_id, status=status, reason=reason, evidence_key=evidence_key,
                                         result=result, judgment=judgment, index=index)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            try:
                _canonical(result)
            except (TypeError, ValueError, OverflowError):
                result = None
            return self.store.finish_run(run_id, status="errore", reason=reason, evidence_key=evidence_key,
                                         result=result, judgment={"status": "errore", "findings": [], "reason": reason,
                                                                   "model": None, "usage": None}, index=index)

    def run(self, ticker, trigger="manual"):
        return self.execute(self.queue(ticker, trigger)["id"])

    def run_due(self, now=None):
        from bellomberg.storage.filing_store import RunAlreadyActive, RunNotDue
        outcomes = []
        for due in self.store.next_due(now):
            try:
                outcomes.append(self.run(due["ticker"], "scheduled"))
            except (RunAlreadyActive, RunNotDue) as exc:
                outcomes.append({"ticker": due["ticker"], "status": "skipped", "reason": str(exc)})
            except Exception as exc:
                outcomes.append({"ticker": due["ticker"], "status": "errore", "reason": f"{type(exc).__name__}: {exc}"})
        return outcomes

    def status(self, ticker):
        p = self.store.get_profile(ticker)
        if p:
            p = {**p, "next_due_at": self.store.next_due_at(ticker)}
        runs = self.store.list_runs(ticker)
        active = next((r for r in runs if r["status"] in ("queued", "running")), None)
        latest = runs[0] if runs else None
        return {"ticker": ticker, "profile": p, "runs": [self._summary(r) for r in runs],
                "active_run": self._summary(active) if active else None,
                "status": latest["status"] if latest else ("ready" if p else "non_disponibile"),
                "reason": latest["reason"] if latest else (None if p else "profilo assente"),
                "last_attempt": latest["started_at"] if latest else None}

    @staticmethod
    def _summary(run):
        if run is None:
            return None
        return {k: run[k] for k in ("id", "ticker", "status", "reason", "started_at", "finished_at", "trigger", "profile_version", "judgment_language")}

    def detail(self, run_id):
        return self.store.get_run(run_id)
