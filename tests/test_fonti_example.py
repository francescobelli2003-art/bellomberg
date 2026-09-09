"""Fase 1 della migrazione fonti IR: l'esempio viaggia nel repo pubblico, il
negozio vero no. Simboli, nomi e domini INVENTATI: tests/ e' dentro il perimetro
pubblico.

L'esempio ci viaggia DAVVERO da questo fix: `fonti_guidance.example.json` e' stato
aggiunto a `pubblico/ALLOWLIST.txt`; prima il test usciva e l'esempio no, e nel tree
pubblico queste prove cadevano con FileNotFoundError. Quel legame lo asserisce
`test_esempio_SELEZIONATO_dall_export`, che nel tree pubblico SALTA con il motivo
dichiarato invece di leggere una lista che li' non c'e'."""
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESEMPIO_REL = "src/bellomberg/resources/examples/fonti_guidance.example.json"
ESEMPIO = os.path.join(REPO, *ESEMPIO_REL.split("/"))
CAMPI_MINIMI = {"nome", "tipo"}
# BETA.DE si e' aggiunta col Task 4: senza una voce che porti la data COMPLETA
# (data + fonte + giorno di verifica) il test d'igiene della data nuda, in un clone
# dove il negozio privato non c'e', non avrebbe nessuna data da controllare e
# passerebbe a vuoto. Simbolo e nome restano inventati come gli altri due.
SIMBOLI_INVENTATI = {"ACME.MI", "BETA.DE", "ORO.MI"}
NOMI_INVENTATI = {"Acme Manifatture SpA", "Beta Industrie AG", "Oro Fisico ETC"}
CHIAVI_SERVIZIO = {"_leggimi"}


def _esempio():
    return json.load(open(ESEMPIO, encoding="utf-8"))


def _voci(d):
    """Le voci vere: le chiavi con `_` davanti sono servizio, non simboli."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _host(valore):
    return (urlparse(valore).hostname or "") if isinstance(valore, str) else ""


def test_esempio_parsa_ed_e_un_dizionario_di_voci():
    d = _esempio()
    assert isinstance(d, dict) and d, "l'esempio non e' un dizionario non vuoto"
    voci = _voci(d)
    assert voci, "l'esempio non ha nessuna voce: chi lo copia non vede lo schema"
    for tk, v in voci.items():
        assert CAMPI_MINIMI <= set(v), f"{tk}: campi minimi mancanti"


def test_esempio_mostra_almeno_una_voce_applicabile():
    """Un esempio di sole voci non applicabili (nessuna trimestrale da attendere) non
    insegna lo schema pieno: ci vuole almeno una voce con una fonte IR da interrogare."""
    voci = _voci(_esempio())
    applicabili = [tk for tk, v in voci.items()
                   if v.get("tipo") == "equity" and v.get("ir_url")]
    assert applicabili, "nessuna voce applicabile (tipo equity con ir_url): esempio degenere"


def test_esempio_non_contiene_dati_veri():
    """L'esempio e' pubblico: simboli, nomi e domini devono essere inventati. Il vettore
    realistico e' il copia-incolla da `fonti_guidance.py`, che di nomi e URL veri ne ha
    molti. Regole MISURABILI, non una lista di divieti:
      · le voci sono ESATTAMENTE i simboli di `SIMBOLI_INVENTATI` (l'uguaglianza vieta
        sia un simbolo vero in piu' sia un esempio svuotato). La docstring nomina la
        costante e non piu' il loro NUMERO: quando il Task 4 ne ha aggiunto un terzo,
        contarli qui aveva gia' reso falsa questa frase;
      · le chiavi di servizio sono solo quelle note: un ticker vero sa nascondersi
        dietro un underscore (la forma e' `_BETA.DE`, scritta con un simbolo
        INVENTATO: fino al 04/09 qui c'era una base vera del book, e l'esempio di
        un vettore non deve ESSERE il vettore) e il filtro sulle voci lo salterebbe;
      · ogni campo `*_url` VALORIZZATO e ogni valore che contenga uno schema `://` deve
        stare sotto `.invalid`, il TLD che la RFC 2606 riserva e che nessuna societa'
        potra' avere. «Valorizzato» non e' una sfumatura: i campi a `null` il codice li
        SALTA (v. il commento dentro il ciclo), e fino al 04/09 questo trattino diceva
        «ogni campo `*_url`» senza dirlo — una promessa piu' larga della misura;
      · i `nome` sono esattamente quelli di `NOMI_INVENTATI`.
    """
    d = _esempio()
    voci = _voci(d)
    assert set(voci) == SIMBOLI_INVENTATI, set(voci)
    assert set(d) - set(voci) <= CHIAVI_SERVIZIO, set(d) - set(voci)

    nomi = set()
    for tk, v in voci.items():
        nomi.add(v.get("nome"))
        for campo, val in v.items():
            if val is None:
                # `null` = campo dichiarato assente: forma legittima dello schema, che
                # l'esempio usa gia' per next_report_date/verificato_il e che il Task 2
                # usera' per le voci senza fonte. Senza questo salto un `ir_url: null`
                # cadrebbe con un messaggio fuorviante («non e' un URL sotto .invalid (None)»).
                continue
            if campo.endswith("_url"):
                assert _host(val).endswith(".invalid"), \
                    f"{tk}.{campo}: non e' un URL sotto .invalid ({val!r})"
            if isinstance(val, str) and "://" in val:
                assert _host(val).endswith(".invalid"), \
                    f"{tk}.{campo}: URL verso un dominio vero ({val!r})"
    assert nomi == NOMI_INVENTATI, nomi


def test_esempio_SELEZIONATO_dall_export():
    """Il buco lasciato aperto dal fix round 1: se qualcuno toglie l'esempio da
    `pubblico/ALLOWLIST.txt`, il test esce nel repo pubblico e il suo file no, e le prove
    qui sopra cadono in FileNotFoundError SOLO nella CI pubblica — muto, di la' dal
    cancello. Ma chi toglie quella riga la toglie nel repo PRIVATO e lancia la suite
    privata: e' qui che la guardia serve, ed e' qui che sta.

    ESSERE NOMINATI NON E' USCIRE, ed e' la differenza che il round 2 ha lasciato aperta.
    Fino al 04/09 qui si asseriva che la riga fosse NOMINATA dalla lista. Il revisore l'ha
    falsificata senza toccare quella riga: basta aggiungere in coda `!fonti_guidance.example.json`
    (o un negativo che lo peschi per glob) e la riga resta scritta, il file NON esce, la suite
    privata dice 8 passed e il cancello dichiara PULITO. Solo la suite dentro il tree
    esportato se ne accorge, un minuto dopo. Quindi si asserisce cio' che conta davvero —
    che l'export lo SCELGA — con le funzioni VERE del cancello
    (`export_pubblico.file_tracciati` + `export_pubblico.seleziona` sull'allowlist letta dal
    parser vero `verifica_pubblico.leggi_lista`: commenti via, BOM via, positivi e '!'
    applicati come li applica l'export). Riscrivere qui il matching vorrebbe dire misurare
    la MIA regola invece di quella che governa l'export.

    Nel tree pubblico questa prova SALTA con il motivo dichiarato: `pubblico/ALLOWLIST.txt`
    e' la lista di cio' che esce e non esce a sua volta (misurato). Salto dichiarato, non
    fallback muto — stesso pattern di
    `test_red_team_vede_i_report_interi.test_lo_strumento_di_misura_IMPORTA_il_cap...`."""
    allowlist = os.path.join(REPO, "tools", "release", "policy", "ALLOWLIST.txt")
    if not os.path.exists(allowlist):
        pytest.skip("pubblico/ALLOWLIST.txt assente: siamo nel tree pubblico, dove la lista "
                    "di cio' che esce non viene pubblicata (P2)")
    from tools.release import export_pubblico as ep
    from tools.release import verifica_pubblico as vp

    scelti, _senza_riscontro = ep.seleziona(ep.file_tracciati(REPO),
                                            vp.leggi_lista(allowlist))
    assert ESEMPIO_REL in scelti, (
        "l'export NON sceglie %s: uscirebbe il test senza il suo "
        "file e la CI pubblica cadrebbe in FileNotFoundError. La riga puo' benissimo essere "
        "ancora scritta nella lista: a toglierla dall'export basta un '!' che la peschi."
        % ESEMPIO_REL)


def test_esempio_tracciato_e_negozio_ignorato():
    """`git check-ignore` esce 1 anche quando il file NON ESISTE: da solo non prova il
    tracciato, ed e' il motivo per cui questa prova era verde prima ancora che l'esempio
    esistesse. Il tracciato lo misura `git ls-files --error-unmatch`, che esce 0 solo se
    il file e' nell'indice."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8").returncode

    assert git("ls-files", "--error-unmatch", ESEMPIO_REL) == 0, \
        "%s non e' tracciato da git" % ESEMPIO_REL
    assert git("check-ignore", ESEMPIO_REL) == 1, \
        "%s NON deve essere ignorato" % ESEMPIO_REL
    assert git("check-ignore", "data/fonti_guidance.json") == 0, \
        "data/fonti_guidance.json DEVE restare ignorato"
