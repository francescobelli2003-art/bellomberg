"""L'edge scanner deve coprire il book INTERO, e dichiarare quel che non copre.
(22/08, Opus 5, chat backend — voce F)

IL DIFETTO FU MISURATO su un portafoglio reale nell'agosto 2026, prima del fix,
chiamando gli endpoint di produzione. Il libro della batteria qui sotto e'
INVENTATO (03/09): riproduce le CONDIZIONI del difetto — piu' posizioni del
taglio, una coda che ci cade sotto, nomi con e senza suffisso di borsa — e non
la composizione, l'ordine o la taglia di nessun portafoglio vero. I rapporti
fra i numeri restano, le identita' no.

  `scan_portfolio(max_tickers=20)` — il 20 e' un DEFAULT DI FIRMA che nessuno
  puo' passare da fuori (ne' lo schema del tool in `chat_tools`, ne' l'endpoint
  `/signals/edge_scan` espongono il parametro). Il taglio avviene DOPO la
  conversione FX, quindi cadono le posizioni piu' piccole in EUR — MAI passate
  dallo scanner.

  Cosa nascondeva quel taglio, misurato uno per uno su `/signals/position_doctor`
  (che chiama esattamente `scan_ticker`):

      ORYN -> Congress Cluster Buy 100 · Vol Risk Premium 87 · Vol Regime 84
              (il giro pieno delle 20:14 dava 89 sullo stesso segnale: l'IV si
               muove fra due misure a 40' di distanza — sono due misure, non un errore)
      KEPL -> Vol Risk Premium 100 · Congress Cluster Sell 100
      VRTA -> Vol Risk Premium 77

  (i nomi sono quelli inventati della batteria; le forze sono quelle misurate
  allora sui nomi veri corrispondenti)

  Cioe' TRE segnali a forza 100 e uno a 87 — alla pari col meglio di quelli che
  passavano. E due dei tagliati stavano dentro `us_options`, l'insieme che esiste
  apposta per dire "su questi gira la scansione opzioni": 2 dei 5 non ci
  arrivavano mai.

  Secondo taglio, a valle: `capo.py` inietta `scan["signals"][:14]` senza dirlo,
  e la riga di log stampa il TOTALE, non quanti ne inietta. Nel log vero di 8 run:
  "Edge scan iniettato: 13 12 12 12 11 19 17 13" (ordine vero del log; ce ne sono
  altre due in `data/scheduler.log`, 14 e 14, fuori da queste 8) — sulle run da 19 e 17 il Capo
  ne ha visti 14, cioe' 5 e 3 buttati in silenzio mentre il log diceva 19 e 17.

  Terza cosa: se lo scanner cade, il blocco EDGE SCAN sparisce dal prompt del
  Capo SENZA UNA PAROLA. E le strade sono due, non una: l'eccezione (che almeno
  stampa su stdout) e il payload `{"error": ...}`, dove `scan.get("signals")` e'
  None e non stampa nemmeno quello.

  Zero test su `scan_portfolio` prima di oggi.

REGOLA DI CASA (PM 14/07): un contenuto che non entra tutto DICE quante voci
restano fuori e come recuperarle. Questi test non vietano i tetti: pretendono
che ogni tetto si dichiari, e che la via di recupero indicata ESISTA.
"""
import json
import os
import re

import pytest

import bellomberg.portfolio.signal_engine as se

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Il libro di questa batteria e' INVENTATO: nomi che non sono societa' reali e
# valori scelti a tavolino. Conserva le sole proprieta' che i test provano — piu'
# posizioni del taglio a 20, una coda che ci cade sotto, e un mix di nomi col punto
# (non-US) e senza (US, gli unici su cui girano OPRA e i trade del Congresso).
# NON e' il portafoglio di nessuno: ne' la composizione, ne' l'ordine, ne' la taglia.
LIBRO_VERO = [
    ("BETA.L", 9000.00), ("NOVX", 7000.00), ("ALFA.MI", 6200.00),
    ("GAMM.MI", 5800.00), ("DELT.MI", 5400.00), ("RHOM.DE", 5000.00),
    ("EPSI.MI", 4700.00), ("IOTA.MI", 4400.00), ("KAPP.MI", 4100.00),
    ("LAMB.MI", 3900.00), ("QDEX", 3700.00), ("SIGM.MI", 3500.00),
    ("TAUX.MI", 3300.00), ("OMEG.MI", 3150.00), ("PHIX.FRA", 3050.00),
    ("CHIA.MI", 2950.00), ("PSIQ.MI", 2850.00), ("ZENT", 2750.00),
    ("XIVA.MI", 2650.00), ("HALO", 2600.00),
    # da qui in giu' il vecchio [:20] tagliava:
    ("VRTA", 2500.00), ("NUEX.MI", 2300.00), ("KEPL", 2100.00),
    ("THET.VI", 1900.00), ("ORYN", 1700.00),
]
TAGLIATE_DAL_VENTI = [t for t, _ in LIBRO_VERO[20:]]


def _sorgente(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def scanner_senza_rete(monkeypatch):
    """Sostituisce i rilevatori (che chiamano API vere) con spie che registrano
    CHI e' stato interrogato e CON QUANTI rilevatori. Il portafoglio e' il libro
    vero; nessuna rete, nessun DB toccato."""
    from bellomberg.storage import memory_db

    class _DBFinto:
        def get_portfolio_summary(self):
            return {"positions": [
                {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
                for t, v in LIBRO_VERO
            ]}

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: _DBFinto())

    visti = {"piena": [], "solo_prezzo": []}

    # ⚠️ Le spie DEPOSITANO gli esiti come i rilevatori veri. Senza, la
    # copertura le classificherebbe come «nessuna misura» e il test
    # collauderebbe uno scenario che la produzione non produce (e' il difetto
    # che la review del 22/08 ha trovato nella prima stesura, al contrario).
    def _scan_ticker_spia(tk, esiti=None, **k):
        visti["piena"].append(tk)
        if esiti is not None:
            esiti["vol risk premium"] = "interrogato"
            esiti["dealer gamma"] = "interrogato"
            esiti["z-score di prezzo"] = "interrogato"
            esiti["cluster del Congresso"] = (
                "interrogato" if "." not in tk
                else "NON APPLICABILE: ticker col punto")
        return [se._sig(tk, "volatility", "Vol Risk Premium", 1.0, "ctx",
                        "bullish", 80, "lettura", "spia")]

    def _zscore_spia(tk, esiti=None, *a, **k):
        visti["solo_prezzo"].append(tk)
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return [se._sig(tk, "momentum", "Price Z-Score (20g)", 1.0, "ctx",
                        "neutral", 60, "lettura", "spia")]

    monkeypatch.setattr(se, "scan_ticker", _scan_ticker_spia)
    monkeypatch.setattr(se, "sig_price_zscore", _zscore_spia)

    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    return visti


# ============================================================
# 1. LA COPERTURA
# ============================================================

def test_scansiona_tutte_le_posizioni_non_le_prime_venti(scanner_senza_rete):
    """Il difetto in una riga: `[:max_tickers]` con max_tickers=20 di default."""
    se.scan_portfolio(min_strength=45)
    interrogati = set(scanner_senza_rete["piena"]) | set(scanner_senza_rete["solo_prezzo"])
    mancanti = [t for t, _ in LIBRO_VERO if t not in interrogati]
    assert not mancanti, (
        "lo scanner non ha interrogato %d posizioni su %d: %s"
        % (len(mancanti), len(LIBRO_VERO), mancanti))


def test_il_payload_dichiara_quante_posizioni_ha_scansionato_su_quante(scanner_senza_rete):
    """Un numero zitto non e' una misura: chi legge deve sapere il denominatore."""
    out = se.scan_portfolio(min_strength=45)
    cop = out.get("copertura")
    assert cop, "il payload non dichiara la copertura"
    assert cop["posizioni_totali"] == len(LIBRO_VERO)
    assert cop["posizioni_scansionate"] == len(LIBRO_VERO)
    assert cop["non_scansionate"] == []


def test_un_tetto_esplicito_dichiara_chi_resta_fuori_e_come_recuperarlo(scanner_senza_rete):
    """Il tetto resta possibile, ma smette di essere muto: deve nominare le
    escluse e indicare una via di recupero che ESISTE davvero."""
    out = se.scan_portfolio(min_strength=45, max_tickers=20)
    cop = out["copertura"]
    assert cop["posizioni_scansionate"] == 20
    assert cop["non_scansionate"] == TAGLIATE_DAL_VENTI, (
        "le escluse non sono dichiarate per nome: senza i nomi, chi legge non "
        "sa nemmeno cosa chiedere")
    assert "get_position_doctor" in cop.get("recupero", ""), (
        "la dichiarazione non indica come recuperare le escluse")


def test_dichiara_che_sui_nomi_non_us_gira_un_rilevatore_su_quattro(scanner_senza_rete):
    """La `description` prometteva sei coperture per tutti. Sul libro intero, 18
    posizioni su 25 ricevono UN SOLO rilevatore per-ticker, lo z-score di prezzo:
    opzioni OPRA e trade del Congresso su ALFA.MI non esistono.

    ⚠️ I numeri che stavano qui erano quelli del libro TAGLIATO a 20, montati
    sulla frase che dichiarava il totale: numeri veri di una misura diversa.
    Trovato dalla review del 22/08 — la classe di errore resta anche ora che il
    libro della batteria e' inventato.

    ⚠️ E NON e' vero che quei nomi ricevano una misura sola in assoluto: le
    esposizioni fattoriali li coprono dall'altra strada (`_region_for_ticker`
    mappa .MI/.DE/.L/.VI su `europe`). La distinzione e' fra i quattro
    rilevatori PER-TICKER e la strada fattoriale."""
    out = se.scan_portfolio(min_strength=45)
    cop = out["copertura"]
    piena = set(cop["scansione_piena"])
    solo = set(cop["solo_prezzo"])
    assert piena & solo == set(), "un ticker non puo' stare in entrambi i gruppi"
    assert piena | solo == {t for t, _ in LIBRO_VERO}
    # Sul libro INTERO: i 7 senza suffisso di borsa (QDEX, NOVX, ZENT, HALO +
    # VRTA, KEPL, ORYN, che il vecchio [:20] tagliava). Gli altri 18 sono quotati
    # su mercati dove OPRA e i trade del Congresso non esistono.
    assert piena == {"QDEX", "NOVX", "ZENT", "HALO", "VRTA", "KEPL", "ORYN"}
    assert len(solo) == 18


def test_la_nota_di_copertura_e_leggibile_dagli_agenti(scanner_senza_rete):
    """Gli agenti leggono prosa, non solo chiavi: la copertura va detta a parole
    dentro il payload, coi numeri veri."""
    out = se.scan_portfolio(min_strength=45)
    nota = out["copertura"].get("nota", "")
    # ⚠️ Le prime versioni di queste due asserzioni erano PASSANTI su un codice
    # guasto (mutazioni 22/08): "25" compariva anche in "Scansionate 25
    # posizioni." senza denominatore, e "prezzo" compariva nell'elenco dei 4
    # rilevatori anche quando la frase sui non-US era stata tolta.
    assert re.search(r"%d posizioni su %d" % (len(LIBRO_VERO), len(LIBRO_VERO)), nota), (
        "la nota non porta il denominatore: %r" % nota)
    assert re.search(r"unico rilevatore PER-TICKER applicabile e' lo z-score", nota), (
        "la nota non dice che sui non-US l'unico rilevatore per-ticker "
        "applicabile e' lo z-score: %r" % nota)


# ============================================================
# 2. CONTRO-PROVA: cosa costava il taglio (documenta il difetto)
# ============================================================

def test_contro_prova_il_vecchio_taglio_a_venti_perdeva_l_undici_per_cento():
    """Se un domani qualcuno rimette il 20, questo test dice cosa si perde."""
    totale = sum(v for _, v in LIBRO_VERO)
    perse = sum(v for _, v in LIBRO_VERO[20:])
    assert len(LIBRO_VERO[20:]) == 5
    assert 10.5 < perse / totale * 100 < 11.5, "fixture non piu' rappresentativa"
    # I tre nomi che portavano i segnali a forza 100 stavano tutti fuori:
    for tk in ("ORYN", "KEPL", "VRTA"):
        assert tk in TAGLIATE_DAL_VENTI


def test_i_nomi_con_opzioni_note_non_possono_cadere_per_dimensione(scanner_senza_rete):
    """`us_options` esiste per dire "su questi gira la scansione opzioni".
    Col taglio a 20, VRTA e ORYN — 2 dei 5 — non ci arrivavano mai."""
    se.scan_portfolio(min_strength=45)
    piena = set(scanner_senza_rete["piena"])
    # ⚠️ NON si usa `se.US_OPTIONS` come metro: sarebbe circolare — togliendo
    # VRTA dall'insieme, anche l'atteso si restringe e il test resta verde
    # (mutazione sopravvissuta il 22/08). I nomi sono letterali, e sono quelli
    # su cui la scansione piena ha prodotto segnali VERI il 21/08:
    #   VRTA Vol Risk Premium 77 · ORYN Vol Risk Premium 89 + Congress Buy 100
    attesi = {"NOVX", "QDEX", "VRTA", "ORYN"}
    assert attesi <= piena, (
        "ticker con opzioni note esclusi dalla scansione piena: %s" % (attesi - piena))


# ============================================================
# 3. IL SECONDO TAGLIO — il Capo
# ============================================================

def test_il_capo_dichiara_i_segnali_che_restano_fuori_dal_tetto():
    """Nel log vero: 19 e 17 segnali, 14 iniettati, 5 e 3 buttati in silenzio."""
    from bellomberg.agents import capo
    scan = {"signals": [
        se._sig("TCK%d" % i, "momentum", "Seg %d" % i, 1.0, "ctx", "neutral",
                90 - i, "lettura", "spia") for i in range(19)
    ], "copertura": {"nota": "25 posizioni su 25 scansionate."}}
    righe = capo._blocco_edge_scan(scan, tetto=14)
    testo = "\n".join(righe)
    assert "[forza 77] TCK13" in testo, "il tetto non e' 14"
    # ⚠️ `assert "5" in testo` era VACUA: "5" compare in TCK5 e in [forza 85].
    # Si asserisce il conteggio nella sua frase, non una cifra qualsiasi.
    assert re.search(r"altri 5 segnali", testo), (
        "il numero dei segnali fuori non e' quello vero:\n" + testo)
    assert re.search(r"NON entrano in questo prompt", testo), (
        "il taglio e' muto: nel prompt non c'e' nessuna frase che lo dichiari")
    assert "NON puoi richiederli" in testo, (
        "non dice al Capo che in questa run non ha modo di recuperarli")


def test_il_capo_riporta_la_copertura_dello_scanner():
    """Il Capo decide su questi segnali: deve sapere su quante posizioni
    sono stati misurati."""
    from bellomberg.agents import capo
    scan = {"signals": [se._sig("NOVX", "momentum", "Z", 1.0, "c", "neutral",
                                80, "l", "spia")],
            "copertura": {"nota": "Scansionate 25 posizioni su 25."}}
    testo = "\n".join(capo._blocco_edge_scan(scan, tetto=14))
    assert "25 posizioni su 25" in testo


def test_il_capo_dice_quando_lo_scanner_non_ha_risposto():
    """Il ramo muto peggiore: `{"error": ...}` non stampava nemmeno su stdout."""
    from bellomberg.agents import capo
    testo = "\n".join(capo._blocco_edge_scan({"error": "portfolio fetch: DB locked"}))
    assert testo.strip(), "il blocco sparisce dal prompt senza una parola"
    assert "DB locked" in testo, "il motivo non risale al prompt"
    # ⚠️ La regex di prima era soddisfatta dal solo HEADER: cancellando la frase
    # che spiega, il test restava verde. Si asserisce la frase.
    corpo = "\n".join(r for r in testo.splitlines() if not r.startswith("==="))
    assert re.search(r"NON sono state misurate", corpo), (
        "la frase che spiega e' sparita, resta solo l'intestazione:\n" + testo)
    assert re.search(r"non dedurne", corpo, re.I), corpo


def test_il_capo_non_scambia_zero_segnali_per_scanner_rotto():
    """Uno zero da uno scanner che ha girato E' una misura, e va detto come tale
    — la distinzione che la voce E ha insegnato ieri."""
    from bellomberg.agents import capo
    # ⚠️ Serve una copertura COMPLETA e dimostrata: senza le chiavi, il blocco
    # non puo' provare che i rilevatori abbiano risposto e sceglie — bene — il
    # ramo prudente. E' il comportamento che protegge le fixture di
    # test_capo_collasso/test_capo_streaming, che passano `{"signals": []}` nudo.
    testo = "\n".join(capo._blocco_edge_scan({"signals": [], "copertura": {
        "nota": "Scansionate 25 posizioni su 25.", "posizioni_scansionate": 25,
        "scansione_degradata": {}, "nessuna_misura": {}, "fattoriali_ko": None}}))
    assert testo.strip(), "silenzio anche sullo zero misurato"
    # ⚠️ Anche qui l'header bastava: si asserisce la distinzione, che e' il
    # motivo per cui questo ramo esiste.
    corpo = "\n".join(r for r in testo.splitlines() if not r.startswith("==="))
    assert re.search(r"E' una misura", corpo), (
        "la frase che distingue lo zero misurato dal buco e' sparita:\n" + testo)
    assert "DB locked" not in testo


def test_se_lo_scanner_solleva_il_capo_lo_dichiara_nel_prompt():
    """Oggi l'except stampa su stdout e prosegue: il memo esce senza EDGE SCAN
    e senza sapere che manca. E' la stessa forma del red team che puo' sparire.

    ⚠️ Il corpo dell'`except` va isolato DAVVERO: una finestra di +/-400 char
    attorno alla riga di log pesca gli `append` del ramo felice qui sopra e il
    test passa verde sul difetto (misurato: e' successo alla prima stesura).
    """
    righe = _sorgente("src/bellomberg/agents/capo.py").splitlines()
    n_log = next((i for i, r in enumerate(righe) if "[CAPO] Edge scan skip" in r), None)
    assert n_log is not None, "il ramo di errore dell'edge scan e' sparito"
    n_exc = next(i for i in range(n_log, -1, -1)
                 if righe[i].lstrip().startswith("except "))
    indent = len(righe[n_exc]) - len(righe[n_exc].lstrip())
    corpo = []
    for r in righe[n_exc + 1:]:
        if r.strip() and (len(r) - len(r.lstrip())) <= indent:
            break
        corpo.append(r)
    assert any("user_msg_parts" in r for r in corpo), (
        "l'except dell'edge scan non scrive nulla nel prompt del Capo: "
        "il blocco sparisce in silenzio. Corpo dell'except:\n" + "\n".join(corpo))


def test_il_conteggio_del_log_e_quello_delle_righe_finite_nel_prompt():
    """Il log diceva 19 mentre il prompt ne aveva 14: chi guarda il log crede
    che il Capo abbia visto tutto. L'invariante e' funzionale, non sintattica —
    il numero stampato dev'essere quello delle righe di segnale VERE."""
    from bellomberg.agents import capo
    scan = {"signals": [
        se._sig("TCK%d" % i, "momentum", "Seg %d" % i, 1.0, "ctx", "neutral",
                90 - i, "lettura", "spia") for i in range(19)
    ]}
    righe = capo._blocco_edge_scan(scan, tetto=14)
    assert capo._n_segnali_iniettati(righe) == 14
    assert capo._n_segnali_iniettati(righe) != len(scan["signals"])
    # ...e nel ramo di guasto non si stampa "iniettati" su zero righe di segnale
    assert capo._n_segnali_iniettati(capo._blocco_edge_scan({"error": "x"})) == 0


def test_la_riga_di_log_usa_il_conteggio_vero_non_il_totale():
    """La guardia di cablaggio: la produzione deve passare per quel contatore."""
    righe = _sorgente("src/bellomberg/agents/capo.py").splitlines()
    n = next((i for i, r in enumerate(righe) if "Edge scan iniettato" in r), None)
    assert n is not None, "la riga di log e' sparita: test da riscrivere"
    statement = "\n".join(righe[n:n + 3])
    assert "_n_segnali_iniettati" in statement, (
        "il log non usa il conteggio delle righe iniettate:\n" + statement)


# ============================================================
# 4. LA PROMESSA NELLA DESCRIPTION
# ============================================================

def test_la_description_del_tool_non_promette_piu_tutto_il_portafoglio_e_basta():
    """"scansiona tutto il portafoglio" + sei coperture elencate era una promessa
    che non si avvera per la maggioranza dei nomi di un portafoglio con posizioni
    fuori dagli USA. Ora il portafoglio lo scansiona davvero tutto, ma la
    description deve dire che le sei misure non valgono per tutti."""
    import bellomberg.agents.chat_tools as ct
    d = next(t for t in ct.TOOL_DEFINITIONS if t["name"] == "get_edge_scan")["description"]
    assert re.search(r"unico rilevatore per-ticker applicabile e' lo z-score", d), (
        "la description non dichiara che su quei nomi l'unico rilevatore "
        "per-ticker applicabile e' lo z-score di prezzo")
    assert "fuori dagli USA" in d


# ============================================================
# 5. IL TETTO DEL TOOL_RESULT — il taglio che la cura di oggi avvicina
# ============================================================
# Misurato in agosto 2026 su un payload vero con `prova_edge_scan.py`: prima della
# cura il tool_result stava a poco piu' della meta' del tetto di 12.000 char, dopo
# la cura all'88% — perche' la cura fa passare TUTTE le posizioni, non le prime 20.
# Il margine misurato era ~1.500 char, cioe' ~4 segnali. A valle il taglio e' CIECO
# (`specialists/base.py`: `result_str[:_TETTO] + "...[truncated]"`) e cade in
# CODA, dove stanno i segnali. La cura non deve creare un taglio nuovo e muto:
# e' il vincolo del PM ("non voglio che ci siano tagli o si rompa qualcosa").

def _segnali_finti(n, corpo=260):
    return [se._sig("TCK%02d" % i, "momentum", "Segnale numero %d" % i, 1.0,
                    "contesto", "neutral", 99 - i, "L" * corpo, "spia")
            for i in range(n)]


def _payload_finto(n, corpo=260):
    return {"generated": "2026-08-21T20:00:00", "n_signals_total": n,
            "n_signals_strong": n, "by_category": {"momentum": n},
            "copertura": {"posizioni_totali": 25, "posizioni_scansionate": 25,
                          "scansione_piena": [], "solo_prezzo": [],
                          "non_scansionate": [], "rilevatore_caduto": [],
                          "fattoriali_su": 25, "recupero": "",
                          "nota": "Scansionate 25 posizioni su 25."},
            "signals": _segnali_finti(n, corpo), "_source": "spia", "_note": "x"}


def test_un_payload_che_sta_nel_tetto_passa_intatto():
    """Non si degrada quel che non sfora: una `_vista` inutile e' rumore che
    svaluta i caveat veri."""
    import bellomberg.agents.chat_tools as ct
    dentro = _payload_finto(4)
    assert ct._peso_json(dentro) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT
    import copy
    atteso = copy.deepcopy(dentro)
    out = ct._compatta_edge_scan(dentro)
    # ⚠️ `out == dentro` confrontava l'oggetto con se stesso (la funzione torna
    # `r`): passava anche su una mutazione in place. Il metro e' una copia
    # fatta PRIMA.
    assert out == atteso
    assert "_vista" not in out
    assert all(x.get("reading") for x in out["signals"])


def test_se_sfora_toglie_le_letture_dei_piu_deboli_e_lo_dichiara():
    """La lettura in prosa e' il pezzo grasso (~260 char a segnale). Si toglie
    dal PIU' DEBOLE in su, e nessun segnale sparisce dall'elenco."""
    import bellomberg.agents.chat_tools as ct
    fuori = _payload_finto(40)
    assert ct._peso_json(fuori) + ct._BUSTA_STAMP > ct.TETTO_TOOL_RESULT
    out = ct._compatta_edge_scan(fuori)
    assert ct._peso_json(out) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT, (
        "la vista compatta sfora comunque: %d char" % ct._peso_json(out))
    assert len(out["signals"]) == 40, "un segnale e' sparito invece di dimagrire"
    assert out["signals"][0]["reading"], "il segnale PIU' FORTE ha perso la lettura"
    assert "_vista" in out and re.search(r"lettur", out["_vista"], re.I), (
        "la degradazione non e' dichiarata: %r" % out.get("_vista"))
    # ⚠️ `re.search(r"\d+")` era VACUA: il primo pezzo contiene sempre 12000.
    # Si conta quante letture sono state DAVVERO tolte e si pretende il numero.
    tolte = sum(1 for x in out["signals"] if not x.get("reading"))
    assert tolte > 0
    assert re.search(r"prosa di %d segnali" % tolte, out["_vista"]), (
        "il numero dichiarato non e' quello vero (%d tolte): %r" % (tolte, out["_vista"]))


def test_se_le_letture_non_bastano_toglie_i_segnali_e_li_conta():
    """Caso estremo: tanti segnali che nemmeno senza prosa ci stanno. Allora si
    tolgono i piu' deboli, ma il payload DICE quanti e come recuperarli."""
    import bellomberg.agents.chat_tools as ct
    out = ct._compatta_edge_scan(_payload_finto(300, corpo=40))
    assert ct._peso_json(out) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT
    tolti = 300 - len(out["signals"])
    assert tolti > 0
    # ⚠️ `r"\d+ segnali"` matchava la frase delle LETTURE, non quella dei
    # segnali tolti: il conteggio che diceva di verificare non era verificato.
    assert re.search(r"Tolti anche %d segnali" % tolti, out["_vista"]), (
        "il conteggio dei segnali tolti non e' quello vero (%d): %r"
        % (tolti, out["_vista"]))


def test_la_dichiarazione_e_la_copertura_sopravvivono_al_taglio_in_coda():
    """Il taglio a valle e' cieco e cade in CODA: `_vista` e `copertura` devono
    essere serializzate PRIMA dei segnali, o si perde proprio cio' che spiega."""
    import bellomberg.agents.chat_tools as ct
    out = ct._compatta_edge_scan(_payload_finto(40))
    s = json.dumps(out, ensure_ascii=False, default=str)
    p_vista, p_cop, p_sig = s.find('"_vista"'), s.find('"copertura"'), s.find('"signals"')
    assert 0 <= p_vista < p_sig, "_vista non precede i segnali"
    assert 0 <= p_cop < p_sig, "copertura non precede i segnali"


def test_il_percorso_llm_degrada_e_l_endpoint_http_resta_intatto():
    """Le viste compatte vivono SOLO sul percorso LLM: il frontend consuma
    /signals/edge_scan e non deve vedere una forma diversa."""
    # ⚠️ NON una finestra a caratteri fissi: la prima stesura usava 400 char e
    # ci e' sconfinata dentro appena un commento si e' allungato (previsto dalla
    # review, poi successo). Il ramo si delimita sul ramo SUCCESSIVO.
    def _ramo(src, inizio, fine):
        i = src.find(inizio)
        assert i > 0, "ancora non trovata: " + inizio
        j = src.find(fine, i + len(inizio))
        return src[i:j if j > 0 else len(src)]

    ramo_llm = _ramo(_sorgente("src/bellomberg/agents/chat_tools.py"),
                     'if tool_name == "get_edge_scan"', 'if tool_name == "get_position_doctor"')
    assert "_compatta_edge_scan" in ramo_llm, (
        "il percorso LLM non passa dalla vista: il taglio a valle resta cieco")
    ramo_http = _ramo(_sorgente("src/bellomberg/api/bellomberg_api.py"),
                      "def get_edge_scan_endpoint", "def get_position_doctor_endpoint")
    assert "_compatta_edge_scan" not in ramo_http, (
        "l'endpoint HTTP e' stato degradato: cambia forma sotto il frontend")
    # 22/08 sera (decisione (a) del PM): la chiamata porta anche `force`,
    # l'ancora segue la forma nuova. Sempre scanner DIRETTO, mai la vista.
    assert "scan_portfolio(min_strength=min_strength, force=force)" in ramo_http, (
        "l'endpoint non chiama piu' lo scanner diretto (o ha perso `force`)")


# ============================================================
# 6. I BUCHI CHE LE MUTAZIONI DEL 22/08 HANNO TROVATO
# ============================================================
# Dieci mutazioni su 27 sopravvivevano alla batteria verde. Questi test sono
# quelli che mancavano: senza, un codice guasto passava.

def test_un_rilevatore_che_cade_non_viene_dichiarato_come_copertura(monkeypatch):
    """Il ramo dell'ECCEZIONE. ⚠️ La review del 22/08 ha misurato che nessun
    rilevatore vero solleva mai, quindi questo NON e' lo stato di guasto tipico
    (quello sta in `test_un_nome_con_rilevatori_muti_...`, dove la fonte torna
    `{"error": ...}` come fa davvero). Resta come cintura: se un domani qualcosa
    solleva, il nome non dev'essere dichiarato coperto."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
            for t, v in LIBRO_VERO]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})

    def _scan_rotto(tk, esiti=None, **k):
        if tk in ("KEPL", "ORYN"):
            raise RuntimeError("Polygon 429")
        if esiti is not None:
            esiti["vol risk premium"] = "interrogato"
        return []

    def _z(tk, esiti=None, *a, **k):
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "scan_ticker", _scan_rotto)
    monkeypatch.setattr(se, "sig_price_zscore", _z)

    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert "KEPL" not in cop["scansione_piena"], "dichiara coperto un nome su cui e' caduto"
    assert "ORYN" not in cop["scansione_piena"]
    senza = cop["nessuna_misura"]
    assert "KEPL" in senza and "ORYN" in senza, "il guasto non e' dichiarato: %r" % cop
    assert "RuntimeError" in senza["KEPL"], "non dice nemmeno CHE guasto e' stato"
    assert "get_position_doctor" in cop["recupero"], (
        "un nome caduto non ha via di recupero")


def test_i_nomi_coperti_dai_fattori_sono_contati_non_supposti(monkeypatch):
    """`fattoriali_su` e' una misura della seconda strada, non un letterale.
    (Mutazione sopravvissuta: `fattoriali_su = 25` fisso.)"""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
            for t, v in LIBRO_VERO]}})())
    monkeypatch.setattr(se, "scan_ticker", lambda tk, *a, **k: [])
    monkeypatch.setattr(se, "sig_price_zscore", lambda tk, *a, **k: [])
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {"NOVX": {}, "QDEX": {}, "ORYN": {}}})

    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert cop["fattoriali_su"] == 3, (
        "fattoriali_su non conta i nomi veri: %r" % cop["fattoriali_su"])
    assert "3 nomi" in cop["nota"]


def test_se_i_fattori_non_si_calcolano_il_buco_e_dichiarato(monkeypatch):
    """La seconda strada puo' cadere: allora la copertura che prometteva manca,
    e va detto invece di sparire in un `except: pass`."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": "NOVX", "valore_mercato": 1.0, "valuta": "EUR"}]}})())
    monkeypatch.setattr(se, "scan_ticker", lambda tk, *a, **k: [])

    def _rotto(*a, **k):
        raise ValueError("cache FF5 illeggibile")

    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors", _rotto)
    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert cop["fattoriali_su"] is None
    assert "cache FF5 illeggibile" in cop["nota"], (
        "il buco fattoriale e' muto: %r" % cop["nota"])


def test_la_copertura_e_serializzata_PRIMA_dei_segnali(scanner_senza_rete):
    """Il taglio a valle e' cieco e cade in coda. Se `copertura` finisce dopo
    `signals`, la dichiarazione e' la prima cosa che si perde — esattamente il
    difetto trovato ieri sulla voce E. (Mutazione sopravvissuta.)
    22/08 sera: stessa regola per `cache` — la dichiarazione di un dato
    riscaldato che non sopravvive al taglio non dichiara niente."""
    out = se.scan_portfolio(min_strength=45)
    chiavi = list(out.keys())
    assert chiavi.index("copertura") < chiavi.index("signals"), (
        "ordine delle chiavi: %s" % chiavi)
    assert chiavi.index("cache") < chiavi.index("signals"), (
        "la dichiarazione di cache sta DOPO i segnali, dove cade il taglio: %s"
        % chiavi)
    s = json.dumps(out, ensure_ascii=False, default=str)
    assert 0 <= s.find('"copertura"') < s.find('"signals"')
    assert 0 <= s.find('"cache"') < s.find('"signals"')


def test_di_default_il_capo_vede_TUTTI_i_segnali():
    """Il 14 era il numero storico e «va cambiato con una decisione, non per
    sbaglio»: la decisione e' arrivata (PM 22/08 sera, voce (b)
    §9-quinquadragies, col costo misurato PRIMA — +2.100 char ~= 1.050 token a
    run). Il Capo alloca su questi segnali: li vede tutti. Un tetto ESPLICITO
    resta possibile e continua a dichiararsi coi nomi (test sopra)."""
    from bellomberg.agents import capo
    scan = {"signals": [
        se._sig("TCK%02d" % i, "momentum", "Seg", 1.0, "c", "neutral", 99 - i,
                "l", "spia") for i in range(23)
    ]}
    righe = capo._blocco_edge_scan(scan)
    assert capo._n_segnali_iniettati(righe) == 23
    testo = "\n".join(righe)
    assert "NON entrano in questo prompt" not in testo, (
        "senza tetto non ci sono esclusi: la dichiarazione di taglio qui "
        "sarebbe una frase falsa:\n" + testo)


def test_la_description_non_promette_piu_una_copertura_uniforme():
    """(Mutazione sopravvissuta: bastava che la parola "europeo" restasse da
    qualche parte perche' il test verde. Ora si guarda l'affermazione.)"""
    import bellomberg.agents.chat_tools as ct
    d = next(t for t in ct.TOOL_DEFINITIONS if t["name"] == "get_edge_scan")["description"]
    assert "copertura NON e' uniforme" in d, (
        "la description non dichiara la copertura disomogenea")
    assert "`copertura`" in d, "non indirizza al campo che porta i numeri veri"
    # ⚠️ Il codice ramifica sulla PRESENZA DI UN PUNTO, non su un elenco di
    # suffissi: BRK.B e BF.B sono US col punto. La description non deve
    # affermare come fatto di mercato quello che e' un'euristica sul simbolo —
    # e' la classe di `"." not in ticker` -> BA.L -> il CIK di Boeing (22/08).
    assert "punto" in d.lower(), (
        "la description spaccia l'euristica sul punto per un elenco di "
        "suffissi: %r" % d)


def test_us_options_conta_solo_su_un_nome_col_suffisso(monkeypatch):
    """⚠️ Fatto misurato il 22/08 con le mutazioni: `US_OPTIONS` oggi e' INERTE.
    TUTTI i suoi membri sono senza punto, quindi `"." not in tk` li manda gia'
    alla scansione piena da solo: togliere uno qualsiasi dall'insieme non cambia
    NIENTE (mutazione equivalente, non un buco). La proprieta' si asserisce qui
    sotto sull'insieme VERO, senza elencarne i nomi.

    L'insieme serve al caso per cui e' nato — un nome con suffisso di borsa che
    ha comunque opzioni US — e quel ramo non e' mai stato esercitato da nessuno.
    Questo test lo esercita, cosi' se un domani il ramo si rompe si sa.
    NON si cancella `US_OPTIONS`: un presidio che sembra inerte va tenuto con la
    sua ragione scritta (lezione del 22/08, il filtro `"." not in ticker`).
    """
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors

    # La misura che rende vera la frase qui sopra, senza nominare nessun membro:
    # se un domani ne entra uno COL punto, l'insieme smette di essere inerte e
    # questa riga lo dice (03/09).
    assert all("." not in t for t in se.US_OPTIONS), (
        "US_OPTIONS non e' piu' inerte: %d membri col suffisso di borsa"
        % sum(1 for t in se.US_OPTIONS if "." in t))

    monkeypatch.setattr(se, "US_OPTIONS", {"BETA.L"})
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": "BETA.L", "valore_mercato": 12000.0, "valuta": "EUR"},
            {"ticker": "ALFA.MI", "valore_mercato": 4500.0, "valuta": "EUR"}]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    visti = []
    vero_scan_ticker = se.scan_ticker

    def _spia(tk, esiti=None, **k):
        visti.append(tk)
        if esiti is not None:
            esiti["vol risk premium"] = "interrogato"
            esiti["dealer gamma"] = "interrogato"
            esiti["z-score di prezzo"] = "interrogato"
        if esiti is not None and "." in tk:
            esiti["cluster del Congresso"] = "NON APPLICABILE: ticker col punto"
        return []

    def _z(tk, esiti=None, *a, **k):
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "scan_ticker", _spia)
    monkeypatch.setattr(se, "sig_price_zscore", _z)

    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert visti == ["BETA.L"], (
        "un ticker col punto dentro US_OPTIONS non riceve la scansione piena: %s" % visti)
    assert cop["scansione_piena"] == ["BETA.L"]
    assert cop["solo_prezzo"] == ["ALFA.MI"]
    # ⚠️ E la nota NON deve dire «tutti e 4»: su un nome col punto il Congresso
    # non si applica. La prima stesura asseriva proprio lo stato in cui mentiva.
    assert "tutti e 4" not in cop["nota"], cop["nota"]
    esiti_veri = {}
    vero_scan_ticker("BETA.L", esiti=esiti_veri)   # la funzione VERA, non la spia
    assert esiti_veri["cluster del Congresso"].startswith("NON APPLICABILE"), esiti_veri


# ============================================================
# 7. LA COPERTURA DEV'ESSERE UNA MISURA — i reperti ALTA della review 22/08
# ============================================================
# Tre revisori indipendenti su cinque hanno trovato lo stesso difetto, e la
# prova e' una grep: NESSUN `sig_*` solleva mai (tutti chiudono con
# `except Exception: return []`) e le fonti a monte tornano `{"error": ...}`
# invece di lanciare (`vol_surface.py:144`, `positioning_tools.py:56`,
# `quiver_data.py:36`). Quindi il ramo `except` di `scan_portfolio` era
# IRRAGGIUNGIBILE in produzione: `rilevatore_caduto` sempre vuoto, e
# `scansione_piena` contava i TENTATIVI.
# Con Polygon giu', il prompt del Capo dichiarava che NOVX/KEPL/ZENT avevano
# ricevuto misure di volatilita' che non avevano ricevuto: prima della cura
# quell'assenza era MUTA, dopo era un'AFFERMAZIONE FALSA all'agente che alloca.
# I test della prima stesura non lo vedevano perche' la spia SOLLEVAVA:
# collaudavano uno scenario che il codice vero non produce mai.


def test_il_rilevatore_di_volatilita_dichiara_quando_la_fonte_e_muta(monkeypatch):
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface",
                        lambda *a, **k: {"error": "POLYGON_API_KEY mancante o non attiva"})
    esiti = {}
    se.sig_vol_risk_premium("NOVX", esiti=esiti)
    assert esiti.get("vol risk premium", "").startswith("MUTO"), esiti
    assert "POLYGON_API_KEY" in esiti["vol risk premium"]


def test_il_rilevatore_di_volatilita_dichiara_quando_ha_guardato(monkeypatch):
    """Uno zero da una fonte che ha risposto E' una misura, e va distinto."""
    from bellomberg.portfolio import vol_surface
    monkeypatch.setattr(vol_surface, "build_vol_surface", lambda *a, **k: {
        "iv_rv_spread_front": 0.001, "rv_percentile_1y": 50, "expected_move_pct": None})
    esiti = {}
    se.sig_vol_risk_premium("NOVX", esiti=esiti)
    assert esiti.get("vol risk premium") == "interrogato", esiti


def test_il_gamma_dichiara_la_fonte_muta(monkeypatch):
    from bellomberg.portfolio import positioning_tools
    monkeypatch.setattr(positioning_tools, "compute_gex",
                        lambda *a, **k: {"error": "POLYGON_API_KEY mancante o non attiva"})
    esiti = {}
    se.sig_dealer_gamma("NOVX", esiti=esiti)
    assert esiti.get("dealer gamma", "").startswith("MUTO"), esiti


def test_il_congresso_dichiara_la_fonte_spenta(monkeypatch):
    from bellomberg.market_data import quiver_data
    monkeypatch.setattr(quiver_data, "quiver_available", lambda: False)
    esiti = {}
    se.sig_insider_congress("NOVX", esiti=esiti)
    assert esiti.get("cluster del Congresso", "").startswith("MUTO"), esiti


def test_il_congresso_e_NON_APPLICABILE_sui_nomi_col_suffisso():
    """`scan_ticker` salta il Congresso sui ticker col punto. Non e' un guasto e
    non e' una misura: e' una non applicabilita', e va detta come tale.
    ⚠️ La guardia `"." not in ticker` NON si tocca: e' della stessa famiglia di
    quella che il 22/08 ha aperto BA.L -> il CIK di Boeing."""
    esiti = {}
    se.scan_ticker("ALFA.MI", esiti=esiti)
    assert esiti.get("cluster del Congresso", "").startswith("NON APPLICABILE"), esiti


def test_un_nome_con_rilevatori_muti_non_finisce_in_scansione_piena(monkeypatch):
    """IL DIFETTO ALTA, riprodotto con il modo VERO in cui i rilevatori cadono."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors
    from bellomberg.portfolio import vol_surface
    from bellomberg.portfolio import positioning_tools
    from bellomberg.market_data import quiver_data

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": "NOVX", "valore_mercato": 3700.0, "valuta": "EUR"},
            {"ticker": "ALFA.MI", "valore_mercato": 4500.0, "valuta": "EUR"}]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    monkeypatch.setattr(vol_surface, "build_vol_surface",
                        lambda *a, **k: {"error": "POLYGON_API_KEY mancante o non attiva"})
    monkeypatch.setattr(positioning_tools, "compute_gex",
                        lambda *a, **k: {"error": "POLYGON_API_KEY mancante o non attiva"})
    monkeypatch.setattr(quiver_data, "quiver_available", lambda: True)
    monkeypatch.setattr(quiver_data, "get_congress_trades",
                        lambda *a, **k: {"error": "HTTP 429"})

    def _z(tk, esiti=None, *a, **k):
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "sig_price_zscore", _z)

    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert "NOVX" not in cop["scansione_piena"], (
        "dichiara copertura piena su un nome i cui rilevatori sono muti: %s" % cop)
    degradata = cop.get("scansione_degradata") or {}
    assert "NOVX" in degradata, "il degrado non e' dichiarato: %s" % cop
    assert "POLYGON_API_KEY" in degradata["NOVX"], degradata["NOVX"]
    assert "429" in degradata["NOVX"], degradata["NOVX"]
    assert cop["solo_prezzo"] == ["ALFA.MI"]
    assert "NOVX" in cop["nota"], "la nota non nomina il nome degradato"


def test_se_nessun_rilevatore_risponde_il_nome_e_senza_misura(monkeypatch):
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": "ALFA.MI", "valore_mercato": 4500.0, "valuta": "EUR"}]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})

    def _z(tk, esiti=None, *a, **k):
        if esiti is not None:
            esiti["z-score di prezzo"] = "MUTO: yfinance HTTP 429"
        return []

    monkeypatch.setattr(se, "sig_price_zscore", _z)
    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert cop["solo_prezzo"] == []
    assert "ALFA.MI" in (cop.get("nessuna_misura") or {})
    assert "429" in cop["nessuna_misura"]["ALFA.MI"]


def test_i_conti_della_copertura_tornano_sempre(monkeypatch):
    """Invariante: i cinque secchi coprono il libro, senza sovrapposizioni."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
            for t, v in LIBRO_VERO]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})

    def _st(tk, esiti=None, **k):
        if esiti is not None:
            esiti["vol risk premium"] = "interrogato"
            esiti["dealer gamma"] = "MUTO: finto"
            esiti["z-score di prezzo"] = "interrogato"
            esiti["cluster del Congresso"] = "interrogato"
        return []

    def _z(tk, esiti=None, *a, **k):
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "scan_ticker", _st)
    monkeypatch.setattr(se, "sig_price_zscore", _z)
    cop = se.scan_portfolio(min_strength=45, max_tickers=22)["copertura"]
    secchi = (list(cop["scansione_piena"]) + list(cop["scansione_degradata"])
              + list(cop["solo_prezzo"]) + list(cop["nessuna_misura"])
              + list(cop["non_scansionate"]))
    assert len(secchi) == len(set(secchi)) == cop["posizioni_totali"] == len(LIBRO_VERO)
    assert cop["posizioni_totali"] == 25 and len(cop["non_scansionate"]) == 3


def test_i_rami_error_dei_fattori_sono_dichiarati_non_solo_le_eccezioni(monkeypatch):
    """`compute_portfolio_factors` torna `{"error": ...}` in SEI rami invece di
    sollevare: il ramo `except` non li vedeva e la nota scriveva «0 nomi»."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": "NOVX", "valore_mercato": 1.0, "valuta": "EUR"}]}})())
    monkeypatch.setattr(se, "scan_ticker", lambda tk, esiti=None, **k: [])
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"error": "FF download failed per tutte le regioni"})
    cop = se.scan_portfolio(min_strength=45)["copertura"]
    assert cop["fattoriali_su"] is None, (
        "0 si legge come una misura: %r" % cop["fattoriali_su"])
    assert "FF download failed" in cop["nota"]


def test_il_messaggio_di_guasto_non_puo_mangiare_il_payload():
    """`fattoriali_ko` era l'unico campo non limitato, sta PRIMA dei segnali e
    non e' in nessuna leva di dimagrimento: un `{e}` da qualche KB spingeva
    fuori i dati per salvare una stringa d'errore."""
    lungo = "X" * 5000
    corto = se._motivo_corto(lungo)
    assert len(corto) < 400
    assert "non riportati" in corto
    assert se._motivo_corto("corto") == "corto"


def test_il_libro_vuoto_non_e_uno_zero_misurato(monkeypatch):
    """CLAUDE.md, LEZIONI: «un DB 0 posizioni = path sbagliato, non dati persi»."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": []}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    out = se.scan_portfolio(min_strength=45)
    assert out.get("error"), "un libro vuoto esce come uno zero misurato: %s" % out
    assert "nessuna posizione" in out["error"].lower()


def test_la_nota_non_dice_piu_che_i_nomi_eu_ricevono_una_misura_sola(scanner_senza_rete):
    """Falsa: le esposizioni fattoriali coprono anche loro, e la nota stessa lo
    diceva due frasi dopo. La distinzione vera e' «rilevatori PER-TICKER»."""
    nota = se.scan_portfolio(min_strength=45)["copertura"]["nota"]
    assert "per-ticker" in nota.lower(), nota
    assert not re.search(r"ne ricevono UNO — solo lo z-score", nota), nota


# ---------- il Capo ----------

def test_lo_zero_del_capo_e_una_misura_SOLO_se_i_rilevatori_hanno_risposto():
    """La frase «questo zero E' una misura» scattava proprio nello stato in cui
    e' falsa: un blackout dei dati da' `signals: []` SENZA `error`."""
    from bellomberg.agents import capo
    sano = {"signals": [], "copertura": {"nota": "n.", "scansione_degradata": {},
                                         "nessuna_misura": {}, "fattoriali_ko": None,
                                         "posizioni_scansionate": 25}}
    t = "\n".join(capo._blocco_edge_scan(sano))
    assert "E' una misura" in t, t

    guasto = {"signals": [], "copertura": {
        "nota": "n.", "scansione_degradata": {"NOVX": "vol risk premium MUTO"},
        "nessuna_misura": {}, "fattoriali_ko": None, "posizioni_scansionate": 25}}
    t2 = "\n".join(capo._blocco_edge_scan(guasto))
    assert "questo zero E' una misura" not in t2, (
        "afferma che lo zero e' una misura mentre i rilevatori erano muti:\n" + t2)
    assert "NOVX" in t2, t2


def test_il_capo_non_riceve_vie_di_recupero_che_non_puo_percorrere():
    """`grep -c "tools=" capo.py` -> 0: il Capo NON ha tool. Indicargli
    `get_edge_scan`/`get_position_doctor` e' la lezione del 21/08 (il red team e
    `ask_specialist`) ripetuta il giorno dopo."""
    from bellomberg.agents import capo
    scan = {"signals": [
        se._sig("TCK%02d" % i, "momentum", "S", 1.0, "c", "neutral", 99 - i, "l", "x")
        for i in range(23)],
        "copertura": {"nota": "Scansionate 25 su 25.",
                      "recupero": "get_position_doctor(ticker) su un singolo nome"}}
    t = "\n".join(capo._blocco_edge_scan(scan))
    assert "get_edge_scan" not in t, "promette un tool che il Capo non ha:\n" + t
    assert "get_position_doctor" not in t, "idem:\n" + t


def test_il_capo_vede_i_NOMI_dei_segnali_che_non_entrano():
    """«altri 9 segnali» senza sapere QUALI non e' un caveat applicabile a
    nessuna riga della action table."""
    from bellomberg.agents import capo
    scan = {"signals": [
        se._sig("TCK%02d" % i, "momentum", "S", 1.0, "c", "neutral", 99 - i, "l", "x")
        for i in range(17)]}
    t = "\n".join(capo._blocco_edge_scan(scan, tetto=14))
    for tk in ("TCK14", "TCK15", "TCK16"):
        assert tk in t, "il nome escluso %s non arriva al Capo:\n%s" % (tk, t)


def test_il_capo_riceve_i_nomi_coperti_da_un_solo_rilevatore():
    from bellomberg.agents import capo
    scan = {"signals": [se._sig("NOVX", "m", "S", 1.0, "c", "neutral", 80, "l", "x")],
            "copertura": {"nota": "Scansionate 3 posizioni su 3.",
                          "solo_prezzo": ["ALFA.MI", "GAMM.MI"],
                          "scansione_piena": ["NOVX"]}}
    t = "\n".join(capo._blocco_edge_scan(scan))
    assert "ALFA.MI" in t and "GAMM.MI" in t, "i nomi non arrivano al Capo:\n" + t


def test_il_capo_dichiara_la_soglia_con_cui_lo_scanner_e_stato_chiamato():
    """Il Capo chiama con min_strength=50, il tool e l'endpoint con 45: senza il
    numero, «altri N segnali» si legge come il resto totale, e non lo e'."""
    from bellomberg.agents import capo
    scan = {"signals": [se._sig("T%d" % i, "m", "S", 1.0, "c", "neutral", 99 - i,
                                "l", "x") for i in range(20)]}
    t = "\n".join(capo._blocco_edge_scan(scan, soglia=50))
    assert "50" in t, "la soglia non e' dichiarata:\n" + t


def test_il_tetto_del_capo_non_puo_essere_zero_o_negativo():
    from bellomberg.agents import capo
    scan = {"signals": [se._sig("T%d" % i, "m", "S", 1.0, "c", "neutral", 99 - i,
                                "l", "x") for i in range(5)]}
    for t in (0, -3):
        righe = capo._blocco_edge_scan(scan, tetto=t)
        assert capo._n_segnali_iniettati(righe) >= 1, (
            "tetto=%r produce un blocco senza nemmeno un segnale" % t)


def test_la_riga_di_log_non_chiama_misurati_i_gia_filtrati():
    """`signals` e' gia' filtrato a min_strength: i MISURATI sono
    `n_signals_total`. Il fix aveva corretto il primo numero e lasciato falso
    il secondo."""
    righe = _sorgente("src/bellomberg/agents/capo.py").splitlines()
    n = next(i for i, r in enumerate(righe) if "Edge scan iniettato" in r)
    statement = "\n".join(righe[n:n + 3])
    assert "n_signals_total" in statement, (
        "il secondo numero non e' quello dei misurati:\n" + statement)


# ---------- la vista compatta ----------

def test_la_vista_non_promette_un_recupero_che_non_esiste():
    """I segnali tolti sono i PIU' DEBOLI: nessun valore di min_strength li fa
    riapparire (alzarlo li esclude, abbassarlo li fa ritagliare). Il test
    precedente lo lasciava passare per via di un `or`."""
    import bellomberg.agents.chat_tools as ct
    out = ct._compatta_edge_scan(_payload_finto(300, corpo=40))
    v = out["_vista"]
    assert "get_position_doctor" in v, v
    assert not re.search(r"alzando min_strength|min_strength per restringere", v), (
        "promette un recupero impossibile: %r" % v)


def test_la_vista_nomina_i_segnali_che_toglie():
    """Senza i nomi, chi legge non sa nemmeno su cosa chiamare position_doctor."""
    import bellomberg.agents.chat_tools as ct
    out = ct._compatta_edge_scan(_payload_finto(300, corpo=40))
    assert re.search(r"TCK\d\d", out["_vista"]), (
        "i segnali tolti non sono nominati: %r" % out["_vista"])


# ============================================================
# 8. IL CABLAGGIO — il buco piu' grave, trovato dalla review 22/08
# ============================================================

def test_run_capo_inietta_davvero_il_blocco_nel_prompt():
    """🔴 CRITICO trovato dalla review: cancellando `user_msg_parts += righe_edge`
    (capo.py, ramo felice) TUTTI i 27 test restavano verdi e il Capo tornava a
    non vedere nessun EDGE SCAN — il difetto originale, integrale. Testavo
    l'helper e mai il cablaggio. Qui si guarda il RAMO FELICE, non solo l'except.
    """
    righe = _sorgente("src/bellomberg/agents/capo.py").splitlines()
    # ⚠️ L'ancora della prima stesura era `_blocco_edge_scan(scan`, che matcha
    # anche la riga `def _blocco_edge_scan(scan, ...)`: la finestra partiva dalla
    # DEFINIZIONE e si mangiava tutta la funzione piu' mezzo `run_capo`, dove
    # `user_msg_parts` compare comunque. La mutazione che cancella il cablaggio
    # e' sopravvissuta proprio per questo. Stesso errore della finestra a 400
    # char: si ancora sull'ASSEGNAZIONE, che e' unica.
    n = next(i for i, r in enumerate(righe) if "righe_edge = _blocco_edge_scan(" in r)
    fine = next(i for i in range(n, len(righe)) if "Edge scan iniettato" in righe[i])
    blocco = "\n".join(righe[n:fine + 1])
    assert re.search(r"user_msg_parts\s*\+?=\s*righe_edge", blocco), (
        "il blocco EDGE SCAN non viene mai aggiunto al prompt del Capo:\n" + blocco)
    # ...e la soglia con cui lo scanner e' stato chiamato dev'essere quella che
    # il blocco dichiara: passarla e' cablaggio, non cosmesi.
    assert re.search(r"_blocco_edge_scan\(scan,\s*soglia\s*=", blocco), (
        "run_capo non passa la soglia al blocco: il Capo legge «sopra la soglia» "
        "senza sapere quale:\n" + blocco)
    m = re.search(r"scan_portfolio\(min_strength=(\d+)\)",
                  _sorgente("src/bellomberg/agents/capo.py"))
    assert m, "run_capo non chiama piu' scan_portfolio con una soglia esplicita"
    assert re.search(r"soglia\s*=\s*" + m.group(1), blocco), (
        "la soglia dichiarata nel prompt non e' quella con cui lo scanner e' "
        "stato chiamato (%s)" % m.group(1))




def test_la_nota_elenca_i_nomi_coperti_da_un_solo_rilevatore(scanner_senza_rete):
    """Senza i NOMI il caveat non e' applicabile a nessuna riga della action
    table. (Mutazione sopravvissuta al 2° giro: sostituire l'elenco con "...")"""
    nota = se.scan_portfolio(min_strength=45)["copertura"]["nota"]
    for tk in ("ALFA.MI", "DELT.MI", "EPSI.MI"):
        assert tk in nota, "il nome %s non e' nella nota:\n%s" % (tk, nota)


def test_un_payload_di_errore_passa_INTATTO_anche_se_e_enorme():
    """Non si compatta un guasto: togliere le letture a un payload d'errore non
    ha senso, e il motivo e' l'unica cosa che c'e'. (Mutazione sopravvissuta:
    togliere `_e_errore(r) or` non cambiava nulla su un errore CORTO.)"""
    import bellomberg.agents.chat_tools as ct
    grosso = {"error": "X" * (ct.TETTO_TOOL_RESULT + 5000), "_timestamp": "t"}
    assert ct._peso_json(grosso) > ct.TETTO_TOOL_RESULT
    out = ct._compatta_edge_scan(grosso)
    assert out is grosso, "un payload d'errore e' stato degradato invece di passare"
    assert "_vista" not in out


# ============================================================
# 7. LA CACHE IN-PROCESS (22/08 sera — decisione (a) del PM, §9-quinquadragies)
# ============================================================
# I numeri, RIMISURATI dalla review 22/08 sera su `data/consigliere_run.log`:
# una run fa 3-4 scansioni nello stesso processo (2-3 dal desk quant + 1
# diretta del Capo; le «21 chiamate» di MASTER §9-quinquadragies sono il
# totale del log su NOVE run) a 165,4s caldo / 381,4s freddo l'una. La cache
# collassa le 3-4 scansioni di una run in UNA (da ~8-25 min a 2,8-6,4 min,
# secondo caldo/freddo) e i click di soglia di F13 in hit — ma i processi
# sono DUE (il consigliere e' un subprocess): la run non scalda la cache
# dell'API, e la prima apertura di F13 a backend freddo paga la scansione
# intera. Ogni risposta servita da cache DICHIARA la propria eta' (regola PM
# 14/07: un dato riscaldato non si spaccia per fresco) — nei campi del
# payload, nella nota per il Capo.


def _interrogazioni(visti):
    """Quante volte i rilevatori sono stati chiamati (un append per ticker)."""
    return len(visti["piena"]) + len(visti["solo_prezzo"])


def test_la_seconda_chiamata_non_reinterroga_i_rilevatori(scanner_senza_rete):
    out1 = se.scan_portfolio(min_strength=45)
    prima = _interrogazioni(scanner_senza_rete)
    assert prima == len(LIBRO_VERO)
    out2 = se.scan_portfolio(min_strength=45)
    assert _interrogazioni(scanner_senza_rete) == prima, (
        "la seconda chiamata ha ri-interrogato i rilevatori: la cache non c'e' "
        "o non serve")
    assert out2["signals"] == out1["signals"]


def test_la_risposta_da_cache_dichiara_la_propria_eta(scanner_senza_rete):
    """La dichiarazione va sia nei campi (per F13) sia nella nota di copertura
    (che e' l'unica cosa del payload che arriva al prompt del Capo)."""
    out1 = se.scan_portfolio(min_strength=45)
    assert out1["cache"]["servita_da_cache"] is False
    assert "non sono stati re-interrogati" not in out1["copertura"]["nota"], (
        "la frase di cache compare anche su una scansione fresca: e' falsa")
    out2 = se.scan_portfolio(min_strength=45)
    c = out2["cache"]
    assert c["servita_da_cache"] is True
    assert isinstance(c["eta_s"], int) and c["eta_s"] >= 0
    assert c["ttl_s"] == se.SCAN_CACHE_TTL_SEC
    assert "non sono stati re-interrogati" in out2["copertura"]["nota"], (
        "la nota non dice a chi legge che la scansione non e' di adesso")


def test_soglie_diverse_condividono_la_stessa_scansione(scanner_senza_rete):
    """`min_strength` e' un filtro A VALLE: il Capo (50), il tool (45) e i 4
    bottoni di soglia di F13 devono condividere la scansione, non rifarla."""
    out45 = se.scan_portfolio(min_strength=45)
    prima = _interrogazioni(scanner_senza_rete)
    # cintura (review 22/08 sera): senza, con una cache gia' sporca il test
    # diventerebbe `0 == 0` — verde senza aver mai osservato una scansione.
    assert prima == len(LIBRO_VERO)
    out80 = se.scan_portfolio(min_strength=80)
    assert _interrogazioni(scanner_senza_rete) == prima, (
        "cambiare la soglia ha rifatto la scansione: il filtro non e' a valle "
        "della cache")
    assert out80["n_signals_total"] == out45["n_signals_total"]
    assert out80["signals"] and all(s["strength"] >= 80 for s in out80["signals"])
    assert len(out80["signals"]) < len(out45["signals"])


def test_force_rifa_la_scansione(scanner_senza_rete):
    """La leva per un refresh vero resta: `force=True` re-interroga."""
    se.scan_portfolio(min_strength=45)
    prima = _interrogazioni(scanner_senza_rete)
    out = se.scan_portfolio(min_strength=45, force=True)
    assert _interrogazioni(scanner_senza_rete) == 2 * prima
    assert out["cache"]["servita_da_cache"] is False


def test_la_cache_scade_col_ttl(scanner_senza_rete):
    """Una scansione piu' vecchia del TTL non si serve: si rifa'. (Il test
    invecchia l'entry a mano invece di aspettare un'ora.)"""
    se.scan_portfolio(min_strength=45)
    prima = _interrogazioni(scanner_senza_rete)
    for entry in se._SCAN_CACHE.values():
        entry["ts"] -= se.SCAN_CACHE_TTL_SEC + 1
    out = se.scan_portfolio(min_strength=45)
    assert _interrogazioni(scanner_senza_rete) == 2 * prima, (
        "una scansione oltre il TTL e' stata servita lo stesso")
    assert out["cache"]["servita_da_cache"] is False


def test_un_cambio_del_libro_invalida_la_cache(scanner_senza_rete, monkeypatch):
    """La chiave e' il libro: una posizione nuova non puo' essere servita da
    una scansione che non l'ha mai vista."""
    from bellomberg.storage import memory_db
    se.scan_portfolio(min_strength=45)

    class _DBCambiato:
        def get_portfolio_summary(self):
            return {"positions": [
                {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
                for t, v in LIBRO_VERO
            ] + [{"ticker": "AAPL", "valore_mercato": 1000.0, "valuta": "EUR"}]}

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: _DBCambiato())
    out = se.scan_portfolio(min_strength=45)
    assert out["cache"]["servita_da_cache"] is False
    assert "AAPL" in scanner_senza_rete["piena"], (
        "il nome nuovo non e' stato interrogato: la cache ha servito un libro "
        "che non esiste piu'")


def test_un_tetto_esplicito_scavalca_la_cache_e_lo_dichiara(scanner_senza_rete):
    """`max_tickers` esplicito seleziona in base all'ORDINE del libro: servirgli
    la scansione piena dalla cache sarebbe rispondere a un'altra domanda."""
    se.scan_portfolio(min_strength=45)
    out = se.scan_portfolio(min_strength=45, max_tickers=5)
    assert out["cache"]["attiva"] is False
    # il «lo dichiara» del nome va asserito (review 22/08 sera: cancellare il
    # motivo lasciava tutto verde)
    assert "max_tickers esplicito" in out["cache"]["motivo"]
    assert out["copertura"]["posizioni_scansionate"] == 5


def test_un_errore_non_finisce_in_cache(monkeypatch):
    """Un payload `{"error"}` non deve restare in cache: il pattern di casa
    (benchmark_series) mette in cache SOLO i successi.

    LIMITE DICHIARATO (review 22/08 sera): questo e' un PIN STRUTTURALE, non
    un comportamento falsificabile dal banco — l'error-return sta PRIMA del
    blocco cache per costruzione, e nessuna mutazione testuale semplice lo
    sposta dopo (verificato: nessuna delle 10 mutazioni CACHE lo fa cadere).
    Passava verde anche prima dell'implementazione. Protegge dai refactor che
    spostano il ramo, non da altro. E NON copre i guasti dei PROVIDER: una
    scansione DEGRADATA (payload non-error) entra in cache e resta per il TTL,
    dichiarata — voce a registro se serve un TTL corto sui degradati."""
    from bellomberg.storage import memory_db

    class _DBRotto:
        def get_portfolio_summary(self):
            raise RuntimeError("DB lockato")

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: _DBRotto())
    out1 = se.scan_portfolio(min_strength=45)
    assert out1.get("error")

    class _DBGuarito:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "NOVX", "valore_mercato": 1.0,
                                   "valuta": "EUR"}]}

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: _DBGuarito())
    chiamate = []

    def _spia(tk, esiti=None, **k):
        chiamate.append(tk)
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "scan_ticker", _spia)
    from bellomberg.portfolio import portfolio_factors
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    out2 = se.scan_portfolio(min_strength=45)
    assert not out2.get("error"), "l'errore precedente e' rimasto in cache"
    assert chiamate == ["NOVX"]


def test_chi_muta_la_risposta_non_avvelena_la_cache(scanner_senza_rete):
    """La cache serve COPIE. `_compatta_edge_scan` oggi copia i suoi dict, ma
    basterebbe un `out["signals"].pop()` in un chiamante qualsiasi per
    corrompere ogni risposta successiva fino allo scadere del TTL."""
    out1 = se.scan_portfolio(min_strength=45)
    out1["signals"][0]["reading"] = "AVVELENATO"
    out1["copertura"]["scansione_piena"].append("FANTASMA")
    out2 = se.scan_portfolio(min_strength=45)
    assert out2["cache"]["servita_da_cache"] is True
    assert all(s.get("reading") != "AVVELENATO" for s in out2["signals"])
    assert "FANTASMA" not in out2["copertura"]["scansione_piena"]
    out2["signals"][0]["reading"] = "AVVELENATO-2"
    out3 = se.scan_portfolio(min_strength=45)
    assert all(s.get("reading") != "AVVELENATO-2" for s in out3["signals"])


def test_due_chiamate_concorrenti_fanno_una_scansione_sola(scanner_senza_rete, monkeypatch):
    """Il caso vero: F13 apre mentre il desk quant sta scansionando. Senza
    serializzazione partirebbero DUE scansioni da 165-381s; la seconda chiamata
    deve aspettare la prima e uscire dalla cache."""
    import threading
    import time as _t
    spia_vera = se.scan_ticker

    def _lenta(tk, esiti=None, **k):
        _t.sleep(0.05)
        return spia_vera(tk, esiti=esiti, **k)

    monkeypatch.setattr(se, "scan_ticker", _lenta)
    risposte = []

    def _chiama():
        risposte.append(se.scan_portfolio(min_strength=45))

    t1 = threading.Thread(target=_chiama)
    t2 = threading.Thread(target=_chiama)
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)
    assert len(risposte) == 2
    assert all(not r.get("error") for r in risposte)
    assert _interrogazioni(scanner_senza_rete) == len(LIBRO_VERO), (
        "due chiamate concorrenti hanno fatto due scansioni (%d interrogazioni "
        "su %d attese)" % (_interrogazioni(scanner_senza_rete), len(LIBRO_VERO)))


def test_l_endpoint_espone_force_e_lo_passa_allo_scanner():
    """F13 deve avere una leva per il refresh vero: parametro `force` additivo
    sull'endpoint (fa parte della decisione (a); la (c) e' stata DECISA il
    25/08 — S3, 503 col motivo: v. sezione 8 di questo file)."""
    src = _sorgente("src/bellomberg/api/bellomberg_api.py")
    i = src.find("def get_edge_scan_endpoint")
    assert i > 0, "ancora non trovata: def get_edge_scan_endpoint"
    j = src.find("def get_position_doctor_endpoint", i)
    ramo = src[i:j if j > 0 else len(src)]
    assert "force: bool = False" in ramo, "l'endpoint non espone `force`"
    assert "scan_portfolio(min_strength=min_strength, force=force)" in ramo, (
        "l'endpoint non passa `force` allo scanner")


def test_una_scansione_degradata_ha_un_ttl_corto_e_lo_dichiara(monkeypatch):
    """Decisione (g) del PM (22/08 sera-2): un blackout di un minuto dei
    provider non deve restare in cache un'ora. Una scansione con rilevatori
    MUTI (o fattoriali KO) si conserva per SCAN_CACHE_TTL_DEGRADATA_SEC, il
    payload lo dice (ttl_s + ttl_motivo), e allo scadere si RIFA' mentre una
    scansione pulita con la stessa eta' verrebbe ancora servita."""
    from bellomberg.storage import memory_db
    from bellomberg.portfolio import portfolio_factors

    monkeypatch.setattr(memory_db, "MemoryDB", lambda *a, **k: type(
        "D", (), {"get_portfolio_summary": lambda self: {"positions": [
            {"ticker": t, "valore_mercato": v, "valuta": "EUR"}
            for t, v in LIBRO_VERO]}})())
    monkeypatch.setattr(portfolio_factors, "compute_portfolio_factors",
                        lambda *a, **k: {"per_holding": {}})
    chiamate = []

    def _scan_muto(tk, esiti=None, **k):
        chiamate.append(tk)
        if esiti is not None:
            # la forma VERA del guasto: la fonte torna {"error"} e il
            # rilevatore deposita MUTO, il prezzo risponde
            esiti["vol risk premium"] = ("MUTO: Polygon 429" if tk in ("KEPL", "ORYN")
                                         else "interrogato")
            esiti["z-score di prezzo"] = "interrogato"
        return []

    def _z(tk, esiti=None, *a, **k):
        chiamate.append(tk)
        if esiti is not None:
            esiti["z-score di prezzo"] = "interrogato"
        return []

    monkeypatch.setattr(se, "scan_ticker", _scan_muto)
    monkeypatch.setattr(se, "sig_price_zscore", _z)

    out1 = se.scan_portfolio(min_strength=45)
    assert out1["copertura"]["scansione_degradata"], "premessa: la scansione e' degradata"
    assert out1["cache"]["ttl_s"] == se.SCAN_CACHE_TTL_DEGRADATA_SEC
    assert se.SCAN_CACHE_TTL_DEGRADATA_SEC < se.SCAN_CACHE_TTL_SEC
    assert "degradata" in out1["cache"]["ttl_motivo"].lower()
    n_prima = len(chiamate)

    out2 = se.scan_portfolio(min_strength=45)
    assert out2["cache"]["servita_da_cache"] is True
    assert out2["cache"]["ttl_s"] == se.SCAN_CACHE_TTL_DEGRADATA_SEC
    assert len(chiamate) == n_prima

    # oltre il TTL corto ma ben sotto quello lungo: si rifa'
    for entry in se._SCAN_CACHE.values():
        entry["ts"] -= se.SCAN_CACHE_TTL_DEGRADATA_SEC + 1
    out3 = se.scan_portfolio(min_strength=45)
    assert out3["cache"]["servita_da_cache"] is False, (
        "una scansione degradata e' stata servita oltre il suo TTL corto")
    assert len(chiamate) == 2 * n_prima


# =============================================================================
# 8. LA (c) E' DECISA: IL GUASTO RISPONDE 503 COL MOTIVO (S3, PM 25/08 — F43)
#    Prima: HTTP 200 col corpo {"error"} — F13 rendeva «Nessun segnale sopra
#    la soglia» su un DB lockato (audit/23, ALTO). Misura PRE 25/08: il ramo
#    error usciva come dict, nessuna eccezione HTTP. Il ramo {"error"} di
#    scan_portfolio RESTA per i chiamanti non-HTTP (tool degli agenti, Capo):
#    l'endpoint TRADUCE, non cambia il contratto interno.
# =============================================================================

def _endpoint(monkeypatch, risposta):
    import bellomberg.api.bellomberg_api as api
    if callable(risposta):
        monkeypatch.setattr(se, "scan_portfolio", risposta)
    else:
        monkeypatch.setattr(se, "scan_portfolio", lambda **k: risposta)
    return api


def test_guasto_scanner_risponde_503_col_motivo_verbatim(monkeypatch):
    """Fail-closed come la cassa (changelog (70)): il motivo VERO nel detail,
    mai un testo generico — e' la riga che F13 rende verbatim al PM."""
    from fastapi import HTTPException
    api = _endpoint(monkeypatch, {"error": "portfolio fetch: DB locked (finto)",
                                  "_timestamp": "2026-08-25T12:00:00"})
    with pytest.raises(HTTPException) as ei:
        api.get_edge_scan_endpoint(min_strength=50)
    assert ei.value.status_code == 503
    assert "portfolio fetch: DB locked (finto)" in str(ei.value.detail)
    # l'ora del guasto viaggia nel detail: il frontend la mostra (F42 punto b)
    assert "2026-08-25T12:00:00" in str(ei.value.detail)


def test_libro_vuoto_e_un_503_non_uno_zero(monkeypatch):
    """L'altro ramo error di scan_portfolio (libro vuoto/illeggibile = DB
    fantasma, lezione storica): anche lui NON deve travestirsi da misura.
    Forma VERA del ramo: in produzione porta SEMPRE `_timestamp` (review
    25/08 — la prima stesura lo ometteva: input approssimato, non vero)."""
    from fastapi import HTTPException
    api = _endpoint(monkeypatch, {"error": "nessuna posizione nel libro: il "
                                  "portafoglio e' vuoto o illeggibile, non e' "
                                  "uno zero misurato",
                                  "_timestamp": "2026-08-25T13:00:00"})
    with pytest.raises(HTTPException) as ei:
        api.get_edge_scan_endpoint()
    assert ei.value.status_code == 503
    assert "nessuna posizione nel libro" in str(ei.value.detail)


def test_error_senza_timestamp_non_stampa_None(monkeypatch):
    """Proprieta' trovata NON ancorata dalla review 25/08: se `_timestamp`
    manca (stub, o un ramo error futuro), la parentesi dell'ora deve sparire
    per intero — mai «guasto rilevato alle None». Il codice era gia' giusto:
    questo test lo INCHIODA (la mutazione gemella del banco toglie il
    condizionale). Nato verde, falsificato dal banco."""
    from fastapi import HTTPException
    api = _endpoint(monkeypatch, {"error": "guasto senza ora (finto)"})
    with pytest.raises(HTTPException) as ei:
        api.get_edge_scan_endpoint()
    assert ei.value.status_code == 503
    assert "guasto senza ora (finto)" in str(ei.value.detail)
    assert "None" not in str(ei.value.detail)


def test_payload_sano_passa_intatto(monkeypatch):
    """Il 503 e' SOLO per il guasto: una scansione sana (anche a zero segnali
    misurati) resta un 200 col payload byte-identico."""
    sano = {"signals": [], "copertura": {"posizioni_totali": 3},
            "cache": {"attiva": True}, "generated": "x"}
    api = _endpoint(monkeypatch, sano)
    assert api.get_edge_scan_endpoint(min_strength=45) is sano


def test_eccezione_dello_scanner_resta_un_500(monkeypatch):
    """Distinzione voluta: {"error"} = guasto DICHIARATO dallo scanner → 503
    (servizio non disponibile); eccezione non gestita = bug → 500 via _err500
    (autopsia nel log). Due stati, due codici."""
    from fastapi import HTTPException

    def _esplode(**k):
        raise RuntimeError("bug non gestito (finto)")

    api = _endpoint(monkeypatch, _esplode)
    with pytest.raises(HTTPException) as ei:
        api.get_edge_scan_endpoint()
    assert ei.value.status_code == 500
    assert "RuntimeError" in str(ei.value.detail)
