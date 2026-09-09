# -*- coding: utf-8 -*-
"""I legami fra i numeri di RISCHIO del mandato (criterio (5), lotto B, difetto 4 della
coda di A2 — MASTER §9-sexoctogies).

Cosa deve essere vero per chi usa il programma:
  - uno stress «2008» E' un drawdown: dichiarare «non voglio perdere piu' del 20%» e
    insieme «nello stress 2008 metto in conto il 30%» sono due frasi che si smentiscono,
    e il programma non deve accettarle zitte;
  - il caso UGUALI (perdo al massimo il 20%, e nello stress arrivo al 20%) e' lecito:
    e' il profilo di esempio del repo, e rifiutarlo farebbe cadere ogni test della suite.

Sul legame VOLATILITA' TARGET <-> VaR (difetto 3) il PM ha scelto il 06/09 la **banda
larga**: si rifiuta solo quando la volatilita' implicita dal VaR supera **1,5 volte** il
target dichiarato, cioe' l'incoerenza grossa (chi dichiara «voglio poca volatilita'» e poi
mette in conto una perdita giornaliera da portafoglio molto piu' mosso). Una regola piu'
stretta avrebbe rifiutato mandati coerenti con una prudenza dichiarata al ribasso.

Tutti i valori qui sotto sono INVENTATI: il file di casa non entra mai in un test.
"""
import copy

import pytest

import bellomberg.core.mandato_pm as mp


def _con_rischio(**valori):
    m = copy.deepcopy(mp.profilo_esempio())
    m["rischio"].update(valori)
    return m


def test_il_drawdown_massimo_sotto_lo_stress_viene_rifiutato():
    """«Non perdo piu' del 20%» + «nello stress perdo il 30%»: la seconda frase sfonda la
    prima. Oggi passava senza una parola."""
    m = _con_rischio(var99_1g_pct=1, drawdown_max_pct=20, stress_gfc_pct=30)
    _, errori = mp.valida(m)
    testo = "\n".join(errori)
    assert errori, "drawdown massimo 20 con stress 30: accettato senza dire niente"
    assert "drawdown_max_pct" in testo, errori
    assert "30" in testo and "20" in testo, ("l'errore deve dire i due numeri", errori)
    assert all(":" in e for e in errori), "forma «campo: motivo»"


def test_lo_stress_uguale_al_drawdown_massimo_e_lecito():
    """Il caso al limite, ed e' quello del PROFILO DI ESEMPIO del repo (20 e 20): una regola
    stretta («maggiore», non «maggiore o uguale») lo rifiuterebbe e con esso cadrebbe la
    fixture di sessione che salva l'esempio, cioe' TUTTA la suite."""
    m = _con_rischio(var99_1g_pct=1, drawdown_max_pct=20, stress_gfc_pct=20)
    _, errori = mp.valida(m)
    assert errori == [], errori


def test_il_profilo_di_esempio_del_repo_resta_valido():
    """La prova diretta di cio' che il test qui sopra protegge: l'esempio come sta sul disco."""
    _, errori = mp.valida(mp.profilo_esempio())
    assert errori == [], errori


def test_lo_stress_sotto_il_drawdown_massimo_e_lecito():
    """Il verso normale: metto in conto il 40% di perdita massima e nello stress il 30%."""
    m = _con_rischio(var99_1g_pct=1, drawdown_max_pct=40, stress_gfc_pct=30)
    _, errori = mp.valida(m)
    assert errori == [], errori


@pytest.mark.parametrize("dd, stress, deve_scattare", [
    (20, 30, True),    # lo stress sfonda il drawdown dichiarato
    (20, 21, True),    # di un solo punto: scatta lo stesso
    (20, 20, False),   # uguali: lecito
    (40, 30, False),   # verso normale
    (30, 25, False),   # verso normale, distanza larga
])
def test_il_legame_vale_su_piu_valori_non_su_uno(dd, stress, deve_scattare):
    """Il legame fra i due numeri sta in una regola, non in un caso fortunato: provato su
    cinque coppie, comprese le due che stanno a un punto dal confine."""
    m = _con_rischio(var99_1g_pct=1, drawdown_max_pct=dd, stress_gfc_pct=stress)
    _, errori = mp.valida(m)
    scattato = any("drawdown_max_pct" in e for e in errori)
    assert scattato is deve_scattare, (dd, stress, errori)


# ------------------------------------- volatilita' target contro VaR (difetto 3)

def test_la_volatilita_implicita_dal_var_e_una_funzione_pubblica():
    """Serve anche alla pagina Mandato e all'anteprima, non solo alla validazione:
    chi compila deve poter vedere che numero sta implicando col suo VaR."""
    assert hasattr(mp, "vol_annua_implicita"), "manca la funzione che traduce il VaR in volatilita'"
    # raddoppiando il VaR raddoppia la volatilita' implicita: e' una scalatura lineare
    assert mp.vol_annua_implicita(4) == pytest.approx(2 * mp.vol_annua_implicita(2))
    # e un VaR non dichiarato non produce un numero inventato
    assert mp.vol_annua_implicita(None) is None


def test_un_var_incoerente_col_target_di_volatilita_viene_rifiutato():
    """Il caso per cui il difetto e' stato aperto: «voglio poca volatilita'» e insieme una
    perdita giornaliera da portafoglio molto piu' mosso."""
    m = _con_rischio(volatilita_target_pct=5, var99_1g_pct=15,
                     drawdown_max_pct=60, stress_gfc_pct=50)
    _, errori = mp.valida(m)
    testo = "\n".join(errori)
    assert errori, "vol target 5% con VaR99 1g 15%: accettato senza dire niente"
    assert "volatilita_target_pct" in testo, errori
    assert all(":" in e for e in errori), "forma «campo: motivo»"


def test_una_prudenza_dichiarata_al_ribasso_resta_lecita():
    """La banda e' LARGA per scelta del PM (06/09): un target dichiarato piu' basso della
    volatilita' implicita dal VaR non e' un'incoerenza finche' resta entro 1,5 volte."""
    m = _con_rischio(volatilita_target_pct=20, var99_1g_pct=4,
                     drawdown_max_pct=30, stress_gfc_pct=25)
    _, errori = mp.valida(m)
    assert errori == [], errori


@pytest.mark.parametrize("rapporto, deve_scattare", [
    (1.00, False),   # implicita = target
    (1.40, False),   # dentro la banda
    (1.49, False),   # appena dentro
    (1.51, True),    # appena fuori
    (3.00, True),    # incoerenza grossa
])
def test_la_banda_e_a_una_volta_e_mezza(rapporto, deve_scattare):
    """La soglia sta in una REGOLA provata su piu' valori, non in un caso fortunato, e i
    due punti che contano sono quelli a cavallo del confine.

    Il rapporto ESATTAMENTE 1,5 non e' fra i casi, e non per pigrizia: il giro
    volatilita' -> VaR -> volatilita' passa da `sqrt(252)` e non torna al bit, quindi
    «esattamente al confine» non e' raggiungibile per costruzione e un test che lo
    pretendesse misurerebbe l'errore di arrotondamento, non la regola. Che il confine sia
    INCLUSO si legge dal confronto stretto (`>`) in `mandato_pm.valida`, ed e' la
    differenza fra 1,49 e 1,51 a provare che la soglia sta li' e non altrove."""
    target = 20.0
    # il VaR che produce esattamente quel rapporto, invertendo la formula
    var = mp.var_da_vol_annua(target * rapporto)
    # drawdown e stress DENTRO i loro intervalli e coerenti fra loro: con valori fuori
    # intervallo `valida` esce PRIMA delle coerenze e questo test misurerebbe la fixture
    # invece della regola (preso in flagrante il 06/09 con 90/80).
    m = _con_rischio(volatilita_target_pct=target, var99_1g_pct=round(var, 4),
                     drawdown_max_pct=70, stress_gfc_pct=60)
    _, errori = mp.valida(m)
    altri = [e for e in errori if "volatilita_target_pct" not in e]
    assert altri == [], ("la fixture non deve produrre altri errori, o il test misura "
                        "quelli invece della regola", altri)
    scattato = any("volatilita_target_pct" in e for e in errori)
    assert scattato is deve_scattare, (rapporto, var, errori)


def test_l_errore_dice_i_due_numeri_che_non_tornano():
    """Chi legge deve capire COSA cambiare: il numero implicito e quello dichiarato."""
    m = _con_rischio(volatilita_target_pct=5, var99_1g_pct=15,
                     drawdown_max_pct=60, stress_gfc_pct=50)
    _, errori = mp.valida(m)
    riga = [e for e in errori if "volatilita_target_pct" in e][0]
    assert "5" in riga, riga
    assert "%" in riga, riga
