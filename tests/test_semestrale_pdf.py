"""PRY-H1 (10/10, Opus 5.5): numeri chiave dal PDF della relazione semestrale (emittenti europei senza XBRL).

v3 (revisione avversariale): un numero sbagliato e' peggio di n.d. Fixture SINTETICHE generate a runtime
(reportlab, nessun PDF committato), emittenti inventati, numeri interi non tondi. I PDF mettono le celle
sotto le colonne (allineate a destra) come i prospetti veri: la lettura usa le coordinate. Le prove su testo
(`testo_pagine`) coprono la lettura senza coordinate.
"""
import hashlib
from datetime import date
from io import BytesIO

import pytest

from bellomberg.market_data import semestrale_pdf as sp

EMITTENTE = "Cavi Fittizi Alfa S.p.A."
REGOLA = r"Cavi\s+Fittizi\s+Alfa"
X = (420, 530, 640, 750, 860, 970)  # bordo destro delle colonne (colonne larghe 110 punti)


def _pdf(pagine, *, inverso=False):
    """PDF vero: una stringa = riga a sinistra; (etichetta, [celle]) = celle allineate a destra sotto le colonne
    X (None = cella vuota). `inverso`: le celle si scrivono nello stream da destra a sinistra."""
    from reportlab.pdfgen import canvas
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(1000, 595))
    for righe in pagine:
        y = 560
        for r in righe:
            if isinstance(r, str):
                c.drawString(40, y, r)
            else:
                etichetta, celle = r
                if etichetta:
                    c.drawString(40, y, etichetta)
                coppie = [(x, v) for x, v in zip(X, celle) if v]
                for x, v in (reversed(coppie) if inverso else coppie):
                    c.drawRightString(x, y, v)
            y -= 14
        c.showPage()
    c.save()
    return buffer.getvalue()


def _h1_en(anno=2026, *, emittente=EMITTENTE, ordine_inverso=False, inverso=False, segmento=True):
    a, p = anno, anno - 1

    def r(etichetta, corrente, confronto, *altre):
        return (etichetta, [confronto, corrente, *altre] if ordine_inverso else [corrente, confronto, *altre])
    testa = r("", f"H1 {a}", f"H1 {p}", "% change", f"FY {p}")
    pagine = [
        [emittente, "HALF-YEAR FINANCIAL REPORT", f"AT 30 JUNE {a}", "Margin 12.5% 11.3% 10.1%"],
        [f"{emittente} | DIRECTORS' REPORT", "CONSOLIDATED FINANCIAL HIGHLIGHTS", "(€m)", testa,
         r("Revenues", "7,413", "6,958", "6.5%", "14,206"),
         r("Adj. EBITDA (1)", "1,187", "1,046", "13.5%", "2,233"),
         r("Net profit", "451", "389", "15.9%", "907"),
         "(1) Adjusted EBITDA is defined as EBITDA before non-recurring items.",
         "CONSOLIDATED NET FINANCIAL POSITION", "(€m)",
         r("", f"June 30, {a}", f"June 30, {p}", "Change", f"December 31, {p}"),
         r("Net financial debt", "2,861", "3,247", "(386)", "2,594")],
        [f"{emittente} | DIRECTORS' REPORT", "GROUP PERFORMANCE AND RESULTS", "(€m)", testa,
         r("Revenues", "7,413", "6,958", "6.5%", "14,206"),
         r("EBITDA", "1,139", "1,052", "8.3%", "2,171"),
         r("Operating income", "763", "617", "23.7%", "1,482"),
         r("Net profit", "451", "389", "15.9%", "907"),
         r("% of revenues", "6.1%", "5.6%", None, "6.4%"),
         "Attributable to:",
         r("Owners of the parent", "437", "372", None, "878"),
         r("Non-controlling interests", "14", "17", None, "29")],
    ]
    if segmento:
        pagine.append([f"{emittente} | DIRECTORS' REPORT", "PERFORMANCE OF CABLES OPERATING SEGMENT", "(€m)", testa,
                       r("Revenues", "2,183", "1,974", "10.6%", "4,117"), r("Adj. EBITDA", "362", "331", "9.4%", "684")])
    pagine.append([f"{emittente} | DIRECTORS' REPORT", "CONSOLIDATED STATEMENT OF CASH FLOWS", "(€m)",
                   ("", [None, None, None, "12 months", None]),
                   ("", [f"H1 {a}", f"H1 {p}", "Change", "ended June", f"FY {p}"]),
                   ("", [None, None, None, f"30, {a}", None]),
                   ("Net cash flow from operating activities", ["1,263", "1,076", "187", "2,415", "2,228"]),
                   "(before changes in net working capital)",
                   ("Net cash flow from operating activities", ["529", "418", "111", "1,377", "1,266"]),
                   ("Free cash flow", ["(183)", "241", "(424)", "604", "1,028"]),
                   ("Net financial debt at end of period", ["(2,861)", "(3,247)", "386", "(2,861)", "(2,594)"])])
    return _pdf(pagine, inverso=inverso)


def _scrivi(tmp_path, nome, contenuto):
    path = tmp_path / nome
    path.write_bytes(contenuto)
    return path


def _estrai(path, fine="2026-06-30", **kw):
    kw.setdefault("regola_emittente", REGOLA)
    return sp.estrai_numeri_semestrale(str(path), periodo_fine=fine, periodo_inizio=fine[:4] + "-01-01", **kw)


def _testo(*pagine, fine="2026-06-30", **kw):
    """Prova su testo (senza coordinate): pagine come liste di righe; il nome dell'emittente in testa."""
    kw.setdefault("regola_emittente", REGOLA)
    tp = [(n, "\n".join([EMITTENTE] + list(p))) for n, p in enumerate(pagine, 1)]
    return sp.estrai_numeri_semestrale("x.pdf", periodo_fine=fine, testo_pagine=tp, **kw)


F = "Margin 12.5% 11.3% 10.1% 9.8% 7.7% 6.6%"   # prova del formato 1,234.5
FI = "Margine 5,3% 7,4% 8,4% 11,2% 15,6% 10,0%"  # prova del formato 1.234,5


# --- lettura dal PDF (coordinate) ----------------------------------------------------------------------------

def test_inglese_colonna_corrente_mai_il_confronto(tmp_path):
    r = _estrai(_scrivi(tmp_path, "h1.pdf", _h1_en()), fino_al="2026-10-10")
    assert r["stato"] == "ok" and r["formato_numerico"] == "en"
    v = r["voci"]
    assert (v["ricavi"]["corrente"], v["ricavi"]["confronto"]) == (7413 * 10 ** 6, 6958 * 10 ** 6)
    assert v["ricavi"]["unita"] == "milioni" and v["ricavi"]["valuta"] == "EUR"
    assert v["ebitda"]["corrente"] == 1139 * 10 ** 6 and v["ebitda"]["misura"] == "riportato"
    assert v["ebitda_rettificato"]["corrente"] == 1187 * 10 ** 6 and v["ebitda_rettificato"]["misura"] == "rettificato"
    assert v["utile_operativo"]["corrente"] == 763 * 10 ** 6
    # quota del gruppo: «Attributable to: / Owners of the parent» subito dopo l'utile netto (la riga di soli % non conta)
    assert v["utile_netto"]["corrente"] == 437 * 10 ** 6 and v["utile_netto"]["confronto"] == 372 * 10 ** 6
    assert "utile_netto_totale" not in v
    # istante: 30/06 corrente e 30/06 dell'anno prima, mai il 31/12; «at end of period» fra parentesi non e' la voce
    assert (v["debito_netto"]["corrente"], v["debito_netto"]["confronto"]) == (2861 * 10 ** 6, 3247 * 10 ** 6)
    assert "non verificata" in v["debito_netto"]["segno"]
    # intestazione su tre righe («12 months / ended June / 30, 2026»); etichetta che continua sotto i numeri
    # («... (before changes in net working capital)»): non e' il flusso di cassa operativo
    assert v["flusso_cassa_operativo"]["corrente"] == 529 * 10 ** 6
    assert v["free_cash_flow"]["corrente"] == -183 * 10 ** 6 and v["free_cash_flow"]["confronto"] == 241 * 10 ** 6
    # tabella di segmento con valori diversi: esclusa
    assert v["ricavi"]["pagine"] == [2, 3]
    assert r["scarti"] == {} and "data di pubblicazione ignota" in r["avvisi"][0]


def test_colonne_in_ordine_inverso_e_stream_inverso(tmp_path):
    for nome, pdf in (("ord.pdf", _h1_en(ordine_inverso=True)), ("stream.pdf", _h1_en(inverso=True))):
        v = _estrai(_scrivi(tmp_path, nome, pdf))["voci"]
        assert v["ricavi"]["corrente"] == 7413 * 10 ** 6 and v["ricavi"]["confronto"] == 6958 * 10 ** 6, nome
        assert v["utile_netto"]["corrente"] == 437 * 10 ** 6 and v["utile_netto"]["confronto"] == 372 * 10 ** 6, nome


def test_cella_a_cavallo_di_due_colonne_non_si_assegna():
    colonne = [("corrente", "H1 2026"), ("confronto", "H1 2025")]
    x = [(375.0, 420.0), (485.0, 530.0)]
    mappa, etichetta, n = sp._mappa_visiva(colonne, x, [("Revenues", 40, 90), ("7,413", 410, 500)], "en")
    assert mappa is None and etichetta == "Revenues" and n == 1  # 10 punti su una colonna, 15 sull'altra
    mappa, _, _ = sp._mappa_visiva(colonne, x, [("Revenues", 40, 90), ("7,413", 390, 420), ("6,958", 500, 530)], "en")
    assert mappa == {0: "7,413", 1: "6,958"}


def test_tabelle_affiancate_e_unita_nella_riga(tmp_path):
    pdf = _pdf([[EMITTENTE, F], [
        "KEY FIGURES OF THE GROUP",
        ("", ["H1 2026", "H1 2025", None, "H1 2026", "H1 2025"]),
        ("Sales € million", ["3,289", "2,947", None, None, None]),
        ("EBIT € million", ["562", "481", None, None, None]),
    ]])
    v = _estrai(_scrivi(tmp_path, "aff.pdf", pdf))["voci"]
    assert v["ricavi"]["corrente"] == 3289 * 10 ** 6 and v["ricavi"]["unita"] == "milioni"
    assert v["utile_operativo"]["corrente"] == 562 * 10 ** 6


# --- lingue, formati, unita' (prove su testo) ----------------------------------------------------------------

def test_italiano_in_migliaia_con_negativi():
    r = _testo([FI], ["PRINCIPALI DATI DEL GRUPPO", "(in migliaia di euro)", "1° semestre 2026 1° semestre 2025 Var. %",
                      "Ricavi 48.317 45.902 5,3%", "EBITDA rettificato 6.271 5.838 7,4%",
                      "Risultato operativo 3.164 3.402 (7,0%)", "Risultato netto di competenza del Gruppo (2.719) 1.846 n.s.",
                      "(in migliaia di euro)", "30.06.2026 31.12.2025", "Indebitamento finanziario netto 19.473 17.926"])
    v = r["voci"]
    assert r["formato_numerico"] == "it" and v["ricavi"]["corrente"] == 48317 * 10 ** 3
    assert v["ricavi"]["unita"] == "migliaia" and v["ebitda_rettificato"]["misura"] == "rettificato"
    assert v["utile_netto"]["corrente"] == -2719 * 10 ** 3 and v["utile_netto"]["confronto"] == 1846 * 10 ** 3
    assert v["debito_netto"]["corrente"] == 19473 * 10 ** 3 and v["debito_netto"]["confronto"] is None


def test_tedesco_mio_euro_meno_e_davon():
    r = _testo([FI], ["KONZERN-KENNZAHLEN", "in Mio. €", "1. Halbjahr 2026 1. Halbjahr 2025 Veränderung",
                      "Umsatzerlöse 3.417,6 3.152,9 8,4%", "Bereinigtes EBITDA 521,3 468,7 11,2%",
                      "Konzernergebnis 163,9 –42,7 n.a.", "davon:", "Aktionäre der Kabelwerke Fiktiv AG 151,2 –47,3",
                      "Freier Cashflow –86,1 112,4 n.a."])
    v = r["voci"]
    assert v["ricavi"]["corrente"] == pytest.approx(3417.6e6)  # colonna «Veränderung» con % non fa cadere la riga
    assert v["ebitda_rettificato"]["corrente"] == pytest.approx(521.3e6)
    assert v["utile_netto"]["corrente"] == pytest.approx(151.2e6) and v["utile_netto"]["confronto"] == pytest.approx(-47.3e6)
    assert v["free_cash_flow"]["corrente"] == pytest.approx(-86.1e6)


def test_francese_spazi_normali_e_non_divisibili():
    nb = " "
    r = _testo(["Marge 12,5 % 11,3 % 10,1 % 9,8 % 7,7 %"],
               ["CHIFFRES CLÉS CONSOLIDÉS", "(en milliers d'euros)", "S1 2025 S1 2026 Variation",
                "Chiffre d'affaires 41 873 46 219 4 346", f"EBITDA ajusté 6{nb}157 7{nb}046 889",
                "Résultat net part du groupe (1 264) 2 731 3 995", "Dette nette 19 384 17 652 (1 732)"])
    v = r["voci"]
    assert v["ricavi"]["corrente"] == 46219 * 10 ** 3 and v["ricavi"]["confronto"] == 41873 * 10 ** 3
    assert v["ebitda_rettificato"]["corrente"] == 7046 * 10 ** 3
    assert v["utile_netto"]["corrente"] == 2731 * 10 ** 3 and v["utile_netto"]["confronto"] == -1264 * 10 ** 3
    assert v["debito_netto"]["corrente"] == 17652 * 10 ** 3  # ordine S1 2025 | S1 2026


def test_olandese_e_spagnolo():
    nl = _testo([FI], ["KERNCIJFERS GROEP", "(in miljoenen euro)", "eerste halfjaar 2026 eerste halfjaar 2025",
                       "Omzet 2.843,7 2.611,2", "Netto schuld 917,4 1.032,6"])["voci"]
    assert nl["ricavi"]["corrente"] == pytest.approx(2843.7e6) and nl["debito_netto"]["corrente"] == pytest.approx(917.4e6)
    es = _testo([FI], ["PRINCIPALES MAGNITUDES DEL GRUPO", "(millones de euros)", "1S 2026 1S 2025 Var. %",
                       "Importe neto de la cifra de negocios 2.417 2.198 10,0%", "EBITDA ajustado 389 352 10,5%",
                       "Beneficio neto atribuible a la sociedad dominante 163 141 15,6%",
                       "Deuda financiera neta 1.274 1.391 (8,4%)"])["voci"]
    assert es["ricavi"]["corrente"] == 2417 * 10 ** 6 and es["ebitda_rettificato"]["misura"] == "rettificato"
    assert es["utile_netto"]["corrente"] == 163 * 10 ** 6 and es["debito_netto"]["corrente"] == 1274 * 10 ** 6
    m = _testo([FI], ["PRINCIPALES MAGNITUDES DEL GRUPO", "M€", "1S 2026 1S 2025", "EBITDA 1.276 1.134"])["voci"]
    assert m["ebitda"]["corrente"] == 1276 * 10 ** 6


def test_miliardi_dopo_milioni_e_unita_ignota():
    r = _testo([F], ["GROUP KEY FIGURES", "(€m)", "H1 2026 H1 2025", "Order intake 9,463 5,159",
                     "(€bn)", "H1 2026 H1 2025", "Revenues 11.2 9.7"])
    assert r["voci"]["ricavi"]["corrente"] == pytest.approx(11.2e9) and r["voci"]["ricavi"]["unita"] == "miliardi"
    r = _testo([F], ["GROUP KEY FIGURES", "(€m)", "H1 2026 H1 2025", "Order intake 9,463 5,159",
                     "(EUR mds)", "H1 2026 H1 2025", "Revenues 11.2 9.7"])
    assert "ricavi" not in r["voci"] and "non riconosciute" in r["scarti"]["ricavi"]
    assert sp._unita("in € thousand / in € million")[0] == "milioni"  # l'ultimo marcatore della riga


def test_unita_mai_ereditata_ne_presa_dalla_prosa():
    # pagina precedente, prosa «1,000 euros», tabella seguente senza marcatore proprio: n.d.
    r = _testo([F, "(€ thousand)"], ["CONSOLIDATED INCOME STATEMENT", "H1 2026 H1 2025", "Revenues 4,317 3,902"])
    assert "non dichiarate" in r["scarti"]["ricavi"]
    r = _testo([F], ["Each director received a fee of 1,000 euros per meeting.", "CONSOLIDATED INCOME STATEMENT",
                     "H1 2026 H1 2025", "Revenues 4,317 3,902"])
    assert "non dichiarate" in r["scarti"]["ricavi"]
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(US$m)", "H1 2026 H1 2025", "EBITDA 1,203 1,118",
                     "Translated into euro for convenience", "H1 2026 H1 2025", "Revenues 4,317 3,902"])
    assert r["voci"]["ebitda"]["valuta"] == "USD" and "non dichiarate" in r["scarti"]["ricavi"]


def test_unita_nella_riga_su_testo():
    r = _testo([F], ["KEY FIGURES OF THE GROUP", "H1 2026 H1 2025", "Sales € million 3,289 2,947"])
    assert r["voci"]["ricavi"]["corrente"] == 3289 * 10 ** 6


# --- periodo: sei mesi per le durate --------------------------------------------------------------------------

def test_durata_solo_con_sei_mesi_dichiarati():
    trimestre = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "€ million", "1.4.-30.6.2026 1.4.-30.6.2025",
                             "Sales 3,289 1,949"])
    assert "ricavi" not in trimestre["voci"] and "sei mesi" in trimestre["scarti"]["ricavi"]
    semestre = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "€ million", "1.1.-30.6.2026 1.1.-30.6.2025",
                            "Sales 5,227 3,749"])
    assert semestre["voci"]["ricavi"]["corrente"] == 5227 * 10 ** 6
    nuda = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "(€m)", "June 30, 2026 June 30, 2025",
                        "Revenues 5,817 5,103", "Net financial debt 2,861 3,247"])
    assert "ricavi" not in nuda["voci"] and nuda["voci"]["debito_netto"]["corrente"] == 2861 * 10 ** 6
    tre_mesi = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "(€m)", "Three months ended",
                            "June 30, 2026 June 30, 2025", "Revenues 3,289 1,949"])
    assert "ricavi" not in tre_mesi["voci"]
    sei_mesi = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "(€m)", "Six months ended",
                            "June 30, 2026 June 30, 2025", "Revenues 5,817 5,103"])
    assert sei_mesi["voci"]["ricavi"]["corrente"] == 5817 * 10 ** 6


def test_due_colonne_correnti_ambigue():
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2026 H1 2025", "Revenues 7,413 7,388 6,958"])
    assert "ricavi" not in r["voci"]


def test_classifica_colonne():
    fine = date(2026, 6, 30)
    tipi = [t for t, _ in sp.classifica_colonne(
        "Q2 2026 Q2 2025 H1 2026 H1 2025 12 months ended June 30, 2026 FY 2025 % change Change", fine)]
    assert tipi == ["altro", "altro", "corrente", "confronto", "altro", "altro", "var_pct", "var_abs"]
    tipi = [t for t, _ in sp.classifica_colonne("30.06.2026 30.06.2025 31 dicembre 2025", fine)]
    assert tipi == ["corrente_istante", "confronto_istante", "altro"]
    assert sp.formato_numerico("1,234.5 6.5%") == "en" and sp.formato_numerico("1.234,5 6,5%") == "it"
    assert sp.formato_numerico("7,413 6,958") is None


# --- perimetro -------------------------------------------------------------------------------------------------

def test_perimetro_non_provato_e_tabelle_escluse():
    # pagina di un business senza parola chiave e senza titolo di gruppo: n.d.
    r = _testo([F], ["ENERGY SOLUTIONS", "(€m)", "H1 2026 H1 2025 % change", "Revenues 2,183 1,974 10.6%"])
    assert "perimetro non provato" in r["scarti"]["ricavi"]
    # segmento con titolo di gruppo: escluso; il valore e' quello del gruppo
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"],
               ["CONSOLIDATED SEGMENT INFORMATION - CABLES SEGMENT", "(€m)", "H1 2026 H1 2025", "Revenues 2,183 1,974"])
    assert r["voci"]["ricavi"]["corrente"] == 7413 * 10 ** 6
    # attivita' cessate con titolo consolidato: escluse
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"],
               ["CONSOLIDATED DISCONTINUED OPERATIONS", "(€m)", "H1 2026 H1 2025", "Revenues 953 1,107"])
    assert r["voci"]["ricavi"]["corrente"] == 7413 * 10 ** 6
    # prospetto dell'utile per azione con utile rettificato: escluso
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Net profit 451 389", "Attributable to:",
                     "Owners of the parent 437 372"],
               ["CONSOLIDATED EARNINGS PER SHARE", "(€m)", "H1 2026 H1 2025",
                "Net profit attributable to owners of the parent 412 361", "Weighted average number of shares 291 286",
                "Basic earnings per share 1.41 1.26"])
    assert r["voci"]["utile_netto"]["corrente"] == 437 * 10 ** 6
    # due tabelle di gruppo discordi: ambigua
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"],
               ["GROUP INCOME STATEMENT", "(€m)", "H1 2026 H1 2025", "Revenues 7,388 6,958"])
    assert "ambigua" in r["scarti"]["ricavi"]


def test_gruppo_dalla_testata_della_pagina():
    prosa = ["The figures below are taken from the interim report.", "They have been reviewed by the auditor.",
             "Amounts are rounded to the nearest unit.", "Totals may not add up because of rounding.",
             "Comparative figures are presented for the same period.", "No change in accounting policies occurred."]
    tp = [(1, "\n".join([EMITTENTE, F])),
          (2, "\n".join(["Consolidated half-year report"] + prosa + ["SUMMARY", "(€m)", "H1 2026 H1 2025",
                                                                     "Revenues 7,413 6,958"]))]
    r = sp.estrai_numeri_semestrale("x.pdf", periodo_fine="2026-06-30", testo_pagine=tp, regola_emittente=REGOLA)
    assert r["voci"]["ricavi"]["corrente"] == 7413 * 10 ** 6  # la sola prova di gruppo e' la testata della pagina


def test_tabelle_di_gruppo_discordi_con_altre_tabelle():
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"],
               ["GROUP INCOME STATEMENT", "(€m)", "H1 2026 H1 2025", "Revenues 7,388 6,958"],
               ["ENERGY SOLUTIONS", "(€m)", "H1 2026 H1 2025", "Revenues 2,183 1,974"])
    assert "ricavi" not in r["voci"] and "ambigua" in r["scarti"]["ricavi"]


def test_quota_del_gruppo_solo_subito_dopo_l_utile_netto():
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Net profit 451 389",
                     "Other items 120 (30)", "Total 571 359", "Attributable to:", "Owners of the parent 553 348"])
    assert "utile_netto" not in r["voci"] and r["voci"]["utile_netto_totale"]["corrente"] == 451 * 10 ** 6


def test_conto_economico_complessivo_e_minoranze():
    r = _testo([F], ["CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME", "(€m)", "H1 2026 H1 2025", "Net profit 451 389",
                     "Currency translation 120 (30)", "Total comprehensive income 571 359", "Attributable to:",
                     "Owners of the parent 553 348", "Non-controlling interests 18 11"])
    assert "utile_netto" not in r["voci"] and r["voci"]["utile_netto_totale"]["corrente"] == 451 * 10 ** 6
    assert r["voci"]["utile_netto_totale"]["perimetro"] == "totale, incluse le minoranze"
    r = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "(€m)", "H1 2026 H1 2025",
                     "Net profit attributable to owners of the parent and non-controlling interests 451 389"])
    assert "utile_netto" not in r["voci"]  # «... and non-controlling interests» e' il totale, non la quota del gruppo
    r = _testo([F], ["CONSOLIDATED INCOME STATEMENT", "(€m)", "H1 2026 H1 2025", "Net profit 451 389"])
    assert r["voci"]["utile_netto_totale"]["perimetro"].startswith("non determinato")


def test_segno_del_debito_netto():
    r = _testo([F], ["CONSOLIDATED STATEMENT OF CASH FLOWS", "(€m)", "H1 2026 H1 2025", "Net financial debt (2,861) (3,247)"])
    assert "convenzione di segno" in r["scarti"]["debito_netto"]
    r = _testo([F], ["CONSOLIDATED HIGHLIGHTS", "(€m)", "H1 2026 H1 2025", "Net financial debt 2,861 3,247"],
               ["GROUP NET DEBT", "(€m)", "H1 2026 H1 2025", "Net financial debt (2,861) (3,247)"])
    assert "segno opposto" in r["scarti"]["debito_netto"] or "ambigua" in r["scarti"]["debito_netto"]


def test_colonna_di_variazione_su_riga_separata_e_note():
    r = _testo([F], ["GROUP KEY DATA", "Change", "€ million H1 2026 H1 2025", "Free cash flow 7 4 3"])
    assert "free_cash_flow" not in r["voci"] and "riga separata" in r["scarti"]["free_cash_flow"]
    r = _testo([F], ["CONSOLIDATED KEY FIGURES", "(€m)", "H1 2026 H1 2025 Change", "Net cash 3 8 (5)",
                     "Free cash flow (1) 7 4 3"])
    assert r["voci"]["cassa_netta"]["corrente"] == 3 * 10 ** 6 and r["voci"]["free_cash_flow"]["corrente"] == 7 * 10 ** 6
    r = _testo([F], ["CONSOLIDATED KEY FIGURES", "(€m)", "H1 2026 H1 2025", "Free cash flow 2 7 4 3"])
    assert "free_cash_flow" not in r["voci"]  # due celle in piu': nessuna lettura


def test_operating_result_e_misura_dell_emittente():
    r = _testo([F], ["KEY FIGURES OF THE GROUP", "(€m)", "H1 2026 H1 2025", "Operating result 786 453", "EBIT 664 385"])
    assert r["voci"]["utile_operativo"]["corrente"] == 664 * 10 ** 6
    assert r["voci"]["utile_operativo_rettificato"]["misura"] == "misura dell'emittente"


# --- v4 (seconda revisione) -----------------------------------------------------------------------------------

def test_v4_affiancate_unita_della_tabella_accanto_non_entra(tmp_path):
    pdf = _pdf([[EMITTENTE, F], [
        "CONSOLIDATED KEY FIGURES", "(€m)",
        ("", ["H1 2026", "H1 2025", None, "H1 2026", "H1 2025"]),
        ("Revenues", ["7,413", "6,958", "Fees € thousand", "5,120", "4,870"]),
    ]])
    v = _estrai(_scrivi(tmp_path, "aff2.pdf", pdf))["voci"]
    assert v["ricavi"]["corrente"] == 7413 * 10 ** 6 and v["ricavi"]["unita"] == "milioni"


def test_v4_sei_mesi_mai_dalla_testata_ricorrente_ne_con_mesi_di_un_trimestre():
    for titolo, data in (("Consolidated income statement for the period April 1 - June 30", "June 30, 2026 June 30, 2025"),
                         ("Konzern-Gewinn- und Verlustrechnung April bis Juni", "June 30, 2026 June 30, 2025"),
                         ("CONSOLIDATED INCOME STATEMENT", "June 30, 2026 June 30, 2025")):
        tp = [(1, "\n".join([EMITTENTE, F])),
              (2, "\n".join(["Half-year financial report 2026", titolo, "(€m)", data, "Revenues 3,289 1,949"]))]
        r = sp.estrai_numeri_semestrale("x.pdf", periodo_fine="2026-06-30", testo_pagine=tp, regola_emittente=REGOLA)
        assert "ricavi" not in r["voci"], titolo
    # trimestre due righe sopra e sei mesi nel titolo: istante si', durata no (N4)
    r = _testo([F], ["SECOND QUARTER", "CONSOLIDATED STATEMENT - SIX MONTHS", "(€m)", "June 30, 2026 June 30, 2025",
                     "Revenues 5,817 5,103"])
    assert "ricavi" not in r["voci"]
    # trimestre nell'intestazione stessa e sei mesi nel titolo: le date nude non sono il semestre (N4)
    r = _testo([F], ["CONSOLIDATED INCOME STATEMENT - SIX MONTHS", "(€m)", "June 30, 2026 June 30, 2025 Q2 2026",
                     "Revenues 5,817 5,103 2,911"])
    assert "ricavi" not in r["voci"]
    assert all(sp._RX_TRE.search(t) for t in ("aprile-giugno", "avril-juin", "abril-junio", "1 April – 30 June"))


def test_v4_attivita_continuative():
    pagine = ([F], ["KEY FIGURES GROUP (CONTINUING OPERATIONS)", "(€m)", "H1 2026 H1 2025", "Free cash flow (1,616) (631)"])
    r = _testo(*pagine)
    assert r["voci"]["free_cash_flow"]["perimetro"].startswith("solo attivita' continuative")
    r = _testo(*pagine, ["CONSOLIDATED DISCONTINUED OPERATIONS", "Civil business held for sale."])
    assert "free_cash_flow" not in r["voci"] and "attivita' cessate" in r["scarti"]["free_cash_flow"]


def test_v4_etichetta_separata_dall_indice_laterale():
    colonne = [("corrente", "H1 2026"), ("confronto", "H1 2025")]
    x = [(375.0, 420.0), (485.0, 530.0)]
    linea = [("Outlook", 20, 60), ("Free", 150, 170), ("cash", 173, 195), ("flow", 198, 218),
             ("(1,660)", 385, 420), ("(644)", 500, 530)]
    mappa, etichetta, _ = sp._mappa_visiva(colonne, x, linea, "en")
    assert etichetta == "Free cash flow" and mappa == {0: "(1,660)", 1: "(644)"}


def test_v5_colonna_di_unita_fra_etichetta_e_numeri(tmp_path):
    colonne = [("corrente", "H1 2026"), ("confronto", "H1 2025")]
    x = [(375.0, 420.0), (485.0, 530.0)]
    linea = [("EBIT", 40, 68), ("€", 280, 285), ("million", 287, 320), ("236", 400, 420), ("201", 510, 530)]
    mappa, etichetta, _ = sp._mappa_visiva(colonne, x, linea, "en")
    assert etichetta == "EBIT" and mappa == {0: "236", 1: "201"}
    assert sp._voce_di("earnings before interest and taxes (ebit)")[0] == "utile_operativo"
    # PDF: «Revenues / Europe € million 3,000 ...» -> la sottovoce non diventa i ricavi (niente eredita'
    # dell'etichetta sopra); la colonna d'unita' non svuota l'etichetta della riga semplice
    from reportlab.pdfgen import canvas
    casi = (("sottovoci", [("Revenues", None), ("Europe", ("3,000", "2,800")), ("Rest of world", ("8,239", "6,854"))], None),
            ("semplice", [("Revenues", ("7,413", "6,958")), ("EBITDA", ("1,139", "1,052"))], 7413 * 10 ** 6))
    for nome, righe, attesa in casi:
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=(1000, 595))
        c.drawString(40, 560, EMITTENTE)
        c.drawString(40, 546, F)
        c.showPage()
        c.drawString(40, 560, "CONSOLIDATED KEY FIGURES")
        c.drawRightString(420, 546, "H1 2026")
        c.drawRightString(530, 546, "H1 2025")
        for k, (etichetta, valori) in enumerate(righe):
            y = 532 - 14 * k
            c.drawString(40, y, etichetta)
            if valori:
                c.drawString(240, y, "€ million")
                c.drawRightString(420, y, valori[0])
                c.drawRightString(530, y, valori[1])
        c.showPage()
        c.save()
        r = _estrai(_scrivi(tmp_path, f"{nome}.pdf", buffer.getvalue()))
        if attesa is None:
            assert "ricavi" not in r["voci"], nome
        else:
            assert r["voci"]["ricavi"]["corrente"] == attesa and r["voci"]["ricavi"]["unita"] == "milioni", nome


def test_v5_testata_della_pagina_fuori_dal_contesto_dei_sei_mesi():
    # tabella in cima alla pagina: la testata ricorrente e' la riga subito sopra l'intestazione (V4)
    tp = [(1, "\n".join([EMITTENTE, F])),
          (2, "\n".join(["Consolidated half-year financial report 2026", "June 30, 2026 June 30, 2025", "(€m)",
                         "Revenues 3,289 1,949", "Net financial debt 2,861 3,247"]))]
    r = sp.estrai_numeri_semestrale("x.pdf", periodo_fine="2026-06-30", testo_pagine=tp, regola_emittente=REGOLA)
    assert "ricavi" not in r["voci"] and r["voci"]["debito_netto"]["corrente"] == 2861 * 10 ** 6


def test_v4_nome_dell_emittente_solo_intero_e_con_parole_di_risultato():
    assert sp._sezione_del_gruppo("CAVI FITTIZI ALFA'S PERFORMANCE AND RESULTS", "Cavi Fittizi Alfa")
    for sezione in ("RISULTATI DELLA CAPOGRUPPO", "CAVI FITTIZI ALFA LEASING GMBH", "CAVI FITTIZI ALFA PARENT COMPANY",
                    "CAVI LEASING RESULTS"):
        assert not sp._sezione_del_gruppo(sezione, "Cavi Fittizi Alfa"), sezione
    # il nome nella testata ricorrente della pagina non prova il gruppo (N7)
    tp = [(1, "\n".join([EMITTENTE, F])),
          (2, "\n".join(["CAVI FITTIZI ALFA RESULTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"]))]
    r = sp.estrai_numeri_semestrale("x.pdf", periodo_fine="2026-06-30", testo_pagine=tp, regola_emittente=REGOLA)
    assert "perimetro non provato" in r["scarti"]["ricavi"]
    tp[1] = (2, "\n".join(["Interim report", "CAVI FITTIZI ALFA RESULTS", "(€m)", "H1 2026 H1 2025", "Revenues 7,413 6,958"]))
    r = sp.estrai_numeri_semestrale("x.pdf", periodo_fine="2026-06-30", testo_pagine=tp, regola_emittente=REGOLA)
    assert r["voci"]["ricavi"]["corrente"] == 7413 * 10 ** 6


def test_v4_due_celle_nella_stessa_colonna_e_ordine_ignoto():
    colonne = [("corrente", "H1 2026"), ("confronto", "H1 2025")]
    mappa, _, _ = sp._mappa_visiva(colonne, [(375.0, 420.0), (485.0, 530.0)],
                                   [("Revenues", 40, 90), ("7,413", 380, 400), ("7,388", 402, 420)], "en")
    assert mappa is None  # N5
    # intestazioni sovrapposte per piu' di meta': ordine non verificabile, n.d. anche con celle assegnabili (N6)
    visive = [[(EMITTENTE, 40, 160)], [("CONSOLIDATED", 40, 120), ("HIGHLIGHTS", 125, 190)], [("(€m)", 40, 60)],
              [("H1", 300, 312), ("2026", 314, 340), ("H1", 318, 330), ("2025", 332, 358)],
              [("Revenues", 40, 90), ("7,413", 300, 316), ("6,958", 342, 358)]]
    trovate, scartate = sp._leggi([(1, "", visive)], date(2026, 6, 30), "en")
    assert "ricavi" not in trovate and "ordine visivo" in " ".join(scartate["ricavi"])


def test_v4_quota_del_gruppo_mai_nel_conto_economico_complessivo():
    r = _testo([F], ["CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME", "(€m)", "H1 2026 H1 2025", "Net profit 451 389",
                     "Attributable to:", "Owners of the parent 437 372"])
    assert "utile_netto" not in r["voci"]  # N9


# --- identita', look-ahead ------------------------------------------------------------------------------------

def test_altro_emittente_rifiutato(tmp_path):
    r = _estrai(_scrivi(tmp_path, "h1.pdf", _h1_en(emittente="Rame Immaginario Beta S.p.A.")))
    assert r["stato"] == "rifiutato" and "altro emittente" in r["motivo"] and r["voci"] == {}


def test_niente_look_ahead_e_byte_verificati(tmp_path):
    path = _scrivi(tmp_path, "h1.pdf", _h1_en())
    r = _estrai(path, fino_al="2026-06-29")
    assert r["stato"] == "rifiutato" and "look-ahead" in r["motivo"]
    r = _estrai(path, fino_al="2026-08-01", filed_date="2026-08-03")
    assert r["stato"] == "rifiutato" and "dopo il cutoff" in r["motivo"]
    r = _estrai(path, sha256_atteso="0" * 64)
    assert r["stato"] == "rifiutato" and "sha256" in r["motivo"]


# --- coppia, pipeline, riga per gli agenti ----------------------------------------------------------------------

def _coppia(tmp_path, dopo_pdf, prima_pdf=None, *, url_dopo="https://example.invalid/ir/h1-2026.pdf"):
    def lato(nome, contenuto, anno, url):
        path = _scrivi(tmp_path, nome, contenuto)
        return {"url": url, "path": str(path), "sha256": hashlib.sha256(contenuto).hexdigest(),
                "metadati": {"emittente_id": "lei:ZZCVALFA000000000000", "lingua": "en", "tipo": "semestrale",
                             "perimetro": "consolidato", "periodo_inizio": f"{anno}-01-01",
                             "periodo_fine": f"{anno}-06-30"},
                "prove_verifica": {"emittente": {"testo": EMITTENTE}}, "filed_date": None}
    return {"ambito": "ultimo_verificato",
            "prima": lato("h1_2025.pdf", prima_pdf or _h1_en(2025), 2025, "https://example.invalid/ir/h1-2025.pdf"),
            "dopo": lato("h1_2026.pdf", dopo_pdf, 2026, url_dopo)}


PROFILO = {"ticker": "ZZCV.MI", "emittente_id": "lei:ZZCVALFA000000000000", "lingua": "en", "tipo": "semestrale",
           "perimetro": "consolidato", "fonti": ["ir"], "verifica": {"emittente": REGOLA}}


def _solo_debito(anno, valore, valuta="(€m)"):
    return _pdf([[EMITTENTE, F], ["CONSOLIDATED FINANCIAL HIGHLIGHTS", valuta,
                                  ("", [f"June 30, {anno}", f"December 31, {anno - 1}"]),
                                  ("Net financial debt", [valore, "2,594"])]])


def test_confronto_dalla_semestrale_precedente_solo_se_stessa_valuta(tmp_path):
    numeri = sp.numeri_semestrale(_coppia(tmp_path, _solo_debito(2026, "2,861"), _solo_debito(2025, "3,247")),
                                  profilo=PROFILO, fino_al="2026-10-10")
    debito = numeri["voci"][0]
    assert debito["prima"] == 3247 * 10 ** 6 and "h1-2025.pdf" in debito["fonte_prima"] and debito["segno"]
    numeri = sp.numeri_semestrale(_coppia(tmp_path, _solo_debito(2026, "2,861"), _solo_debito(2025, "3,247", "(US$m)")),
                                  profilo=PROFILO, fino_al="2026-10-10")
    assert numeri["voci"][0]["prima"] is None  # valuta diversa: non e' la stessa voce


def test_numeri_semestrale_rifiuti_dichiarati(tmp_path):
    altro = sp.numeri_semestrale(_coppia(tmp_path, _h1_en(emittente="Rame Immaginario Beta S.p.A.")),
                                 profilo=PROFILO, fino_al="2026-10-10")
    assert altro["stato"] == "rifiutato" and altro["voci"] == [] and "altro emittente" in altro["motivo"]
    coppia = _coppia(tmp_path, _h1_en())
    esterno = sp.numeri_semestrale(coppia, profilo={**PROFILO, "documenti_esterni": {
        coppia["dopo"]["url"]: "cdn generico"}}, fino_al="2026-10-10")
    assert esterno["stato"] == "non_disponibile" and "fonte esterna" in esterno["motivo"]
    coppia["dopo"]["prove_verifica"] = {}
    senza = sp.numeri_semestrale(coppia, profilo=PROFILO, fino_al="2026-10-10")
    assert senza["stato"] == "non_disponibile" and "prova d'identita'" in senza["motivo"]


def test_comparativo_rideterminato_dichiarato(tmp_path):
    numeri = sp.numeri_semestrale(_coppia(tmp_path, _h1_en()), profilo=PROFILO, fino_al="2026-10-10")
    # nella semestrale 2025 (stessa fixture) i ricavi correnti sono 7,413: il comparativo 2026 (6,958) vince
    rid = {r["voce"]: r for r in numeri["rideterminazioni"]}
    assert rid["ricavi"]["valore_usato"] == 6958 * 10 ** 6 and rid["ricavi"]["valore_precedente"] == 7413 * 10 ** 6
    assert "data di pubblicazione ignota" in numeri["avvisi"][0]


def test_cablaggio_pipeline_e_riga_per_gli_agenti(tmp_path, monkeypatch):
    from bellomberg.market_data import filing_pipeline
    from bellomberg.agents.filing_context import _riga_numeri
    coppia = _coppia(tmp_path, _h1_en())
    annuali = {"stato": "ok", "voci": [], "fonte": "ESEF xBRL-JSON (ifrs-full)", "variante": "annuale"}

    def singolo(profilo, **kw):
        return {"ticker": profilo["ticker"], "stato": "ok", "motivi": [], "candidati": [], "coppia": coppia,
                "confronto_corrente": {"stato": "ok"}, "confronto_storico": None}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", singolo)
    out = filing_pipeline.esegui_profilo(PROFILO, archivio=str(tmp_path), oggi=date(2026, 10, 10))
    numeri = out["numeri"]
    assert numeri["origine"] == "pdf_semestrale" and numeri["stato"] == "ok"
    ricavi = next(v for v in numeri["voci"] if v["voce"] == "ricavi")
    assert ricavi["periodi"] == {"prima": {"fine": "2025-06-30", "inizio": "2025-01-01"},
                                 "dopo": {"fine": "2026-06-30", "inizio": "2026-01-01"}}
    ebitda = next(v for v in numeri["voci"] if v["voce"] == "ebitda_rettificato")
    assert ebitda["tipo_periodo"] == "durata" and ebitda["periodi"]["dopo"]["inizio"] == "2026-01-01"
    riga = _riga_numeri(numeri)
    assert "ricavi +6.5%" in riga.replace(",", ".") and "PDF relazione semestrale" in riga
    assert "rettificato" in riga and "levered" not in riga and "come stampata" in riga  # misura e definizione
    assert "semestrale corrente" in riga and "companyfacts" not in riga  # rideterminazione con la sua origine
    # numeri annuali gia' presenti da un'altra variante: restano accanto e la riga lo dice
    out2 = {"coppia": coppia, "numeri": annuali}
    filing_pipeline._numeri_semestrale_pdf(PROFILO, out2, "2026-10-10")  # `oggi` anche come stringa ISO
    assert out2["numeri"]["origine"] == "pdf_semestrale" and out2["numeri_annuali"] is annuali
    assert "numeri annuali disponibili (variante annuale)" in _riga_numeri(out2["numeri"], out2["numeri_annuali"])
    # numeri gia' calcolati sulla STESSA coppia (XBRL): non si toccano
    stessi = {"stato": "ok", "voci": [], "fonte": "iXBRL"}
    out5 = {"coppia": coppia, "numeri": stessi}
    filing_pipeline._numeri_semestrale_pdf(PROFILO, out5, date(2026, 10, 10))
    assert out5["numeri"] is stessi and "numeri_annuali" not in out5
    # semestrale illeggibile: l'annuale resta e il motivo si dichiara accanto, anche nella riga
    out3 = {"coppia": _coppia(tmp_path, _h1_en(emittente="Rame Immaginario Beta S.p.A.")), "numeri": annuali}
    filing_pipeline._numeri_semestrale_pdf(PROFILO, out3, date(2026, 10, 10))
    assert out3["numeri"] is annuali and out3["numeri_semestrale"]["stato"] == "rifiutato"
    assert "semestrale PDF: n.d." in _riga_numeri(out3["numeri"], None, out3["numeri_semestrale"])
    # senza numeri prima: il motivo diventa la riga dichiarata per gli agenti
    out4 = {"coppia": _coppia(tmp_path, _h1_en(emittente="Rame Immaginario Beta S.p.A."))}
    filing_pipeline._numeri_semestrale_pdf(PROFILO, out4, date(2026, 10, 10))
    assert "altro emittente" in _riga_numeri(out4["numeri"])
