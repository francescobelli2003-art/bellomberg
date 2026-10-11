# -*- coding: utf-8 -*-
"""Correzione score fundamentals/news (ordine PM 09/10 "correggi tutto", Opus 5.5).

Si asserisce la LOGICA, non che esca un numero:
- fundamentals: MOS sul prezzo CORRENTE, media PONDERATA sul peso su tutti i nomi, un solo
  asse con zona neutra +/-15%, troncamento +/-50% dichiarato, copertura in % del book,
  veicoli (anche ETF che il negozio non conosce) dichiarati e fuori dal DCF;
- news: per nome, dedup URL/titolo, finestra 7 giorni, fonti mute = n.d. (mai "calmo"),
  riga n.d. fuori dal massimo, verdetto che dichiara "volume, non tono";
- blocco al desk: mai "0 punti (ok)" su un buco, niente punti per-nome che non si sommano;
- R2: score fundamentals ricalcolato sulle valutazioni della run;
- PDF: cruscotto rotto dichiarato, verdetto a capo misurato col font vero.

Isolamento: normalize_valuation_payload e quote_comparison_text sono stub (si prova
l'aritmetica dello score). `_mos_corrente` NON e' stubbato in questo file salvo che in
`prezzo_corrente_uguale_al_modello`, usato SOLO dagli altri file (veicoli, sha del workbook,
FV invalidi): li' la funzione vera NON gira. La funzione vera e' eseguita qui (quota
osservata, payload bank/NAV, FV canonico diverso dal base, modello non utilizzabile, prezzo
DB, FX storico) e da test_market_quote_adversarial_consumers su un payload del motore vero.
v2 10/10 (Opus 5.5): riserve del revisore (ALTO 1-2, MEDIO 4-7, BASSE).
Ticker e numeri INVENTATI.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bellomberg.agents.specialist_scores as ss
from bellomberg.agents import agent_tools
from bellomberg.valuation import market_quote as mq


def quota_fresca(payload, upside_base_pct=None, giorno=None):
    """Aggiunge a un payload una quotazione osservata OGGI (contratto market_quote/1).

    Senza upside esplicito usa FV base / prezzo del modello: il MOS corrente coincide
    allora con quello al prezzo del modello (prezzo invariato dalla valutazione)."""
    if upside_base_pct is None:
        upside_base_pct = (payload["fair_value_base"] / payload["price"] - 1) * 100
    payload["market_quote"] = {
        "contract": mq.CONTRACT, "status": "ok", "freshness_policy": mq.FRESHNESS_POLICY,
        "observed_local_date": giorno or date.today().isoformat(), "price": 1.0,
        "upside_base_pct": upside_base_pct}
    return payload


def prezzo_corrente_uguale_al_modello(monkeypatch):
    """Per i test il cui oggetto NON e' la quotazione (veicoli/DAT, sha del workbook, FV
    invalidi, copertura): il prezzo corrente si dichiara uguale a quello del modello.

    Non si puo' iniettare una quota finta nei payload veri: normalize_valuation_payload
    riattesta market_quote contro lo snapshot di acquisizione e boccia il modello (misurato
    09/10). La funzione vera e' eseguita da test_market_quote_adversarial_consumers (quota
    acquisita dal motore) e da questo file (aritmetica)."""
    monkeypatch.setattr(ss, "_mos_corrente", lambda v, fv, pos=None: ((fv / v["price"] - 1) * 100, None, None))


@pytest.fixture
def isolato(monkeypatch):
    import bellomberg.valuation.dcf_quality as dq
    import bellomberg.reporting.valuation_quote as vq
    monkeypatch.setattr(dq, "normalize_valuation_payload", lambda v, **k: dict(v))
    monkeypatch.setattr(vq, "quote_comparison_text", lambda v, **k: "quote(sintetica)")
    monkeypatch.setattr(ss.cl, "carica_veicoli",
                        lambda path=None: {"origine": "sintetico", "motivo": None, "veicoli": {}})
    monkeypatch.setattr(ss.cl, "veicoli_per_tipo", lambda tipo, neg=None: {"tickers": []})


def _port(*tw):
    return {"positions": [{"ticker": t, "peso_pct": w} for t, w in tw]}


def _val(**spec):
    """ticker -> (fv, prezzo_modello[, prezzo_corrente[, giorno_quote]])"""
    out = {}
    for t, s in spec.items():
        fv, pr = s[0], s[1]
        cur = s[2] if len(s) > 2 else pr
        out[t] = quota_fresca({"ticker": t, "fair_value": fv, "fair_value_base": fv, "price": pr,
                               "valuation_usability": {"usable": True}},
                              (fv / cur - 1) * 100, s[3] if len(s) > 3 else None)
    return out


def _punteggiate(score):
    return [r for r in score["lines"] if r[2] is not None]


# ---------------------------------------------------------------- fundamentals

def test_il_peso_decide_il_verdetto_book_caro_contro_book_a_sconto(isolato):
    caro = ss.fundamentals_score(_port(("ZZBIG", 60), ("ZZS1", 2), ("ZZS2", 2), ("ZZS3", 2)),
                                 _val(ZZBIG=(60, 100), ZZS1=(160, 100), ZZS2=(160, 100), ZZS3=(160, 100)))
    sconto = ss.fundamentals_score(_port(("ZZBIG", 60), ("ZZS1", 2), ("ZZS2", 2), ("ZZS3", 2)),
                                   _val(ZZBIG=(160, 100), ZZS1=(60, 100), ZZS2=(60, 100), ZZS3=(60, 100)))
    # (60*-40 + 6*+50 troncato) / 66 = -31.8 ; (60*+50 troncato + 6*-40) / 66 = +41.8
    # 10/10: punti continui (45 - MOS) / 40 -> (45 + 31.8) / 40 = 1.92: CARO (MOLTO CARO sotto -45)
    assert caro["metrics"]["mos_book_pct"] == -31.8 and caro["score"] == pytest.approx(1.92)
    assert "BOOK CARO" in caro["verdict"] and "MOLTO CARO" not in caro["verdict"]
    assert sconto["metrics"]["mos_book_pct"] == 41.8 and sconto["score"] == pytest.approx(0.08)
    assert "A SCONTO" in sconto["verdict"]
    assert caro["metrics"]["quota_cara_pct"] == pytest.approx(90.9, abs=0.05)


def test_il_quinto_nome_grosso_entra_nella_media(isolato):
    r = ss.fundamentals_score(
        _port(("ZZA", 31), ("ZZB", 31), ("ZZC", 31), ("ZZD", 31), ("ZZE", 30)),
        _val(ZZA=(130, 100), ZZB=(130, 100), ZZC=(130, 100), ZZD=(130, 100), ZZE=(20, 100)))
    assert r["metrics"]["n_valued"] == 5 and r["metrics"]["copertura_book_pct"] == 100.0
    # (124*30 + 30*-50) / 154 = 14.4: dentro la zona neutra +-15 -> EQUA; 10/10 punti continui
    # (45 - 14.4) / 40 = 0.765
    assert r["metrics"]["mos_book_pct"] == 14.4 and r["score"] == pytest.approx(0.765, abs=0.01)
    assert "VALUTAZIONE EQUA" in r["verdict"]
    assert "5/5 nomi valutati, 100% del peso valutabile" in r["verdict"]


def test_un_outlier_vale_al_massimo_quanto_un_nome_troncato(isolato):
    port = _port(("ZZA", 25), ("ZZB", 25), ("ZZC", 25), ("ZZD", 25))
    outlier = ss.fundamentals_score(port, _val(ZZA=(1000, 100), ZZB=(80, 100), ZZC=(80, 100), ZZD=(80, 100)))
    al_tetto = ss.fundamentals_score(port, _val(ZZA=(150, 100), ZZB=(80, 100), ZZC=(80, 100), ZZD=(80, 100)))
    assert outlier["metrics"]["mos_book_pct"] == al_tetto["metrics"]["mos_book_pct"] == -2.5
    assert outlier["score"] == al_tetto["score"] == pytest.approx(1.19, abs=0.01)   # (45 + 2.5) / 40
    assert outlier["metrics"]["n_troncati"] == 1
    assert any("+900.0%" in str(r[1]) for r in outlier["lines"] if "troncati" in str(r[0]))


def test_un_solo_asse_punti_continui(isolato):
    # 10/10: punti continui; -6% vale (45 + 6) / 40 = 1.275, +4% vale 1.025: entrambi EQUA
    port = _port(("ZZA", 50), ("ZZB", 50))
    meno6 = ss.fundamentals_score(port, _val(ZZA=(94, 100), ZZB=(94, 100)))
    piu4 = ss.fundamentals_score(port, _val(ZZA=(104, 100), ZZB=(104, 100)))
    assert meno6["score"] == pytest.approx(1.275, abs=0.01) and piu4["score"] == pytest.approx(1.025, abs=0.01)
    assert "VALUTAZIONE EQUA" in meno6["verdict"] and "VALUTAZIONE EQUA" in piu4["verdict"]
    assert meno6["max_score"] == 3
    assert len(_punteggiate(meno6)) == 1, "un solo asse: nessun secondo conteggio dei cari"


# 10/10: MOS +45% -> 0 punti ... -75% -> 3 punti, lineare (core/soglie_score); MOS per nome
# troncato a +-50%, quindi oltre -50 il punteggio resta 2.375
@pytest.mark.parametrize("fv,atteso", [(150, 0), (145, 0), (115, 0.75), (105, 1.0), (85, 1.5),
                                       (75, 1.75), (55, 2.25), (40, 2.375)])
def test_punti_continui_fra_le_ancore(isolato, fv, atteso):
    r = ss.fundamentals_score(_port(("ZZA", 100)), _val(ZZA=(fv, 100)))
    assert r["score"] == pytest.approx(atteso), (fv, r["metrics"])


@pytest.mark.parametrize("fv", [115, 85, 70])
def test_niente_scoglio_ai_vecchi_confini(isolato, fv):
    # prima 14,9% e 15,1% (e -15/-30) davano un punto intero di differenza
    a = ss.fundamentals_score(_port(("ZZA", 100)), _val(ZZA=(fv - 0.1, 100)))["score"]
    b = ss.fundamentals_score(_port(("ZZA", 100)), _val(ZZA=(fv + 0.1, 100)))["score"]
    assert abs(a - b) <= 0.03, (fv, a, b)   # 0,2 punti di MOS = 0,01 punti (+ arrotondamenti)


def test_margine_sul_prezzo_corrente_non_su_quello_del_modello(isolato):
    # FV 120, prezzo del modello 100 (+20%), oggi 125: il margine vero e' -4%
    r = ss.fundamentals_score(_port(("ZZA", 100)), _val(ZZA=(120, 100, 125)))
    assert r["metrics"]["mos_book_pct"] == -4.0 and r["score"] == pytest.approx(1.225, abs=0.01)
    riga = next(v for l, v, p in r["lines"] if str(l).strip().startswith("ZZA"))
    assert "-4.0% corrente" in riga and "+20.0% al prezzo del modello" in riga


def test_quotazione_vecchia_e_nd_dichiarato_mai_il_prezzo_del_modello(isolato):
    vecchio = (date.today() - timedelta(days=10)).isoformat()
    # v2 10/10 (ALTO 1b): n.d. DICHIARATO col motivo, non None (il desk deve vederlo)
    nd = ss.fundamentals_score(_port(("ZZA", 100)), _val(ZZA=(150, 100, 100, vecchio)))
    assert nd["score"] is None and nd["max_score"] is None
    assert nd["verdict"].startswith("n.d. - prezzo corrente n.d. su 1/1 nomi"), nd["verdict"]
    r = ss.fundamentals_score(_port(("ZZA", 50), ("ZZB", 50)),
                              _val(ZZA=(150, 100, 100, vecchio), ZZB=(90, 100)))
    assert r["metrics"]["n_valued"] == 1 and r["metrics"]["mos_book_pct"] == -10.0
    assert "prezzo corrente n.d.: ZZA" in r["verdict"]
    assert "FV/prezzo assenti" not in r["verdict"]


def test_etf_non_nel_negozio_e_un_veicolo_non_un_buco_di_dcf(isolato):
    vals = _val(ZZA=(130, 100))
    vals["ZZTEST.ETF"] = {"ok": True, "engine": "etf_passive", "ticker": "ZZTEST.ETF"}
    r = ss.fundamentals_score(_port(("ZZA", 60), ("ZZTEST.ETF", 40)), vals)
    # v2 10/10 (BASSA): n/m e % sullo stesso universo (veicoli fuori da entrambi, a parte)
    assert "1/1 nomi valutati, 100% del peso valutabile" in r["verdict"]
    assert "[veicoli 40.0% del book]" in r["verdict"]
    assert "FV/prezzo assenti" not in r["verdict"]
    riga = next(v for l, v, p in r["lines"] if "Veicoli" in str(l))
    assert "ZZTEST.ETF" in riga and "40.0%" in riga


def test_copertura_dichiarata_in_peso_del_book(isolato):
    r = ss.fundamentals_score(_port(("ZZA", 40), ("ZZB", 30), ("ZZC", 20), ("ZZD", 10)),
                              _val(ZZA=(150, 100)))
    assert r["metrics"]["copertura_book_pct"] == 40.0
    assert "1/4 nomi valutati, 40% del peso valutabile" in r["verdict"]
    buco = next(v for l, v, p in r["lines"] if "Senza modello" in str(l))
    assert buco.startswith("n.d.") and "60.0%" in buco


def test_peso_assente_non_diventa_zero(isolato):
    port = {"positions": [{"ticker": "ZZA", "peso_pct": 50}, {"ticker": "ZZB", "peso_pct": None}]}
    r = ss.fundamentals_score(port, _val(ZZA=(110, 100), ZZB=(40, 100)))
    assert r["metrics"]["n_valued"] == 1 and "peso n.d.: ZZB" in r["verdict"]


# ---------------------------------------------------------------- news

ADESSO = datetime.now(timezone.utc)


FORNITORI = ("GNews (ZZ Wire)", "Yahoo Finance (ZZ Daily)")


def _items(n, tk, stesso=False, giorni_fa=0, data=True, titolo="Company reports results",
           fornitori=FORNITORI):
    """n articoli; il fornitore ruota su `fornitori` (forma di tool_search_news: "GNews (...)")."""
    return [{"titolo": "%s %s %d" % (tk, titolo, 0 if stesso else i), "descrizione": "",
             "fonte": fornitori[i % len(fornitori)],
             "url": "https://zztest.invalid/%s/%d" % (tk, 0 if stesso else i),
             "data": (ADESSO - timedelta(days=giorni_fa, hours=i % 5)).isoformat() if data else ""}
            for i in range(n)]


P6 = _port(*[("ZZT%d" % i, 10 - i) for i in range(6)])


def _fonte(per_nome, cieche=()):
    def cerca(query, max_results=10):
        if query in cieche:
            return {"query": query, "count": 0, "news": [], "copertura": "PARZIALE",
                    "fonti": {"gnews": "SKIP_BUDGET", "yfinance": "live"}, "fonti_mute": ["gnews"]}
        n = per_nome(query)
        return {"query": query, "count": len(n), "news": n, "fonti": {"gnews": "live", "yfinance": "live"}}
    return cerca


def test_stesso_articolo_ripetuto_conta_una_volta(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(45, tk, stesso=True)))
    r = ss.news_score(P6)
    assert all(n["n_7g"] == 1 for n in r["metrics"]["nomi"].values())
    assert r["score"] == 0 and r["metrics"]["n_news_7g"] == 6


def test_articoli_fuori_finestra_non_contano(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(10, tk, giorni_fa=90)))
    r = ss.news_score(P6)
    assert r["metrics"]["n_news_7g"] == 0 and r["score"] == 0


def test_soglia_per_nome_ponderata_sul_peso(monkeypatch):
    # ZZT0+ZZT1 (10+9 su 45 di peso) intensi = 42%: banda 25-50 -> 1 punto
    monkeypatch.setattr(agent_tools, "tool_search_news",
                        _fonte(lambda tk: _items(18 if tk in ("ZZT0", "ZZT1") else 1, tk)))
    r = ss.news_score(P6)
    assert r["metrics"]["quota_intensa_pct"] == 42.2 and r["score"] == 1 and r["max_score"] == 3
    assert "volume, non tono" in r["verdict"]


def test_la_riga_nd_resta_fuori_dal_massimo(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(18, tk)))
    r = ss.news_score(P6)
    assert r["score"] == 3 and r["max_score"] == 3, "ALLERTA ora raggiungibile"
    riga = next(x for x in r["lines"] if x[0] == "Eventi ad alto impatto neg.")
    assert riga[2] is None and riga[0] in r["unscored"]
    # v2 10/10 (riconciliazione col contratto eventdesk v2, revisore: atteso (3,6) ottenuto
    # (None,None)): l'attesa v1 (3,6) sommava il VOLUME news al rischio dell'Event Desk. Il
    # contratto vigente (eventdesk v2) somma solo le `risk_lines` di news, oggi vuote: il
    # volume e' una riga INFORMATIVA esclusa per regola e la riga n.d. resta fuori dal
    # massimo. Con 2 temi geopolitici misurati: 0/6, non 3/6.
    monkeypatch.setattr(ss, "politics_score", lambda *a, **k: {
        "score": 0, "max_score": 6, "lines": [("Prob. coda A", "5%", 0), ("Prob. coda B", "4%", 0)],
        "metrics": {}})
    e = ss.eventdesk_score(P6)
    assert (e["score"], e["max_score"]) == (0, 6)
    assert "NEWS | Eventi ad alto impatto neg." in e["unscored"]
    vol = next(l for l, v, p in e["lines"] if "Volume notizie" in l)
    assert vol in e["excluded"]
    # le righe di contesto (dettaglio per nome) non sono «dato mancante» ne' escluse per regola
    per_nome = next(l for l, v, p in e["lines"] if l.startswith("NEWS |   ZZT0"))
    assert per_nome not in e["unscored"] and per_nome not in e["excluded"]
    riga_blocco = next(r for r in ss.format_score_block(e).splitlines() if "ZZT0 (peso 10.0%)" in r)
    assert "informativa (fuori punteggio)" in riga_blocco, riga_blocco


def test_fonti_mute_sono_nd_mai_flusso_calmo(monkeypatch):
    tutti = {"ZZT%d" % i for i in range(6)}
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(2, tk), cieche=tutti))
    r = ss.news_score(P6)
    assert r["max_score"] == 0 and r["verdict"].startswith("n.d.")
    assert "CALMO" not in r["verdict"]
    # 5 nomi ciechi su 6: il solo nome visto (11% del peso) non decide il book
    monkeypatch.setattr(agent_tools, "tool_search_news",
                        _fonte(lambda tk: _items(2, tk), cieche=tutti - {"ZZT5"}))
    r = ss.news_score(P6)
    assert r["max_score"] == 0 and "CALMO" not in r["verdict"]


def test_flusso_intenso_si_accerta_anche_con_fonti_mute(monkeypatch):
    # thenewsapi muta, gnews + yfinance vivi: 18 distinti da 2 fonti = minimo gia' sopra soglia
    def cerca(query, max_results=10):
        return {"query": query, "count": 18, "news": _items(18, query), "copertura": "PARZIALE",
                "fonti": {"gnews": "live", "thenewsapi": "SKIP_BUDGET", "yfinance": "live"},
                "fonti_mute": ["thenewsapi"]}
    monkeypatch.setattr(agent_tools, "tool_search_news", cerca)
    r = ss.news_score(P6)
    assert r["score"] == 3 and all(n["stato"] == "INTENSO" for n in r["metrics"]["nomi"].values())


def _sonda_e(query, max_results=10):
    """Sonda E del revisore: gnews/marketaux/thenewsapi muti, yfinance vivo col suo tetto 5:
    i primi tre nomi (60% del peso) hanno 5 articoli Yahoo, gli altri 2."""
    n = 5 if query in ("ZE0", "ZE1", "ZE2") else 2
    return {"query": query, "count": n,
            "news": _items(n, query, fornitori=("Yahoo Finance (ZZ)",)), "copertura": "PARZIALE",
            "fonti": {"marketaux": "SKIP_BUDGET", "thenewsapi": "SKIP_BUDGET", "gnews": "SKIP_BUDGET",
                      "yfinance": "live"}, "fonti_mute": ["gnews", "marketaux", "thenewsapi"]}


PE = _port(*[("ZE%d" % i, w) for i, w in enumerate([25, 20, 15, 15, 15, 10])])


def test_sonda_e_fonte_unica_cieca_non_da_allerta(monkeypatch):
    """ALTO 2 + MEDIO 4: prima (da36ffd) quota 100% del MISURATO -> 3/3 «ALLERTA NOTIZIE» con
    tre fonti su quattro mute. Il tetto di yfinance (5) non e' un flusso intenso."""
    monkeypatch.setattr(agent_tools, "tool_search_news", _sonda_e)
    r = ss.news_score(PE)
    assert r["score"] == 0 and r["max_score"] == 0 and r["verdict"].startswith("n.d.")
    assert "ALLERTA" not in r["verdict"]
    assert all(n["stato"] == "n.d." for n in r["metrics"]["nomi"].values())
    monkeypatch.setattr(ss, "politics_score", lambda *a, **k: {
        "score": 0, "max_score": 6, "lines": [("Prob. coda A", "5%", 0), ("Prob. coda B", "4%", 0)],
        "metrics": {}})
    e = ss.eventdesk_score(PE)
    # MEDIO 5: «EVENTI CALMI» descrive la sola geopolitica: lo si dice, e il verde non c'e'
    assert e["verdict"] == "EVENTI CALMI (news n.d.: 3/4 mute)", e["verdict"]
    assert e["componente_nd"] == "news"


def test_una_fonte_sola_non_basta_per_intenso(monkeypatch):
    """MEDIO 4: 20 distinti tutti dalla stessa fonte (ticker-parola che satura GNews)."""
    monkeypatch.setattr(agent_tools, "tool_search_news",
                        _fonte(lambda tk: _items(20, tk, fornitori=("GNews (ZZ)",))))
    r = ss.news_score(P6)
    assert all(n["stato"] == "n.d." for n in r["metrics"]["nomi"].values())
    assert "fonte/i riconosciute" in r["metrics"]["nomi"]["ZZT0"]["motivo"]
    assert r["score"] == 0 and r["max_score"] == 0


@pytest.mark.parametrize("n", [15, 16])
def test_soglia_sopra_il_tetto_della_fonte_piu_larga(monkeypatch, n):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(n, tk)))
    r = ss.news_score(P6)
    stati = {x["stato"] for x in r["metrics"]["nomi"].values()}
    assert stati == ({"CALMO"} if n == 15 else {"INTENSO"})


def _misto(intensi, nd):
    def cerca(query, max_results=10):
        if query in nd:
            return {"query": query, "count": 2, "news": _items(2, query), "copertura": "PARZIALE",
                    "fonti": {"gnews": "SKIP_BUDGET", "yfinance": "live"}, "fonti_mute": ["gnews"]}
        n = 18 if query in intensi else 1
        return {"query": query, "count": n, "news": _items(n, query),
                "fonti": {"gnews": "live", "yfinance": "live"}}
    return cerca


def test_quota_sul_peso_esaminato_non_sul_misurato(monkeypatch):
    """ALTO 2 (uccide M2): ZZT0-2 intensi (27/45 = 60%), ZZT5 n.d. (5/45 = 11%). Sul misurato
    sarebbe 27/40 = 67,5%; sul peso esaminato e' 60%, e il massimo possibile (n.d. intenso)
    71,1% resta nella stessa banda -> 2 punti."""
    monkeypatch.setattr(agent_tools, "tool_search_news", _misto({"ZZT0", "ZZT1", "ZZT2"}, {"ZZT5"}))
    r = ss.news_score(P6)
    assert r["metrics"]["quota_intensa_pct"] == 60.0 and r["metrics"]["quota_intensa_max_pct"] == 71.1
    assert r["score"] == 2 and r["max_score"] == 3


def test_copertura_parziale_con_soli_intensi_non_decide_la_fascia(monkeypatch):
    """ALTO 2: i nomi n.d. potrebbero spostare la banda (60% -> 84%): n.d., non ALLERTA."""
    monkeypatch.setattr(agent_tools, "tool_search_news",
                        _misto({"ZZT0", "ZZT1", "ZZT2"}, {"ZZT4", "ZZT5"}))
    r = ss.news_score(P6)
    assert r["max_score"] == 0 and r["verdict"].startswith("n.d.")
    riga = next(v for l, v, p in r["lines"] if l == "Volume notizie per nome")
    assert "fra 60% e 84%" in riga, riga


def test_articoli_senza_data_non_permettono_di_dire_calmo(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(20, tk, data=False)))
    r = ss.news_score(P6)
    assert r["max_score"] == 0
    assert all(n["stato"] == "n.d." for n in r["metrics"]["nomi"].values())


def test_il_tool_e_chiamato_senza_il_tetto_che_troncava(monkeypatch):
    visti = []
    def cerca(query, max_results=10):
        visti.append(max_results)
        return {"query": query, "count": 0, "news": [], "fonti": {"gnews": "live"}}
    monkeypatch.setattr(agent_tools, "tool_search_news", cerca)
    ss.news_score(P6)
    assert visti and set(visti) == {30}


# ---------------------------------------------------------------- blocco al desk

def test_nessun_buco_stampato_ok_e_nessun_punto_per_nome(isolato, monkeypatch):
    f = ss.fundamentals_score(_port(("ZZA", 40), ("ZZB", 30), ("ZZC", 20), ("ZZD", 10)),
                              _val(ZZA=(99.9, 100)))
    blocco = ss.format_score_block(f)
    for riga in blocco.splitlines():
        if "Senza modello" in riga or riga.strip().startswith("-   ZZA"):
            assert "punti" not in riga, riga
    assert sum(p for l, v, p in f["lines"] if p is not None) == f["score"]
    tutti = {"ZZT%d" % i for i in range(6)}
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(2, tk), cieche=tutti))
    n = ss.news_score(P6)
    bn = ss.format_score_block(n)
    assert "score n.d." in bn and "0/0" not in bn and "(ok)" not in bn
    politica = {"domain": "politics", "score": 0, "max_score": 3, "verdict": "X",
                "lines": [("recessione USA (n.d.)", "nessun mercato valido", 0)]}
    bp = ss.format_score_block(politica)
    assert "(ok)" not in bp and "NON 'ok'" in bp


def test_totale_nd_stampa_comunque_le_righe_dichiarate():
    sc = {"domain": "macro", "score": None, "max_score": None, "verdict": "n.d. - ZZTEST",
          "lines": [("Curva ZZ", "STALE: osservazione 01/08", None),
                    ("Tasso ZZ", "n.d.: fonte muta", None), ("Contesto ZZ", "nota", None)]}
    b = ss.format_score_block(sc)
    assert "Verdetto: n.d. - ZZTEST." in b
    assert "Curva ZZ" in b and "STALE" in b and "Tasso ZZ" in b and "Contesto ZZ" in b
    assert b.count("dato mancante") == 2 and "informativa" in b


def test_news_non_espone_il_volume_come_rischio(monkeypatch):
    monkeypatch.setattr(agent_tools, "tool_search_news", _fonte(lambda tk: _items(18, tk)))
    r = ss.news_score(P6)
    assert r["risk_lines"] == [] and r["score"] == 3


# ---------------------------------------------------------------- R2: ricalcolo

def test_round2_ricalcola_fundamentals_sulle_valutazioni_della_run(monkeypatch):
    from bellomberg.agents.specialists import base
    from bellomberg.valuation import sector_analysis
    board = SimpleNamespace(data={}, memory_db=None, valuation_results={},
                            mark_specialist_start=lambda *a: None,
                            summary_for_specialist=lambda name: {})
    s = base.Specialist(board, client=object())
    s.name = "fundamentals"
    monkeypatch.setattr(base, "blocco_vincoli_pm", lambda db: "")
    monkeypatch.setattr(base, "_blocco_round_precedente", lambda *a: "")
    monkeypatch.setattr(sector_analysis, "valuation_results_block", lambda v: "VALUTAZIONI")
    chiamate = []

    def calcola():
        chiamate.append(dict(board.valuation_results))
        return {"domain": "fundamentals", "score": len(chiamate), "max_score": 3,
                "verdict": "VERDETTO-%d" % len(chiamate), "lines": []}
    monkeypatch.setattr(s, "compute_score", calcola)
    s._build_round_context(0); s._build_round_context(1)
    assert len(chiamate) == 1 and board.data["_score_cache"]["fundamentals"]["verdict"] == "VERDETTO-1"
    board.valuation_results = {"ZZA": {"ticker": "ZZA"}}
    testo = s._build_round_context(2)
    assert len(chiamate) == 2 and chiamate[1] == {"ZZA": {"ticker": "ZZA"}}
    assert board.data["_score_cache"]["fundamentals"]["verdict"] == "VERDETTO-2"
    assert "[SCORE AGGIORNATO]" in testo and "VERDETTO-2" in testo
    # un desk diverso non ricalcola in R2
    s.name = "quant"
    s._build_round_context(2)
    assert len(chiamate) == 2


# ---------------------------------------------------------------- PDF

def _pdf_testo(tmp_path, monkeypatch, scoring):
    import pymupdf   # dipendenza dei test PDF del repo: se manca e' un rosso, non uno skip
    from bellomberg.core.language import language_context
    from bellomberg.reporting import pdf_institutional as pi
    monkeypatch.setattr(pi, "_gen_charts", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(pi, "REPORT_DIR", str(tmp_path))
    out = tmp_path / "memo.pdf"
    with language_context("it"):
        pi.build_institutional_memo("## BLUF\n- ZZTEST sintetico.\n", output_path=str(out),
                                    scoring_data=scoring, title_date="9 ottobre 2026")
    doc = pymupdf.open(str(out))
    spans = [s for p in doc for b in p.get_text("dict")["blocks"] for l in b.get("lines", [])
             for s in l["spans"] if s["text"].strip()]
    return spans, "\n".join(p.get_text() for p in doc)


def test_cruscotto_rotto_dichiarato_nel_pdf(tmp_path, monkeypatch):
    def rotto(cache):
        raise RuntimeError("ZZTEST guasto cruscotto")
    monkeypatch.setattr(ss, "collect_scoreboard", rotto)
    _, testo = _pdf_testo(tmp_path, monkeypatch, {"macro": {"score": 1, "max_score": 3, "verdict": "X"}})
    assert "Cruscotto non disponibile" in testo and "ZZTEST guasto cruscotto" in testo


def _usa_font(monkeypatch, font):
    """'dejavu' = il font che sceglie la CI Linux (ramo DejaVu di _register_fonts), dalla
    copia TTF di matplotlib registrata sotto nomi propri, cosi' lo si misura anche su
    Windows (helper ripreso da test_pdf_missing_metrics_followup, commit 7bddcdf)."""
    if font == "registered":
        return
    import importlib.util
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from bellomberg.reporting import pdf_institutional as pi
    ttf = Path(importlib.util.find_spec("matplotlib").origin).parent / "mpl-data" / "fonts" / "ttf"
    for name, file in (("ZZDV", "DejaVuSans.ttf"), ("ZZDVB", "DejaVuSans-Bold.ttf"),
                       ("ZZDVI", "DejaVuSans-Oblique.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(ttf / file)))
    pdfmetrics.registerFontFamily("ZZDV", normal="ZZDV", bold="ZZDVB", italic="ZZDVI", boldItalic="ZZDVB")
    monkeypatch.setattr(pi, "_register_fonts", lambda: ("ZZDV", "ZZDVB", "ZZDVI"))


# verdetti misurati fuori colonna dal revisore del PDF (09/10), uno per dominio
_VERDETTI_LUNGHI = {
    "macro": "unavailable - score cannot be calculated (disclosed)",
    "fundamentals": ("BOOK CARO (3/4 nomi valutati, 72% del book) [modelli da file, eta' max 23 gg] "
                     "[FLAGGED esclusi: ZZA] [senza modello recente: ZZB, ZZC, ZZD]"),
    "quant": "BOOK MOLTO CARO (12/123 nomi valutati)",
    "options": "n.d. - PARTIAL: ATM IV and skew unavailable for ZZTEST proxy, put/call only (disclosed)",
    "eventdesk": "EVENTI IN FERMENTO (news: 2/4 fonti mute) (copertura PARZIALE: mute: gnews, thenewsapi)",
}


@pytest.mark.parametrize("font", ["registered", "dejavu"])
def test_ogni_verdetto_del_cruscotto_va_a_capo_dentro_la_colonna(tmp_path, monkeypatch, font):
    _usa_font(monkeypatch, font)
    scoring = {k: {"score": 2, "max_score": 3, "verdict": v} for k, v in _VERDETTI_LUNGHI.items()}
    spans, testo = _pdf_testo(tmp_path, monkeypatch, scoring)
    intestazione = next(s for s in spans if s["text"].strip() == "Verdetto")
    sinistra = intestazione["bbox"][0] - 6            # LEFTPADDING 6
    destra = sinistra + 7.4 / 2.54 * 72 - 6           # colonna 7.4 cm meno RIGHTPADDING
    nella_colonna = [s for s in spans if sinistra - 1 <= s["bbox"][0] < sinistra + 20
                     and s["bbox"][1] > intestazione["bbox"][3]]
    nella_colonna.sort(key=lambda s: (round(s["bbox"][1], 1), s["bbox"][0]))
    # righe del cruscotto (anche la riga n.d. dichiarata del dominio vivo assente, crypto)
    from bellomberg.core.language import language_context
    with language_context("it"):
        righe = ss.collect_scoreboard(scoring)
    ordine = [r["key"] for r in righe]
    assert "crypto" in ordine
    atteso = " ".join(str(r["verdict"]) for r in righe).split()
    letto = " ".join(s["text"].strip() for s in nella_colonna).split()
    assert letto == atteso, letto           # nessuna parola persa ne' spostata
    assert len(nella_colonna) > len(ordine), "almeno un verdetto deve andare a capo"
    fuori = [(s["text"], round(s["bbox"][2] - destra, 1)) for s in nella_colonna if s["bbox"][2] > destra + 0.5]
    assert not fuori, fuori


# ======================================================= v2 10/10: riserve del revisore

def _payload(t, fv_base=120.0, price=100.0, usable=True, **extra):
    v = {"ticker": t, "fair_value_base": fv_base, "price": price, "currency": "USD",
         "valuation_usability": {"usable": usable}}
    v.update(extra)
    return v


def test_fv_canonico_diverso_dal_base_si_riporta_col_rapporto(isolato):
    """MEDIO 7 (uccide M1): FV pesato 130, base 120, prezzo osservato 125 (upside base -4%).
    MOS = 130/125 - 1 = +4,0%, non l'upside del base (-4,0%)."""
    v = quota_fresca(_payload("ZZA", fair_value_weighted=130.0), (120.0 / 125.0 - 1) * 100)
    r = ss.fundamentals_score(_port(("ZZA", 100)), {"ZZA": v})
    assert r["metrics"]["mos_book_pct"] == 4.0, r["metrics"]


def test_modello_non_utilizzabile_con_fv_numerico_resta_nd(isolato):
    """MEDIO 7 (uccide M4): il cancello `usable` vale anche se il payload porta un FV."""
    v = quota_fresca(_payload("ZZA", usable=False), 20.0)
    pos = {"ticker": "ZZA", "peso_pct": 100, "prezzo_live": 100.0, "price_stale": False,
           "price_age_minutes": 10, "valuta": "USD"}
    r = ss.fundamentals_score({"positions": [pos]}, {"ZZA": v})
    assert r["score"] is None and "modello non utilizzabile" in r["verdict"] + str(r["lines"])


def test_payload_bank_senza_fair_value_base_usa_il_prezzo_osservato(isolato):
    """MEDIO 7: payload bank (fair_value_blend, niente base) con quota «ok»: prima n.d."""
    v = _payload("ZZBNK", fv_base=None, fair_value_blend=130.0)
    v.pop("fair_value_base")
    v["market_quote"] = {"contract": mq.CONTRACT, "status": "ok", "freshness_policy": mq.FRESHNESS_POLICY,
                         "observed_local_date": date.today().isoformat(), "price": 104.0,
                         "upside_base_pct": None}
    r = ss.fundamentals_score(_port(("ZZBNK", 100)), {"ZZBNK": v})
    assert r["metrics"]["mos_book_pct"] == 25.0 and r["score"] == pytest.approx(0.5)   # 130/104 - 1


def _pos(t, prezzo, valuta="USD", stale=False, eta=30, peso=100):
    return {"ticker": t, "peso_pct": peso, "prezzo_live": prezzo, "price_stale": stale,
            "price_age_minutes": eta, "valuta": valuta}


def test_quota_vecchia_usa_il_prezzo_del_book_dichiarato(isolato):
    """ALTO 1a: modello da file con quota di 10 gg (R0/R1): il prezzo del book fresco vale,
    etichettato con fonte ed eta'; mai il prezzo del modello."""
    vecchio = (date.today() - timedelta(days=10)).isoformat()
    v = quota_fresca(_payload("ZZA"), 20.0, vecchio)
    r = ss.fundamentals_score({"positions": [_pos("ZZA", 150.0)]}, {"ZZA": v})
    assert r["metrics"]["mos_book_pct"] == -20.0 and r["score"] == pytest.approx(1.625, abs=0.01)   # 120/150 - 1
    riga = next(v for l, v, p in r["lines"] if str(l).strip().startswith("ZZA"))
    assert "prezzo DB position_prices, eta' 30 min" in riga, riga
    assert "[MOS su prezzo DB: ZZA]" in r["verdict"]


@pytest.mark.parametrize("pos,motivo", [
    (_pos("ZZA", 150.0, stale=True), "prezzo DB stale"),
    (_pos("ZZA", 150.0, valuta="EUR"), "valuta posizione EUR diversa dal modello USD"),
    (_pos("ZZA", 150.0, eta=8 * 24 * 60), "piu' vecchio di 5 gg"),
    (_pos("ZZA", 15000.0), "incoerente col modello"),
    (_pos("ZZA", None), "prezzo DB assente"),
])
def test_prezzo_del_book_non_usabile_resta_nd_col_motivo(isolato, pos, motivo):
    vecchio = (date.today() - timedelta(days=10)).isoformat()
    v = quota_fresca(_payload("ZZA"), 20.0, vecchio)
    r = ss.fundamentals_score({"positions": [pos]}, {"ZZA": v})
    assert r["score"] is None and motivo in str(r["lines"]), r["lines"]


def test_pence_e_sterline_scala_dichiarata(isolato):
    """GBX (modello) contro GBP (posizione): x100 dichiarato; GBp == GBX senza scala."""
    vecchio = (date.today() - timedelta(days=10)).isoformat()
    v = quota_fresca(_payload("ZZL.L", fv_base=1200.0, price=1000.0, currency="GBX"), 20.0, vecchio)
    r = ss.fundamentals_score({"positions": [_pos("ZZL.L", 12.5, valuta="GBP")]}, {"ZZL.L": v})
    assert r["metrics"]["mos_book_pct"] == -4.0                     # 1200/1250 - 1
    assert "scala x100 GBP->GBX" in str(r["lines"])
    r = ss.fundamentals_score({"positions": [_pos("ZZL.L", 1250.0, valuta="GBp")]}, {"ZZL.L": v})
    assert r["metrics"]["mos_book_pct"] == -4.0 and "scala" not in str(r["lines"])


def test_adr_fx_non_ritradotto_proxy_dichiarato(isolato):
    """MEDIO 6: quota fresca ma fx_not_rolled (il cambio BCE corrente vive nel job di
    reprice del worker, non nel payload): FV al cambio storico contro il prezzo osservato,
    dichiarato «FX storico (proxy)» nella riga e nel verdetto."""
    v = _payload("ZZADR")
    v["market_quote"] = {"contract": mq.CONTRACT, "status": "fx_not_rolled",
                         "freshness_policy": mq.FRESHNESS_POLICY,
                         "observed_local_date": date.today().isoformat(), "price": 96.0,
                         "upside_base_pct": None}
    r = ss.fundamentals_score(_port(("ZZADR", 100)), {"ZZADR": v})
    assert r["metrics"]["mos_book_pct"] == 25.0                     # 120/96 - 1
    assert "[FX storico (proxy): ZZADR]" in r["verdict"]
    assert "(FX storico (proxy))" in str(r["lines"])
    vecchio = (date.today() - timedelta(days=10)).isoformat()
    v["market_quote"]["observed_local_date"] = vecchio
    r = ss.fundamentals_score({"positions": [_pos("ZZADR", 100.0)]}, {"ZZADR": v})
    riga = next(x for l, x, p in r["lines"] if str(l).strip().startswith("ZZADR"))
    assert "prezzo DB position_prices" in riga and "FX storico (proxy)" in riga


def test_tutti_nd_ritorna_il_motivo_e_il_desk_lo_vede(isolato, monkeypatch):
    """ALTO 1b (sonda B del revisore): prima None -> nessuna riga SCORE nel preambolo R1."""
    vecchio = (date.today() - timedelta(days=7)).isoformat()
    port = _port(("ZZA", 50), ("ZZB", 50))
    vals = {t: quota_fresca(_payload(t), 20.0, vecchio) for t in ("ZZA", "ZZB")}
    r = ss.fundamentals_score(port, vals)
    assert r["verdict"].startswith("n.d. - prezzo corrente n.d. su 2/2 nomi"), r["verdict"]
    from bellomberg.agents.specialists import base
    board = SimpleNamespace(data={}, memory_db=None, valuation_results={},
                            mark_specialist_start=lambda *a: None,
                            summary_for_specialist=lambda name: {})
    s = base.Specialist(board, client=object())
    s.name = "fundamentals"
    monkeypatch.setattr(base, "blocco_vincoli_pm", lambda db: "")
    monkeypatch.setattr(base, "_blocco_round_precedente", lambda *a: "")
    monkeypatch.setattr(s, "compute_score", lambda: ss.fundamentals_score(port, vals))
    testo = s._build_round_context(1)
    assert "SCORE DETERMINISTICO (FUNDAMENTALS)" in testo
    assert "Verdetto: n.d. - prezzo corrente n.d. su 2/2 nomi" in testo
    assert "Prezzo corrente n.d." in testo
    riga = next(x for x in ss.collect_scoreboard(board.data["_score_cache"]) if x["key"] == "fundamentals")
    assert riga["verdict"].startswith("n.d. - prezzo corrente") and riga["score"] is None


def test_errore_r1_e_successo_r2_niente_score_nd_stantio(monkeypatch):
    """MEDIO 7 (uccide M3): l'errore dello scorer in R1 non resta nel preambolo di R2."""
    from bellomberg.agents.specialists import base
    from bellomberg.valuation import sector_analysis
    board = SimpleNamespace(data={}, memory_db=None, valuation_results={},
                            mark_specialist_start=lambda *a: None,
                            summary_for_specialist=lambda name: {})
    s = base.Specialist(board, client=object())
    s.name = "fundamentals"
    monkeypatch.setattr(base, "blocco_vincoli_pm", lambda db: "")
    monkeypatch.setattr(base, "_blocco_round_precedente", lambda *a: "")
    monkeypatch.setattr(sector_analysis, "valuation_results_block", lambda v: "VALUTAZIONI")

    def rotto():
        raise RuntimeError("ZZTEST guasto R1")
    monkeypatch.setattr(s, "compute_score", rotto)
    assert "[SCORE n.d.]" in s._build_round_context(1)
    monkeypatch.setattr(s, "compute_score", lambda: {"domain": "fundamentals", "score": 1, "max_score": 3,
                                                     "verdict": "VERDETTO-R2", "lines": []})
    board.valuation_results = {"ZZA": {"ticker": "ZZA"}}
    testo = s._build_round_context(2)
    assert "VERDETTO-R2" in testo and "[SCORE n.d.]" not in testo and "ZZTEST guasto" not in testo


def test_etf_dal_file_e_un_veicolo(isolato, monkeypatch, tmp_path):
    """BASSA: in R0/R1 (modelli da file) l'ETF era «senza modello recente»."""
    import json
    monkeypatch.setattr(ss, "REPORT_DIR", tmp_path)
    (tmp_path / "VAL_ZZETF.payload.json").write_text(json.dumps(
        {"ticker": "ZZETF", "engine": "etf_passive", "_timestamp": "2020-01-01"}), encoding="utf-8")
    r = ss.fundamentals_score(_port(("ZZETF", 30), ("ZZA", 70)), None)
    riga = next(v for l, v, p in r["lines"] if "Veicoli" in str(l))
    assert "ZZETF" in riga and "30.0%" in riga
    assert "ZZETF" not in r["verdict"].split("[veicoli")[0]
    assert "senza modello recente: ZZA" in r["verdict"] and "ZZETF" not in str(
        [v for l, v, p in r["lines"] if "Senza modello" in str(l)])


def test_verdetto_cita_la_quota_cara_o_troncata(isolato):
    """BASSA: EQUA in media con il 30% del valutato molto caro -> il verdetto lo dice."""
    r = ss.fundamentals_score(_port(("ZZA", 30), ("ZZB", 70)), _val(ZZA=(60, 100), ZZB=(110, 100)))
    # 10/10: MOS book -5% -> (45 + 5) / 40 = 1.25 punti, EQUA
    assert r["score"] == pytest.approx(1.25) and "VALUTAZIONE EQUA" in r["verdict"]
    assert "[cari/troncati 30% del valutato]" in r["verdict"], r["verdict"]
    r = ss.fundamentals_score(_port(("ZZA", 20), ("ZZB", 80)), _val(ZZA=(60, 100), ZZB=(110, 100)))
    assert "cari/troncati" not in r["verdict"]


def test_massimo_ricalcolato_non_conta_le_righe_buco():
    sc = {"domain": "politics", "score": 1, "max_score": 6, "verdict": "X", "unscored": ["c (n.d.)"],
          "lines": [("a", "10%", 1), ("b (n.d.)", "nessun mercato", 0), ("c (n.d.)", "n.d.", None)]}
    assert "Massimo ricalcolato su 1 metriche misurate" in ss.format_score_block(sc)


def test_pdf_eventdesk_con_news_nd_non_e_verde(tmp_path, monkeypatch):
    """MEDIO 5: stesso score 0/6, verde con news misurate, grigio con news n.d."""
    from bellomberg.reporting import pdf_institutional as pi
    verde = int(pi.GREEN.hexval()[2:], 16)
    grigio = int(pi.GREY.hexval()[2:], 16)

    def colore(componente_nd):
        scoring = {"eventdesk": {"score": 0, "max_score": 6, "verdict": "EVENTI CALMI ZZTEST",
                                 "componente_nd": componente_nd}}
        spans, _ = _pdf_testo(tmp_path, monkeypatch, scoring)
        return next(s["color"] for s in spans if s["text"].strip() == "EVENTI CALMI ZZTEST")
    assert colore(None) == verde
    assert colore("news") == grigio
