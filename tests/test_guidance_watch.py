"""Voce 8, PRIMO PEZZO (§9-sextrigies punto 2, ok PM 03/08, Fable 5): il
grilletto della trimestrale automatica legge il REGISTRO (`company_guidance.
valid_until`), MAI l'earnings calendar finnhub (verificato inaffidabile il
03/08: cap 1500 zitto + copertura 1/27 — changelog (63)).

Contratto di guidance_watch.scadenze_guidance:
- SOLO righe status='active'; valid_until <= oggi -> SCADUTA (il giorno della
  scadenza E' il giorno del grilletto: valid_until = data attesa della
  trimestrale, review V6 A1); dentro (oggi, oggi+entro_giorni] -> IN_SCADENZA.
- `today` sempre esplicitabile (test deterministici), DB apribile via path.
- SOLA LETTURA per costruzione (mode=ro): il modulo non puo' ne' scrivere ne'
  applicare migrazioni — il ⚠️ di §9-sextrigies sui task che istanziano
  MemoryDB qui non puo' accadere.
"""
import json
import sqlite3

import pytest

from bellomberg.market_data.guidance_watch import radar_completo, scadenze_guidance

SCHEMA = """
CREATE TABLE company_guidance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, metric TEXT NOT NULL, period TEXT NOT NULL,
    value_low REAL, value_mid REAL NOT NULL, value_high REAL,
    unit TEXT NOT NULL, source_doc TEXT NOT NULL, source_date TEXT NOT NULL,
    effective_date TEXT NOT NULL, valid_until TEXT NOT NULL,
    valid_until_source TEXT, status TEXT NOT NULL DEFAULT 'active',
    superseded_by INTEGER, entered_by TEXT, note TEXT, created_at TEXT
)
"""


def _riga(ticker, valid_until, status="active", metric="revenue_growth"):
    return (ticker, metric, "FY2026", None, 1.0, None, "pct",
            "doc collaudo", "2026-07-01", "2026-07-01", valid_until,
            "collaudo", status, None, "test", None, "2026-07-01T00:00:00")


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "collaudo.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    righe = [
        _riga("NORD", "2026-08-05"),                        # scade il giorno del report
        _riga("THETA.MI", "2026-10-27"),                     # attiva, lontana
        _riga("OMEGA.VI", "2026-11-05"),                      # attiva, lontana
        _riga("ALFA", "2026-07-30", status="superseded"),  # storico: MAI nel grilletto
        _riga("SIGMA", "2026-08-01"),                        # gia' scaduta (arretrato)
    ]
    con.executemany("INSERT INTO company_guidance VALUES (NULL," + ",".join("?" * 17) + ")", righe)
    con.commit()
    con.close()
    return path


def test_scaduta_il_giorno_stesso_fa_scattare_il_grilletto(db):
    """Il banco di prova NORD: valid_until 05/08, oggi 05/08 -> SCADUTA."""
    r = scadenze_guidance(today="2026-08-05", db_path=db)
    tk = [x["ticker"] for x in r["scadute"]]
    assert "NORD" in tk
    nvo = next(x for x in r["scadute"] if x["ticker"] == "NORD")
    assert nvo["giorni_di_ritardo"] == 0


def test_arretrato_conta_i_giorni_di_ritardo(db):
    r = scadenze_guidance(today="2026-08-05", db_path=db)
    bsx = next(x for x in r["scadute"] if x["ticker"] == "SIGMA")
    assert bsx["giorni_di_ritardo"] == 4


def test_in_scadenza_entro_finestra_dichiarata(db):
    """Il 03/08 con finestra 7g: NORD (05/08) e' IN SCADENZA, non scaduta."""
    r = scadenze_guidance(today="2026-08-03", entro_giorni=7, db_path=db)
    assert [x["ticker"] for x in r["scadute"]] == ["SIGMA"]
    assert [x["ticker"] for x in r["in_scadenza"]] == ["NORD"]


def test_superseded_mai_nel_grilletto(db):
    """Lo storico non spara: ALFA superseded resta fuori anche se 'scaduta'."""
    r = scadenze_guidance(today="2026-08-05", db_path=db)
    tutti = [x["ticker"] for x in r["scadute"] + r["in_scadenza"]]
    assert "ALFA" not in tutti


def test_attive_lontane_contate_non_elencate(db):
    r = scadenze_guidance(today="2026-08-03", entro_giorni=7, db_path=db)
    assert r["attive_non_in_finestra"] == 2  # THETA.MI + OMEGA.VI


def test_fonte_dichiarata_e_mai_finnhub(db):
    """La riga di dottrina viaggia nel payload: registro, MAI finnhub."""
    r = scadenze_guidance(today="2026-08-03", db_path=db)
    assert "registro" in r["fonte"].lower()
    assert "finnhub" not in r["fonte"].lower() or "mai" in r["fonte"].lower()


def test_sola_lettura_per_costruzione(db):
    """Il modulo apre mode=ro: su un file READ-ONLY la lettura funziona lo
    stesso — la prova che non c'e' nessun percorso di scrittura."""
    import os
    import stat
    os.chmod(db, stat.S_IREAD)
    try:
        r = scadenze_guidance(today="2026-08-05", db_path=db)
        assert len(r["scadute"]) == 2  # NORD + SIGMA: la lettura e' completa
    finally:
        os.chmod(db, stat.S_IWRITE | stat.S_IREAD)


# ============ radar_completo: registro + fonti per-ticker + book ============
# Voce 8 SECONDO PEZZO (ok PM 13/08): il registro resta il grilletto primario;
# le fonti per-ticker (fonti_guidance) coprono il resto del book; ogni ticker
# del book finisce in ESATTAMENTE una casella dichiarata — niente buchi zitti.

FONTI_COLLAUDO = {
    # NORD ha ANCHE una fonte: non deve sparare due volte (registro vince)
    "NORD": {"nome": "Nord Pharma AS", "tipo": "adr",
            "ir_url": "https://nord.example/investors",
            "next_report_date": "2026-08-01",
            "next_report_source": "calendario IR collaudo",
            "verificato_il": "2026-07-20"},
    "BETA.DE": {"nome": "Beta AG", "tipo": "equity",
                "ir_url": "https://beta.example/ir",
                "next_report_date": "2026-08-12",   # entro 7g dal 05/08
                "next_report_source": "calendario IR collaudo",
                "verificato_il": "2026-07-20"},
    "GAMMA": {"nome": "Gamma Corp", "tipo": "equity",
              "ir_url": "https://gamma.example/investors",
              "next_report_date": None,
              "next_report_source": None, "verificato_il": None},
    "ORO.MI": {"nome": "Oro ETC", "tipo": "etc"},
}

BOOK_COLLAUDO = ["NORD", "BETA.DE", "GAMMA", "ORO.MI", "IGNOTO.L"]


@pytest.fixture
def db_con_book(db):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE positions (ticker TEXT, nome TEXT, quantita REAL)")
    # `favorite_companies` c'e' e per default e' VUOTA: cosi' i test storici
    # misurano lo schema canonico (memory_db, riallineamento 23/07) e non il
    # ramo del buco dichiarato, che ha il suo test dedicato piu' sotto.
    con.execute("CREATE TABLE favorite_companies (ticker TEXT PRIMARY KEY, "
                "name TEXT, sector TEXT, industry TEXT, note TEXT, added_at TEXT)")
    con.executemany("INSERT INTO positions VALUES (?,?,?)",
                    [(t, t, 10.0) for t in BOOK_COLLAUDO]
                    + [("CHIUSA.MI", "posizione chiusa", 0.0)])
    con.commit()
    con.close()
    return db


def _aggiungi_preferiti(db, tickers):
    con = sqlite3.connect(db)
    con.executemany("INSERT INTO favorite_companies (ticker, name) VALUES (?,?)",
                    [(t, t) for t in tickers])
    con.commit()
    con.close()
    return db


def test_radar_completo_registro_resta_il_grilletto_primario(db_con_book):
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    assert "NORD" in [x["ticker"] for x in r["registro"]["scadute"]]


def test_radar_completo_ticker_a_registro_non_spara_due_volte(db_con_book):
    """NORD ha riga attiva a registro E fonte con data passata: deve uscire
    SOLO dal registro — il radar fonti non lo doppia."""
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    fr = r["fuori_registro"]
    tutti_fr = [x["ticker"] for x in
                fr["usciti"] + fr["imminenti"] + fr["non_verificati"]]
    assert "NORD" not in tutti_fr


def test_radar_completo_fonti_coprono_il_resto_del_book(db_con_book):
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    assert [x["ticker"] for x in r["fuori_registro"]["imminenti"]] == ["BETA.DE"]
    assert [x["ticker"] for x in r["fuori_registro"]["non_verificati"]] == ["GAMMA"]


def test_radar_completo_conservazione_del_book(db_con_book):
    """Ogni ticker del book (quantita>0) in ESATTAMENTE una casella; la
    posizione chiusa resta fuori."""
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    c = r["copertura"]
    tutte = c["a_registro"] + c["con_fonte"] + c["non_applicabili"] + c["scoperti"]
    assert sorted(tutte) == sorted(BOOK_COLLAUDO)
    assert len(tutte) == len(set(tutte))
    assert "CHIUSA.MI" not in tutte


def test_radar_completo_scoperti_dichiarati(db_con_book):
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    assert r["copertura"]["scoperti"] == ["IGNOTO.L"]


def test_radar_completo_sola_lettura_per_costruzione(db_con_book):
    import os
    import stat
    os.chmod(db_con_book, stat.S_IREAD)
    try:
        r = radar_completo(today="2026-08-05", entro_giorni=7,
                           db_path=db_con_book, fonti=FONTI_COLLAUDO)
        assert r["copertura"]["book"] == 5
    finally:
        os.chmod(db_con_book, stat.S_IWRITE | stat.S_IREAD)


def _riga_other(ticker, valid_until, unit, mid):
    """Riga metric='other': la grandezza la dice `unit` (gate trimestrali 20/08)."""
    return (ticker, "other", "FY2026", None, mid, None, unit,
            "doc collaudo", "2026-07-30", "2026-07-30", valid_until,
            "collaudo", "active", None, "test", None, "2026-07-30T00:00:00")


def test_voci_dello_stesso_ticker_sono_DISTINGUIBILI(tmp_path):
    """Review avversariale 20/08: col supersede per ticker+metric+period+unit un
    ticker puo' avere N target attivi dello stesso periodo (due societa' EU del book: 5 e 9).
    Senza `unit` nel payload il radar li elenca come N voci identiche distinte
    solo da un id opaco, e il PM non sa QUALE grandezza sta scadendo."""
    path = str(tmp_path / "radar.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany("INSERT INTO company_guidance VALUES (NULL," + ",".join("?" * 17) + ")", [
        _riga_other("SIGMA.MI", "2026-11-05", "meur (ordini FY)", 31500),
        _riga_other("SIGMA.MI", "2026-11-05", "meur (EBITA FY)", 2450),
        _riga_other("SIGMA.MI", "2026-11-05", "meur (FOCF FY)", 1180),
    ])
    con.commit()
    con.close()

    r = scadenze_guidance(today="2026-08-20", entro_giorni=120, db_path=path)
    voci = [v for v in r["in_scadenza"] if v["ticker"] == "SIGMA.MI"]
    assert len(voci) == 3
    # ogni voce dice quale grandezza e', e le tre sono diverse fra loro
    assert all("unit" in v for v in voci)
    assert {v["unit"] for v in voci} == {"meur (ordini FY)", "meur (EBITA FY)",
                                         "meur (FOCF FY)"}
    # e non sono distinguibili SOLO dall'id (che e' opaco per chi legge)
    senza_id = [{k: v for k, v in voce.items() if k != "id"} for voce in voci]
    assert len({tuple(sorted(d.items())) for d in senza_id}) == 3


def test_il_radar_copre_anche_i_preferiti(tmp_path):
    """I «preferiti» (favorite_companies) sono nell'universo quanto le posizioni:
    chi mette un titolo fra i preferiti vuole sapere quando riporta.

    Lo schema di `company_guidance` qui e' quello che il grilletto LEGGE
    (guidance_watch.py, SELECT di scadenze_guidance): la versione a tre colonne
    del piano faceva cadere il test su «no such column: id», cioe' per un
    motivo che col preferito non c'entra (lezione «RED per il motivo giusto»)."""
    import sqlite3
    import bellomberg.market_data.guidance_watch as gw
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE positions (ticker TEXT, quantita REAL);
        CREATE TABLE favorite_companies (ticker TEXT, name TEXT);
        CREATE TABLE company_guidance (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, metric TEXT,
            period TEXT, unit TEXT, status TEXT, valid_until TEXT,
            valid_until_source TEXT, source_date TEXT, note TEXT);
        INSERT INTO positions VALUES ('ACME.MI', 10);
        INSERT INTO favorite_companies VALUES ('ORO.MI', 'Oro ETC');
    """)
    con.commit()
    con.close()
    r = gw.radar_completo(db_path=str(db), fonti={})
    coperti = set(r["copertura"]["scoperti"])
    assert "ACME.MI" in coperti, "la posizione manca dall'universo"
    assert "ORO.MI" in coperti, "il preferito manca dall'universo"
    assert r["copertura"]["book"] == 1
    assert r["copertura"]["preferiti"] == 1


def test_il_preferito_che_non_e_una_posizione_entra_nell_universo(db_con_book):
    """Il caso che il PM ha nominato: un titolo che NON e' in portafoglio ma
    sta fra i preferiti. Prima del 04/09 era invisibile al radar."""
    _aggiungi_preferiti(db_con_book, ["DELTA.PA"])
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    c = r["copertura"]
    assert c["universo"] == len(BOOK_COLLAUDO) + 1
    assert c["preferiti"] == 1
    assert c["provenienza"]["DELTA.PA"] == "preferito"
    assert "DELTA.PA" in c["scoperti"], "il preferito senza fonte va DICHIARATO"


def test_il_ticker_che_e_posizione_E_preferito_non_si_doppia(db_con_book):
    """`positions` UNIONE `favorite_companies`: chi sta in tutt'e due compare
    UNA volta sola, e la provenienza lo dice invece di scegliere per lui."""
    _aggiungi_preferiti(db_con_book, ["GAMMA", "DELTA.PA"])
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    c = r["copertura"]
    assert c["book"] == len(BOOK_COLLAUDO)
    assert c["preferiti"] == 2
    assert c["universo"] == len(BOOK_COLLAUDO) + 1, "GAMMA non deve contarsi due volte"
    assert c["provenienza"]["GAMMA"] == "posizione e preferito"
    # simbolo INVENTATO di proposito: riga nostra, e tests/ viaggia nel repo pubblico
    assert c["provenienza"]["BETA.DE"] == "posizione"
    assert c["provenienza"]["DELTA.PA"] == "preferito"
    tutte = c["a_registro"] + c["con_fonte"] + c["non_applicabili"] + c["scoperti"]
    assert sorted(tutte) == sorted(set(BOOK_COLLAUDO) | {"DELTA.PA"})
    assert len(tutte) == len(set(tutte)), "un ticker in due caselle"


def test_conservazione_dell_universo_con_i_preferiti(db_con_book):
    """La proprieta' del secondo pezzo, allargata: ogni ticker dell'universo in
    ESATTAMENTE una casella, e le caselle non inventano ticker."""
    _aggiungi_preferiti(db_con_book, ["DELTA.PA", "EPSILON.MC"])
    r = radar_completo(today="2026-08-05", entro_giorni=7,
                       db_path=db_con_book, fonti=FONTI_COLLAUDO)
    c = r["copertura"]
    tutte = c["a_registro"] + c["con_fonte"] + c["non_applicabili"] + c["scoperti"]
    assert len(tutte) == c["universo"] == len(set(tutte))
    assert sorted(tutte) == sorted(c["provenienza"])
    assert "CHIUSA.MI" not in tutte, "la posizione chiusa resta fuori"


def test_tabella_preferiti_assente_e_un_buco_DICHIARATO(tmp_path):
    """Un DB piu' vecchio dello schema canonico non ha `favorite_companies`.
    Il radar non muore e NON dichiara zero preferiti come se li avesse
    contati: `preferiti_stato` dice che non li ha letti (regola PM 14/07)."""
    db = str(tmp_path / "senza_preferiti.db")
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    con.execute("CREATE TABLE positions (ticker TEXT, nome TEXT, quantita REAL)")
    con.execute("INSERT INTO positions VALUES ('ACME.MI','Acme SpA',10)")
    con.commit()
    con.close()
    r = radar_completo(today="2026-08-05", db_path=db, fonti={})
    c = r["copertura"]
    assert c["preferiti"] == 0
    assert "NON LETTI" in c["preferiti_stato"], c["preferiti_stato"]
    assert "favorite_companies" in c["preferiti_stato"], \
        "il motivo deve dire QUALE tabella manca"
    assert c["universo"] == 1 and c["provenienza"] == {"ACME.MI": "posizione"}


def test_il_radar_RILEGGE_il_negozio_a_ogni_chiamata(tmp_path, monkeypatch):
    """Snapshot PER CHIAMATA, non dell'import (decisione del Task 6): una
    modifica a mano di `data/fonti_guidance.json` si vede alla chiamata dopo,
    senza riavviare il backend. Misura sul COMPORTAMENTO: il negozio cambia
    fra due chiamate e la seconda deve vedere il valore nuovo — cade sia se
    il default torna a essere lo snapshot dell'import, sia se qualcuno
    memorizza il caricamento."""
    import bellomberg.market_data.fonti_guidance as fg
    db = str(tmp_path / "universo.db")
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    con.execute("CREATE TABLE positions (ticker TEXT, nome TEXT, quantita REAL)")
    con.execute("CREATE TABLE favorite_companies (ticker TEXT PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO positions VALUES ('ACME.MI','Acme SpA',10)")
    con.commit()
    con.close()

    negozio = tmp_path / "fonti_guidance.json"
    negozio.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(negozio))

    prima = radar_completo(today="2026-08-05", db_path=db)   # fonti=None
    assert prima["copertura"]["scoperti"] == ["ACME.MI"]

    negozio.write_text(json.dumps({
        "ACME.MI": {"nome": "Acme SpA", "tipo": "equity",
                    "ir_url": "https://acme.invalid/investors",
                    "next_report_date": "2026-09-30",
                    "next_report_source": "calendario IR acme.invalid",
                    "verificato_il": "2026-09-01"}}), encoding="utf-8")

    dopo = radar_completo(today="2026-08-05", db_path=db)    # fonti=None
    assert dopo["copertura"]["scoperti"] == []
    assert dopo["copertura"]["con_fonte"] == ["ACME.MI"], \
        "il radar sta ancora usando uno snapshot vecchio del negozio"


def _db_una_posizione(tmp_path, nome="uno.db"):
    db = str(tmp_path / nome)
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    con.execute("CREATE TABLE positions (ticker TEXT, nome TEXT, quantita REAL)")
    con.execute("CREATE TABLE favorite_companies (ticker TEXT PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO positions VALUES ('ACME.MI','Acme SpA',10)")
    con.commit()
    con.close()
    return db


VOCE_ACME = {"nome": "Acme SpA", "tipo": "equity",
             "ir_url": "https://acme.invalid/investors",
             "next_report_date": "2026-12-01",
             "next_report_source": "calendario IR acme.invalid",
             "verificato_il": "2026-09-01"}


def test_negozio_ROTTO_e_negozio_ASSENTE_non_si_confondono_col_censito(tmp_path, monkeypatch):
    """Rilievo della review (round 1): senza `fonti_stato` i tre mondi davano
    una `copertura` IDENTICA. Negozio buono senza la voce, negozio ROTTO e
    negozio ASSENTE finivano tutti in `scoperti`, e due verita' diverse —
    «questo ticker non ha una fonte censita» e «non lo so, il negozio non si
    legge» — collassavano in una. `carica_fonti()` il motivo ce l'ha; era
    `fonti_correnti()` a buttarlo via (regola PM 14/07)."""
    import bellomberg.market_data.fonti_guidance as fg
    db = _db_una_posizione(tmp_path)
    negozio = tmp_path / "fonti_guidance.json"
    monkeypatch.setattr(fg, "PERCORSO_FONTI", str(negozio))

    # 1. negozio BUONO
    negozio.write_text(json.dumps({"ACME.MI": VOCE_ACME}), encoding="utf-8")
    buono = radar_completo(today="2026-08-05", db_path=db)["copertura"]
    assert buono["con_fonte"] == ["ACME.MI"] and buono["scoperti"] == []
    assert str(negozio) in buono["fonti_stato"] and "1 voci" in buono["fonti_stato"]

    # 2. negozio ROTTO: JSON invalido
    negozio.write_text("{non e' json", encoding="utf-8")
    rotto = radar_completo(today="2026-08-05", db_path=db)["copertura"]
    assert rotto["scoperti"] == ["ACME.MI"], "senza fonti il ticker resta scoperto"
    assert "NEGOZIO NON LETTO" in rotto["fonti_stato"], rotto["fonti_stato"]
    assert "illeggibile" in rotto["fonti_stato"], rotto["fonti_stato"]

    # 3. negozio ASSENTE
    negozio.unlink()
    assente = radar_completo(today="2026-08-05", db_path=db)["copertura"]
    assert assente["scoperti"] == ["ACME.MI"]
    assert "NEGOZIO NON LETTO" in assente["fonti_stato"], assente["fonti_stato"]
    assert "assente" in assente["fonti_stato"], assente["fonti_stato"]

    # ed e' proprio la voce che li distingue: `scoperti` da solo non basta
    assert rotto["scoperti"] == assente["scoperti"] == buono["con_fonte"]
    assert len({buono["fonti_stato"], rotto["fonti_stato"],
                assente["fonti_stato"]}) == 3, \
        "i tre mondi devono essere distinguibili dall'uscita del radar"


def test_la_mappa_iniettata_dichiara_che_il_negozio_non_e_stato_letto(tmp_path):
    """Chi passa `fonti=` sta misurando altro: l'uscita non deve far credere
    che quella mappa venga dal negozio."""
    db = _db_una_posizione(tmp_path)
    c = radar_completo(today="2026-08-05", db_path=db,
                       fonti={"ACME.MI": VOCE_ACME})["copertura"]
    assert "chiamante" in c["fonti_stato"] and "1 voci" in c["fonti_stato"]
    assert "negozio non e' stato letto" in c["fonti_stato"]


def test_preferito_senza_ticker_scartato_e_CONTATO(tmp_path):
    """`favorite_companies.ticker` e' PRIMARY KEY, che in SQLite ammette NULL:
    un vuoto e' raggiungibile dalla CRUD dell'API. Si filtra — non e'
    radarabile — ma la riga scartata si DICHIARA, non sparisce."""
    db = _db_una_posizione(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO favorite_companies (ticker, name) VALUES (NULL,'senza ticker')")
    con.execute("INSERT INTO favorite_companies (ticker, name) VALUES ('','vuoto')")
    con.execute("INSERT INTO favorite_companies (ticker, name) VALUES ('DELTA.PA','Delta SA')")
    con.commit()
    con.close()
    c = radar_completo(today="2026-08-05", db_path=db, fonti={})["copertura"]
    assert c["preferiti"] == 1 and c["provenienza"]["DELTA.PA"] == "preferito"
    assert "2 righe SCARTATE" in c["preferiti_stato"], c["preferiti_stato"]


def test_il_radar_non_tiene_uno_snapshot_dell_import(tmp_path):
    """Cintura strutturale della prova qui sopra: se `FONTI` rientrasse nel
    modulo come nome importato, il default tornerebbe a essere il valore
    dell'import e la freschezza dipenderebbe da chi ricorda di non usarlo."""
    import bellomberg.market_data.guidance_watch as gw
    assert "FONTI" not in dir(gw), \
        "guidance_watch ha di nuovo uno snapshot dell'import fra i suoi nomi"
