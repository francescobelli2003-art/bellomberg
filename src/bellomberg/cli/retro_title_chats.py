"""
retro_title_chats.py — Retro-titolatura delle 74 chat storiche di F3.

Richiesta n.2 del frontend (COORDINAMENTO_CHAT, 26/07 sera), DECISA dal PM, con
l'esecuzione assegnata alla chat backend perche' il DB e' territorio nostro.
I titoli li ha generati loro con Haiku 4.5 (`mockup_f3_chat/titles_haiku.json`,
costo MISURATO 0,0185 € — replicato con llm_pricing il 26/07 sera-6: combacia
alla cifra). Qui non si chiama nessun LLM: si legge quel file e si scrive il DB.

USO:
  python retro_title_chats.py            # DRY-RUN: stampa cosa cambierebbe
  python retro_title_chats.py --apply    # backup + UPDATE in UNA transazione

CINTURE (in ordine, tutte fail-closed):
  1. porta 8765 libera (backend acceso = DB lockato: lezione CLAUDE.md);
  2. backup in data/backups/ PRIMA di aprire la transazione;
  3. si toccano SOLO le sessioni il cui titolo e' ancora quello generico
     "Chat con <agente>" — quindi lo script e' idempotente e non sovrascrive
     MAI una rinomina fatta a mano dal PM;
  4. la regola sul titolo e' quella dell'endpoint (chat_engine.normalizza_titolo),
     non una copia locale che potrebbe divergere;
  5. `last_activity` NON viene toccata: ordinare l'archivio per attivita' e poi
     bussarla su 74 righe rimescolerebbe l'intera lista del PM.

LE CORREZIONI (verificate a mano 26/07 sera-6, non dedotte; oggi nel negozio privato)
Il frontend ne aveva segnalata UNA puntuale (#84) piu' un avviso generico. Una
prima rilettura ne ha trovate 6; la verifica avversariale pre-commit (1 agente
Opus 5 incaricato di smentirmi) ne ha trovate altre e ha bocciato una delle mie:

  · QUATTRO con un ticker che il PM non ha mai scritto e che NON ESISTE o e'
    sbagliato (#34 e #35 'AVXL' = Anavex mentre la domanda diceva Broadcom;
    #55 'BPMBK' inesistente; #61 'PSTH' = la SPAC sciolta nel 2022, mentre in
    book c'e' il fondo chiuso dello stesso gestore) + #84 mozzato;
  · #63, il peggiore: il titolo AFFERMA cio' che la sessione ha SMENTITO;
  · #26: un'intenzione ('impatti su portafogli') mai espressa;
  · #47: qui il difetto era MIO — avevo cristallizzato un refuso del PM sul simbolo
    di un ETF, scartando la correzione che lui stesso scrive due messaggi dopo (il
    simbolo corretto e' quello in book).

REGOLA DI CORREZIONE — decisa dal PM il 26/07 sera-6 (opzione B su una rosa di
tre): si correggono i ticker **sbagliati o inventati**, non le abbreviazioni
corrette. Quindi #44 e #70 (nome della societa' -> il suo ticker vero) e #41
(sp 500 -> SPX) restano come sono: sono espansioni giuste verso ticker veri,
due dei quali in book. La regola piu' stretta ("mai un ticker che il PM non
abbia scritto") era l'opzione A ed e' stata scartata: il danno e' il ticker
falso, non l'abbreviazione giusta.
Dove il PM ha nominato un'azienda senza ticker, si usa il nome dell'azienda.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

from bellomberg.core.paths import DATA_DIR, PROJECT_ROOT, SQLITE_PATH

DB = str(SQLITE_PATH)
JSON_TITOLI = str(PROJECT_ROOT / "archive" / "prototypes" / "mockup_f3_chat" / "titles_haiku.json")
BACKUP_DIR = str(DATA_DIR / "backups")
ATTESI = 74   # il perimetro misurato il 26/07 sera-6; se cambia, si ri-audita

# Etichette generiche STORICHE, misurate sul DB vero il 26/07 sera-6 (non dedotte).
# `create_session` compone "Chat con {AGENT_DISPLAY_NAMES[sp]}", ma due desk sono
# stati rinominati DOPO che quelle chat erano nate: oggi la mappa dice
# "News (legacy)" e "Politics (legacy)", mentre in DB le 16 sessioni vecchie
# (news 6 + politics 10 — sono le stesse che il frontend segnalava come
# irraggiungibili dalla UI) portano ancora "Chat con News" e "Chat con Politics".
# Senza questa tabella l'uguaglianza esatta le scarterebbe tutte e 16: la prima
# stesura di questa cintura ne saltava 15 su 74, ed e' cosi' che l'ho scoperto.
GENERICI_STORICI = {"news": ["News"], "politics": ["Politics"]}

# `normalizza_titolo` e' la regola UNICA condivisa con l'endpoint PATCH;
# `AGENT_DISPLAY_NAMES` serve a ricostruire il titolo generico ESATTO.
from bellomberg.agents.chat_engine import normalizza_titolo, AGENT_DISPLAY_NAMES  # noqa: E402
from bellomberg.storage.memory_db import connect_sqlite  # noqa: E402  (hardening #32: mai sqlite3.connect raw)

try:
    sys.stdout.reconfigure(encoding="utf-8")   # convenzione del repo
except Exception:
    pass

# id -> {titolo corretto, cosa c'era, perche' era sbagliato, deve_contenere}: le correzioni sono
# il giudizio del PM sui SUOI titoli e coi SUOI simboli, quindi vivono nel negozio privato
# (negozi_privati.carica_correzioni_titoli: data/retro_title_correzioni.json, forma in
# retro_title_correzioni.example.json). Negozio assente o illeggibile = lo script si FERMA:
# scrivere i titoli senza le correzioni auditate sarebbe il ripiego muto.
def correzioni():
    from bellomberg.storage.negozi_privati import carica_correzioni_titoli
    n = carica_correzioni_titoli()
    if n["origine"] in ("assente", "illeggibile"):
        raise RuntimeError("negozio delle correzioni %s: %s" % (n["origine"], n["motivo"]))
    return n["correzioni"]


def _porta_8765_occupata() -> bool:
    """CLAUDE.md: backend acceso -> DB lockato -> run perse. Meglio fermarsi.

    Socket, non netstat: la prima stesura leggeva l'stdout di `netstat -ano` e
    falliva APERTA (review pre-commit 26/07 sera-6, finding MEDIA) — se netstat
    usciva non-zero con stdout vuoto, `any(...)` dava False, cioe' "porta
    libera", e lo script scriveva col backend potenzialmente vivo. Il socket o
    si connette o no. Idioma gia' in casa: tools/migrations/backfill_decisions_45.py:28.

    ⚠️ LIMITE DICHIARATO: porta libera NON vuol dire "nessuno scrive sul DB".
    `Bellomberg-PriceUpdater` gira ogni 15' invocando python direttamente e non
    tocca mai la 8765; NewsFeed/Briefing hanno un fallback che scrive proprio
    QUANDO la porta e' libera. Lanciare lontano dai minuti :00/:15/:30/:45 e
    dalle 23:00 (AutoBackup).
    """
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8765))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _backup(db_path: str) -> str:
    """Snapshot COERENTE via l'API di backup di SQLite, non una copia nuda.

    Il DB gira in WAL (memory_db.py imposta journal_mode=wal): `shutil.copy2`
    prende solo il `.db` e lascia indietro il `-wal`, cioe' le transazioni
    committate ma non ancora checkpointate. La review pre-commit 26/07 sera-6
    l'ha DIMOSTRATO su un DB di scratch: 5 righe committate, backup con copy2
    ne conteneva 1. Quattro righe perse dentro un backup "riuscito".
    E' una lezione gia' scritta quattro volte nel repo (tools/ops/db_backup_direct.py:4-6,
    bellomberg_api.py:504, run_db_backup.bat, CONTEXT): tutti gli altri script
    che scrivono sul DB usano `Connection.backup()`. Questo era l'unico fuori riga.

    `quick_check` che ABORTA: un backup troncato non deve restare su disco con
    un nome che lo fa sembrare buono.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"consigliere_backup_{stamp}_pre_retro_titoli.db")
    src = sqlite3.connect(db_path, timeout=15)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
        qc = out.execute("PRAGMA quick_check").fetchone()
        sano = bool(qc) and str(qc[0]).lower() == "ok"
    finally:
        out.close()
        src.close()
    if not sano:
        try:
            os.remove(dst)
        except OSError:
            pass
        raise RuntimeError(
            f"backup NON sano (quick_check: {qc[0] if qc else 'senza esito'}): "
            "non scrivo niente sul DB")
    return dst


def carica_piano():
    """Ritorna (piano, scarti). Piano = [(id, titolo_finale, origine, domanda)].

    `domanda` (il campo `q` del JSON) viaggia col piano perche' serve al
    ri-controllo d'identita' in `main()`: il JSON e' una foto del 26/07 11:26 e
    non ci si scrive addosso a una sessione senza aver prima verificato che
    contenga ancora la domanda da cui quel titolo e' nato.
    """
    corr = correzioni()
    with open(JSON_TITOLI, encoding="utf-8") as f:
        dati = json.load(f)
    piano, scarti = [], []
    visti = set()
    for s in dati["sessions"]:
        sid = int(s["id"])
        if sid in visti:
            scarti.append((sid, "id duplicato nel JSON"))
            continue
        visti.add(sid)
        titolo = (s.get("haiku") or "").strip()
        if sid in corr:
            titolo, atteso = corr[sid]["titolo"], corr[sid]["cosa_c_era"]
            # se il JSON non contiene piu' il titolo che ho auditato, la
            # correzione e' cieca: fermarsi e' meglio che sovrascrivere
            if (s.get("haiku") or "").strip() != atteso.strip():
                scarti.append((sid, "CORREZIONE NON APPLICABILE: il JSON e' cambiato "
                                    f"da quando l'ho verificata (ora: {s.get('haiku')!r})"))
                continue
            origine = "CORRETTO A MANO"
        elif not titolo:
            scarti.append((sid, "nessun titolo Haiku (sessione senza domanda)"))
            continue
        else:
            origine = "haiku"
        try:
            piano.append((sid, normalizza_titolo(titolo), origine, s.get("q")))
        except ValueError as e:
            scarti.append((sid, f"titolo rifiutato dalla regola dell'endpoint: {e}"))
    if len(piano) != ATTESI:
        scarti.append((0, f"⚠️ il piano ha {len(piano)} titoli, ne erano attesi "
                          f"{ATTESI}: il JSON e' cambiato, ri-audita prima di applicare"))
    return piano, scarti


def main(apply: bool = False, db_path: Optional[str] = None) -> int:
    """`db_path` esiste per i test; il default resta il DB vero, quindi da riga
    di comando non cambia niente.

    ⚠️ Chi scrive test qui DEVE passare `db_path`: la connessione passa da
    `memory_db.connect_sqlite`, che il tripwire autouse di conftest sorveglia,
    quindi un test che dimenticasse `db_path` fallirebbe invece di scrivere sul
    DB del PM. (Nella prima stesura questa docstring dava la protezione per
    scontata mentre il codice usava `sqlite3.connect` RAW, che la guardia
    dichiara di non coprire — conftest.py:138-142. Trovato dalla review
    pre-commit 26/07 sera-6: era la stessa classe dell'incidente che ha reso
    necessaria la guardia.)
    """
    db_path = db_path or DB
    if not os.path.exists(db_path):
        print("DB non trovato:", db_path)
        return 1
    if not os.path.exists(JSON_TITOLI):
        print("File titoli non trovato:", JSON_TITOLI)
        return 1

    piano, scarti = carica_piano()
    print(f"JSON: {len(piano)} titoli candidati, {len(scarti)} scartati\n")

    if apply:
        if _porta_8765_occupata():
            print("[STOP] porta 8765 occupata: c'e' un backend vivo sul DB. "
                  "Spegnilo (o l'app Electron) e rilancia.")
            return 2
        dst = _backup(db_path)
        print(f"[BACKUP] {dst}\n")

    conn = connect_sqlite(db_path)
    conn.row_factory = sqlite3.Row
    try:
        da_scrivere, saltate = [], []
        for sid, titolo, origine, domanda in piano:
            r = conn.execute(
                "SELECT id, title, specialist FROM chat_sessions WHERE id=?", (sid,)
            ).fetchone()
            if r is None:
                saltate.append((sid, "sessione non esiste piu' in DB"))
                continue
            attuale = r["title"] or ""
            # CINTURA 3 — uguaglianza ESATTA col titolo generico ricostruito dallo
            # specialista, non `startswith("Chat con ")`. Col prefisso, un PM che
            # rinomina una chat in "Chat con il Capo sul VaR" se la vedrebbe
            # cancellata senza traccia (review pre-commit, finding MEDIA).
            sp = r["specialist"]
            generici = {f"Chat con {AGENT_DISPLAY_NAMES.get(sp, sp)}"}
            generici |= {f"Chat con {v}" for v in GENERICI_STORICI.get(sp, [])}
            if attuale not in generici:
                saltate.append((sid, f"titolo non e' quello generico ({attuale!r}): non tocco"))
                continue
            # CINTURA 5 — ri-controllo d'IDENTITA'. Il JSON e' una foto del
            # 26/07 11:26: si verifica che la sessione contenga ancora la
            # domanda da cui quel titolo e' nato, prima di scriverglielo
            # addosso. E' la cura del difetto documentato in
            # fix_decisions_eur.py:43 (scrivere sull'oggetto sbagliato).
            q_db = conn.execute(
                "SELECT content FROM chat_messages WHERE session_id=? AND role='user' "
                "ORDER BY id ASC LIMIT 1", (sid,)).fetchone()
            if q_db is None or (q_db["content"] or "").strip() != (domanda or "").strip():
                saltate.append((sid, "la prima domanda in DB non combacia col JSON: "
                                     "titolo NON applicato (identita' non provata)"))
                continue
            da_scrivere.append((sid, r["specialist"], attuale, titolo, origine))

        corr = correzioni()
        corrette = [x for x in da_scrivere if x[4] == "CORRETTO A MANO"]
        print(f"== DA SCRIVERE: {len(da_scrivere)} ==")
        print(f"   di cui corretti a mano: {len(corrette)}\n")

        if corrette:
            print(f"-- LE CORREZIONI A MANO ({len(corr)} auditate) --")
            for sid, sp, attuale, titolo, _o in sorted(corrette):
                vecchio, perche = corr[sid]["cosa_c_era"], corr[sid]["perche"]
                print(f"  #{sid} [{sp}]")
                print(f"     Haiku aveva : {vecchio!r}")
                print(f"     scrivo      : {titolo!r}")
                print(f"     motivo      : {perche}")
            print()

        print("-- I TITOLI HAIKU (invariati) --")
        for sid, sp, attuale, titolo, origine in sorted(da_scrivere):
            if origine == "haiku":
                print(f"  #{sid:<3} [{sp:<12}] {attuale!r} -> {titolo!r}")

        if saltate:
            print(f"\n-- SALTATE ({len(saltate)}) --")
            for sid, perche in sorted(saltate):
                print(f"  #{sid}: {perche}")
        if scarti:
            print(f"\n-- SCARTATE DAL JSON ({len(scarti)}) --")
            for sid, perche in sorted(scarti):
                print(f"  #{sid}: {perche}")

        if not apply:
            print(f"\n[DRY-RUN] nulla scritto. Rilancia con --apply per applicare "
                  f"le {len(da_scrivere)} righe.")
            return 0

        # UNA transazione: o tutte o nessuna. `last_activity` NON si tocca.
        # `AND title=?` nella WHERE rende la cintura 3 ATOMICA invece che
        # documentale: fra la SELECT qui sopra e questa UPDATE un altro processo
        # (l'app Electron si auto-spawna se la porta e' libera, CLAUDE.md) puo'
        # rinominare una chat — con la condizione nella WHERE quella riga
        # semplicemente non viene toccata (review pre-commit, TOCTOU).
        conn.execute("BEGIN IMMEDIATE")
        scritte = 0
        for sid, _sp, attuale, titolo, _o in da_scrivere:
            scritte += conn.execute(
                "UPDATE chat_sessions SET title=? WHERE id=? AND title=?",
                (titolo, sid, attuale)).rowcount
        conn.execute("COMMIT")
        print(f"\n[APPLICATO] {scritte} titoli scritti in una transazione "
              f"(pianificati {len(da_scrivere)}).")
        if scritte != len(da_scrivere):
            print(f"[!] {len(da_scrivere) - scritte} righe cambiate sotto il naso "
                  "fra lettura e scrittura: NON sovrascritte (e' voluto).")

        residue = conn.execute(
            "SELECT COUNT(*) FROM chat_sessions s WHERE s.title LIKE 'Chat con %' "
            "AND EXISTS(SELECT 1 FROM chat_messages m WHERE m.session_id=s.id "
            "AND m.role='user')").fetchone()[0]
        print(f"[VERIFICA] sessioni con domanda ancora senza titolo: {residue} (atteso 0)")
        # exit code onesto: la verifica finale deve poter far fallire la run,
        # altrimenti non e' una verifica (review pre-commit, finding BASSA)
        return 0 if (residue == 0 and scritte == len(da_scrivere)) else 3
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="applica al DB (default: dry-run)")
    sys.exit(main(apply=ap.parse_args().apply))
