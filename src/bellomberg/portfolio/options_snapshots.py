"""Archivio su file delle istantanee di superficie di volatilita' (10/10/2026, Opus 5.5).

Prima i download delle chain vivevano solo nella memoria del backend (~1 h) e il confronto
«ΔIV rispetto al download precedente» dipendeva dal localStorage del browser. Qui ogni
download COMPLETATO lascia su disco un'istantanea, leggibile da qualunque client e dopo un
riavvio. Regole:

- dove: ``<data dir>/options_snapshots/<TICKER>/<YYYYMMDDTHHMMSSZ>_<hash>.json.gz`` (+ indice
  ``.meta.json`` accanto). La data dir si risolve A OGNI CHIAMATA con la stessa regola di
  ``core.paths`` (``BELLOMBERG_DATA_DIR``, relativa alla radice del progetto, mai la cwd).
  Mai nel DB del portafoglio.
- cosa: il payload della superficie (slices con ``iv_grid``, ``moneyness_grid``, ``spot_est``,
  flag di qualita', ``snapshot_at``, ``t_years``) + le chain minime per ricalcolare il forward
  + provenienza (fonte, ritardo DELAYED, spot).
- scrittura atomica: temporaneo con nome unico (pid + thread) ACCANTO al bersaglio e
  ``os.replace``, con nuovi tentativi su ``PermissionError`` (Windows: un lettore che tiene
  aperto il bersaglio). Un lock di processo serializza deduplica + scrittura + pulizia.
- deduplica: l'hash del CONTENUTO (ticker, spot, chain minime; non l'orario ne' l'id del
  download) e' nel nome: lo stesso contenuto riscaricato non crea una seconda istantanea.
- retention: ``BELLOMBERG_OPTIONS_SNAPSHOT_RETENTION_DAYS`` (default 90) e
  ``BELLOMBERG_OPTIONS_SNAPSHOT_MAX_PER_TICKER`` (default 120). Si applica a OGNI salvataggio
  riuscito e su TUTTI i ticker dell'archivio (anche quelli non piu' scaricati), non alla lettura:
  senza nuovi salvataggi nulla viene rimosso, e l'elenco lo dichiara in ``retention.applied``.
  La pulizia e' DICHIARATA (id rimossi nell'esito; per un fallimento, quale file e' rimasto);
  una configurazione illeggibile NON cancella nulla e lo dice. Ordine: prima l'indice
  ``.meta.json``, poi il ``.json.gz`` (un gz senza indice resta leggibile; un indice senza gz no).
- i testi bilingui del payload si conservano in coppia (it/en) e si ricostruiscono alla
  lettura: la pagina li vede nella lingua di chi legge, non in quella del download.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from bellomberg.core.presentation import _PresentationText, message, render_payload

log = logging.getLogger(__name__)

FORMAT = "bellomberg.options_snapshot.v1"
DIRNAME = "options_snapshots"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_PER_TICKER = 120
RETENTION_ENV = "BELLOMBERG_OPTIONS_SNAPSHOT_RETENTION_DAYS"
MAX_PER_TICKER_ENV = "BELLOMBERG_OPTIONS_SNAPSHOT_MAX_PER_TICKER"
SOURCE = "Polygon option-chain snapshot"
# campi di contratto che servono a ricostruire smile e forward (put-call parity)
CHAIN_FIELDS = ("contract", "expiry", "type", "strike", "bid", "ask", "iv", "oi", "volume",
                "adjusted", "multiplier", "exercise_style", "quote_timestamp", "quote_timeframe")
_REPLACE_PAUSES = (0.05, 0.15, 0.4, None)
_TMP_MAX_AGE_SECONDS = 86400
_STAMP_RE = re.compile(r"\d{8}T\d{6}Z")
_HASH_RE = re.compile(r"[0-9a-f]{16}")
_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-^]{0,24}")
_ID_RE = re.compile(r"([A-Z0-9][A-Z0-9.\-^]{0,24})~(\d{8}T\d{6}Z)_([0-9a-f]{16})")
_FILE_RE = re.compile(r"(\d{8}T\d{6}Z)_([0-9a-f]{16})\.json\.gz")
# nomi riservati di Windows: una cartella «CON» o «NUL» non si crea
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_LOCK = threading.RLock()


class SnapshotUnreadable(Exception):
    """Il file esiste ma non si legge (gzip/JSON rotto o formato sconosciuto)."""


class SnapshotBusy(Exception):
    """Il file e' in uso o in rimozione (Windows: PermissionError): esito TRANSITORIO, riprovare."""


# ---------------------------------------------------------------- percorsi
def data_dir() -> Path:
    """Data dir del progetto, risolta ADESSO con la regola di core.paths (mai la cwd)."""
    from bellomberg.core import paths
    return paths._runtime_path("BELLOMBERG_DATA_DIR", "data")


def archive_root(root: Optional[os.PathLike] = None) -> Path:
    return Path(root) if root is not None else data_dir() / DIRNAME


def _ticker(value) -> str:
    ticker = str(value).strip().upper()
    if not _TICKER_RE.fullmatch(ticker) or ticker.endswith("."):
        # il punto finale sparisce dai nomi di cartella su Windows: «ZZ.» finirebbe in «ZZ»
        raise ValueError(message('ticker non valido', 'Invalid ticker'))
    return ticker


def _ticker_dirname(ticker: str) -> str:
    return "_" + ticker if ticker.split(".")[0] in _RESERVED else ticker


def stamp_of(snapshot_at: str) -> str:
    """ISO con fuso -> ``YYYYMMDDTHHMMSSZ`` (UTC). Senza fuso o illeggibile: errore, mai un'ora inventata."""
    if not isinstance(snapshot_at, str):
        raise ValueError(message('snapshot_at assente', 'Missing snapshot_at'))
    parsed = datetime.fromisoformat(snapshot_at)
    if parsed.tzinfo is None:
        raise ValueError(message('snapshot_at senza fuso orario', 'snapshot_at without a time zone'))
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_id(snapshot_id: str):
    match = _ID_RE.fullmatch(snapshot_id or "") if isinstance(snapshot_id, str) else None
    if not match:
        raise ValueError(message('id istantanea non valido', 'Invalid snapshot id'))
    _ticker(match.group(1))
    return match.groups()


def _paths(root: Path, ticker: str, stem: str):
    folder = root / _ticker_dirname(ticker)
    return folder, folder / f"{stem}.json.gz", folder / f"{stem}.meta.json"


# ---------------------------------------------------------------- testi bilingui
def _freeze(payload):
    """Copia renderizzata + coppie it/en dei messaggi d'autore, per percorso."""
    texts = []

    def walk(value, path):
        if isinstance(value, _PresentationText):
            texts.append({"path": path, "it": str(render_payload(value, language="it")),
                          "en": str(render_payload(value, language="en"))})
            return str(value)
        if isinstance(value, dict):
            return {str(k): walk(v, [*path, str(k)]) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, [*path, i]) for i, v in enumerate(value)]
        return value
    return walk(payload, []), texts


def _thaw(payload, texts):
    for entry in texts or []:
        node, path = payload, entry.get("path") or []
        try:
            for key in path[:-1]:
                node = node[key]
            if path:
                node[path[-1]] = message(str(entry["it"]), str(entry["en"]))
        except (KeyError, IndexError, TypeError):
            continue  # percorso non piu' presente: resta il testo renderizzato al salvataggio
    return payload


# ---------------------------------------------------------------- record
def minimal_chains(rows: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out = {}
    for expiry in sorted(rows):
        contracts = [{key: c.get(key) for key in CHAIN_FIELDS} for c in rows[expiry].get("contracts") or []]
        contracts.sort(key=lambda c: (c.get("strike") or 0, str(c.get("type")), str(c.get("contract"))))
        out[expiry] = contracts
    return out


def content_hash(ticker: str, spot: Dict[str, Any], chains: Dict[str, List[Dict[str, Any]]]) -> str:
    canonical = json.dumps({"ticker": ticker, "spot": spot, "chains": chains}, sort_keys=True,
                           separators=(",", ":"), default=str, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_record(view: Dict[str, Any], surface: Dict[str, Any]) -> Dict[str, Any]:
    """`view`: istantanea del job presa sotto il lock del manager (vedi options_download)."""
    ticker = _ticker(view["ticker"])
    stamp = stamp_of(view["snapshot_at"])
    chains = minimal_chains(view["rows"])
    spot = {key: view.get(key) for key in ("spot", "spot_source", "spot_timestamp", "spot_timeframe",
                                           "spot_qualified", "spot_fallback")}
    # l'orario dello spot NON entra nell'hash: senza orario del provider e' l'ora di ricezione
    # della pagina, diversa a ogni download anche a contenuto identico (le quote hanno il loro)
    digest = content_hash(ticker, {k: v for k, v in spot.items() if k != "spot_timestamp"}, chains)
    timeframes: Dict[str, int] = {}
    for contracts in chains.values():
        for c in contracts:
            key = c.get("quote_timeframe") or "undeclared"
            timeframes[key] = timeframes.get(key, 0) + 1
    frozen, texts = _freeze({key: value for key, value in surface.items() if key != "archive"})
    slices = frozen.get("slices") or []
    return {
        "format": FORMAT, "id": f"{ticker}~{stamp}_{digest}", "ticker": ticker,
        "snapshot_at": view["snapshot_at"], "saved_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": digest,
        "provenance": {
            "source": SOURCE, "download_id": view.get("download_id"), "scope": view.get("scope"),
            "download_complete": bool(view.get("download_complete")),
            "data_delay": frozen.get("data_delay"), "quote_timeframes": timeframes,
            "output_language": view.get("output_language"), **{k: v for k, v in spot.items()},
            "spot_alignment": view.get("spot_alignment"),
            "note": "Polygon/Massive: IV e greche del provider; quote come dichiarate dal piano (DELAYED ~15 min se cosi' marcate)",
        },
        "summary": {"spot": frozen.get("spot_est"), "expiries": sorted(chains),
                    "surface_expiries": [s.get("expiry") for s in slices],
                    "n_contracts": sum(len(v) for v in chains.values()), "n_slices": len(slices),
                    "surface_error": frozen.get("error"), "data_delay": frozen.get("data_delay"),
                    "spot_qualified": frozen.get("spot_qualified"), "iv_grid_qualified": frozen.get("iv_grid_qualified")},
        "chain_status": {e: {"complete": bool(view["rows"][e].get("complete")), "error": view["rows"][e].get("error")}
                         for e in sorted(view["rows"])},
        "surface": frozen, "presentation_texts": texts, "chains": chains,
    }


# ---------------------------------------------------------------- scrittura
def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        last = None
        for pause in _REPLACE_PAUSES:
            try:
                os.replace(tmp, target)
                return
            except PermissionError as exc:   # Windows: bersaglio aperto da un lettore
                last = exc
                if pause is not None:
                    time.sleep(pause)
        raise last
    finally:
        try:
            if tmp.exists():
                os.remove(tmp)
        except OSError:
            pass


def retention_config():
    """(giorni, massimo per ticker, errore). Un valore illeggibile = nessuna pulizia, dichiarata."""
    out = {}
    for name, env, default in (("days", RETENTION_ENV, DEFAULT_RETENTION_DAYS),
                               ("max_per_ticker", MAX_PER_TICKER_ENV, DEFAULT_MAX_PER_TICKER)):
        raw = os.environ.get(env, "").strip()
        if not raw:
            out[name] = default
            continue
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value < 1:
            return None, None, message(f'{env}={raw!r} non valido: pulizia NON eseguita',
                                       f'{env}={raw!r} invalid: cleanup NOT performed')
        out[name] = value
    return out["days"], out["max_per_ticker"], None


def _entries(folder: Path):
    found = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = _FILE_RE.fullmatch(path.name)
            if match:
                found.append((match.group(1), match.group(2), path))
    return sorted(found)


def prune(ticker: str, root: Optional[os.PathLike] = None, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pulizia DICHIARATA: oltre i giorni di retention o oltre N per ticker (si tengono i piu' recenti)."""
    ticker = _ticker(ticker)
    root = archive_root(root)
    days, keep, error = retention_config()
    result = {"removed": [], "retention_days": days, "max_per_ticker": keep, "error": error, "failed": []}
    if error:
        log.warning("options_snapshots: %s", error)
        return result
    folder = root / _ticker_dirname(ticker)
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")
    entries = _entries(folder)
    doomed = [e for e in entries if e[0] < cutoff]
    survivors = [e for e in entries if e[0] >= cutoff]
    if len(survivors) > keep:
        doomed += survivors[:len(survivors) - keep]
    for stamp, digest, path in doomed:
        stem = f"{stamp}_{digest}"
        meta = folder / f"{stem}.meta.json"
        # PRIMA l'indice, POI il gz: se il secondo passo fallisce resta un gz senza indice, che
        # l'elenco rilegge e dichiara; mai un indice che punta a un gz gia' rimosso.
        step = "meta"
        try:
            if meta.exists():
                os.remove(meta)
            step = "gz"
            os.remove(path)
            result["removed"].append(f"{ticker}~{stem}")
        except OSError as exc:
            remaining = [str(p.name) for p in (meta, path) if p.exists()]
            result["failed"].append({"id": f"{ticker}~{stem}", "step": step, "error": type(exc).__name__,
                                     "remaining": remaining})
    if folder.is_dir():   # indici orfani (gz rimosso da un fallimento precedente o a mano)
        for meta in folder.glob("*.meta.json"):
            if not (folder / (meta.name[:-len(".meta.json")] + ".json.gz")).exists():
                try:
                    os.remove(meta)
                    result["removed"].append(meta.name)
                except OSError as exc:
                    result["failed"].append({"id": None, "step": "orphan_meta", "error": type(exc).__name__,
                                             "remaining": [meta.name]})
    if folder.is_dir():   # temporanei orfani (kill fra write e replace)
        for path in folder.glob("*.tmp"):
            try:
                if time.time() - path.stat().st_mtime > _TMP_MAX_AGE_SECONDS:
                    os.remove(path)
                    result["removed"].append(path.name)
            except OSError:
                pass
    if result["removed"] or result["failed"]:
        log.info("options_snapshots: pulizia %s rimossi=%s falliti=%s", ticker, result["removed"], result["failed"])
    return result


def _folders(root: Path):
    """(ticker, cartella) di tutto l'archivio; nomi che non sono ticker validi: saltati."""
    out = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            name = folder.name[1:] if folder.name.startswith("_") else folder.name
            try:
                out.append((_ticker(name), folder))
            except ValueError:
                continue
    return out


def prune_all(root: Optional[os.PathLike] = None, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """La retention su TUTTI i ticker dell'archivio: un ticker non piu' scaricato invecchia lo stesso."""
    root = archive_root(root)
    days, keep, error = retention_config()
    total = {"removed": [], "failed": [], "retention_days": days, "max_per_ticker": keep, "error": error,
             "scope": "all_tickers"}
    if error:
        log.warning("options_snapshots: %s", error)
        return total
    for ticker, _ in _folders(root):
        one = prune(ticker, root, now=now)
        total["removed"] += one["removed"]
        total["failed"] += one["failed"]
    return total


def _meta(record: Dict[str, Any], size: int) -> Dict[str, Any]:
    summary = record["summary"]
    prov = record["provenance"]
    return {"id": record["id"], "ticker": record["ticker"], "snapshot_at": record["snapshot_at"],
            "saved_at": record["saved_at"], "spot": summary["spot"], "spot_source": prov.get("spot_source"),
            "spot_qualified": summary.get("spot_qualified"), "iv_grid_qualified": summary.get("iv_grid_qualified"),
            "expiries": summary["expiries"], "surface_expiries": summary["surface_expiries"],
            "n_expiries": len(summary["expiries"]), "n_contracts": summary["n_contracts"],
            "n_slices": summary["n_slices"], "surface_error": summary.get("surface_error"),
            "data_delay": summary.get("data_delay"), "download_complete": prov.get("download_complete"),
            "source": prov.get("source"), "size_bytes": size}


def save(record: Dict[str, Any], root: Optional[os.PathLike] = None) -> Dict[str, Any]:
    """Scrive l'istantanea (o riconosce un duplicato). Solleva in caso di errore di scrittura:
    il chiamante lo trasforma in esito dichiarato senza rompere il download."""
    root = archive_root(root)
    ticker = record["ticker"]
    _, stamp, digest = parse_id(record["id"])
    folder, gz_path, meta_path = _paths(root, ticker, f"{stamp}_{digest}")
    with _LOCK:
        for other_stamp, other_digest, path in _entries(folder):
            if other_digest == digest:
                return {"status": "duplicate", "id": f"{ticker}~{other_stamp}_{other_digest}",
                        "error": None, "pruned": None,
                        "note": message('stesso contenuto già archiviato: nessuna seconda istantanea',
                                        'Same content already archived: no second snapshot')}
        blob = gzip.compress(json.dumps(record, ensure_ascii=False, allow_nan=False, default=str)
                             .encode("utf-8"), compresslevel=6, mtime=0)
        _atomic_write(gz_path, blob)
        meta_error = None
        try:
            _atomic_write(meta_path, json.dumps(_meta(record, len(blob)), ensure_ascii=False).encode("utf-8"))
        except OSError as exc:   # l'istantanea c'e': l'elenco la rilegge dal .gz e lo dichiara
            meta_error = type(exc).__name__
            log.warning("options_snapshots: indice %s non scritto (%s)", meta_path, meta_error)
        try:   # la scrittura e' riuscita: un guasto della pulizia non la rinnega, si dichiara
            pruned = prune_all(root)
        except Exception as exc:
            log.warning("options_snapshots: pulizia NON eseguita dopo il salvataggio di %s: %s",
                        record["id"], type(exc).__name__)
            pruned = {"error": message(f'pulizia non eseguita ({type(exc).__name__})',
                                       f'Cleanup not performed ({type(exc).__name__})'),
                      "removed": [], "failed": []}
    return {"status": "saved", "id": record["id"], "error": None, "size_bytes": len(blob),
            "index_error": meta_error, "pruned": pruned}


# ---------------------------------------------------------------- lettura
def _read_gz(path: Path) -> Dict[str, Any]:
    try:
        with gzip.open(path, "rb") as handle:
            record = json.loads(handle.read().decode("utf-8"))
    except FileNotFoundError:
        raise
    except PermissionError as exc:   # Windows: in uso o in rimozione, non «rotto»
        raise SnapshotBusy(type(exc).__name__) from exc
    except (OSError, EOFError, ValueError) as exc:
        raise SnapshotUnreadable(type(exc).__name__) from exc
    if not isinstance(record, dict) or record.get("format") != FORMAT:
        raise SnapshotUnreadable("format")
    return record


def list_snapshots(ticker: Optional[str] = None, root: Optional[os.PathLike] = None) -> Dict[str, Any]:
    """Elenco (piu' recente prima). Un file illeggibile resta in `errors`, mai taciuto."""
    root = archive_root(root)
    wanted = _ticker(ticker) if ticker is not None else None
    items, errors = [], []
    folders = [(name, folder) for name, folder in _folders(root) if wanted is None or name == wanted]
    for name, folder in folders:
        for stamp, digest, path in _entries(folder):
            sid = f"{name}~{stamp}_{digest}"
            meta_path = folder / f"{stamp}_{digest}.meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("id") != sid:
                    raise ValueError("id")
                meta["index"] = "meta"
            except FileNotFoundError:
                meta = None
            except (OSError, ValueError):
                meta = None
            if meta is None:
                try:
                    record = _read_gz(path)
                    meta = {**_meta(record, path.stat().st_size), "index": "rebuilt_from_archive"}
                except FileNotFoundError:
                    continue   # rimosso dalla pulizia mentre si elencava
                except SnapshotBusy as exc:
                    errors.append({"id": sid, "error": f"in uso o in rimozione ({exc}): riprovare", "transient": True})
                    continue
                except SnapshotUnreadable as exc:
                    errors.append({"id": sid, "error": f"illeggibile ({exc})"})
                    continue
            if wanted is not None and meta.get("ticker") != wanted:
                errors.append({"id": sid, "error": "ticker dell'indice diverso dalla cartella"})
                continue
            items.append(meta)
    items.sort(key=lambda m: (str(m.get("snapshot_at")), m["id"]), reverse=True)
    days, keep, cfg_error = retention_config()
    return {"ticker": wanted, "snapshots": items, "count": len(items), "errors": errors,
            "retention": {"days": days, "max_per_ticker": keep, "error": cfg_error,
                          "applied": "a ogni salvataggio riuscito, su tutti i ticker; mai alla lettura"},
            "storage": "file gzip per istantanea sotto la data dir (options_snapshots/)",
            "_source": "archivio istantanee opzioni (file)", "_timestamp": datetime.now(timezone.utc).isoformat()}


def load(snapshot_id: str, root: Optional[os.PathLike] = None) -> Dict[str, Any]:
    ticker, stamp, digest = parse_id(snapshot_id)
    _, gz_path, _ = _paths(archive_root(root), ticker, f"{stamp}_{digest}")
    try:
        record = _read_gz(gz_path)
        size = gz_path.stat().st_size
    except FileNotFoundError:
        raise KeyError(message('istantanea assente (mai salvata o rimossa dalla retention)',
                               'Snapshot missing (never saved or removed by retention)')) from None
    except PermissionError as exc:
        raise SnapshotBusy(type(exc).__name__) from exc
    if record.get("id") != snapshot_id:
        raise SnapshotUnreadable("id")
    record["surface"] = _thaw(record.get("surface") or {}, record.pop("presentation_texts", []))
    record["size_bytes"] = size
    record["_source"] = "archivio istantanee opzioni (file)"
    record["_timestamp"] = record.get("saved_at")
    return record
