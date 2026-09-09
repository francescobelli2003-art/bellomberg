"""Tesi delle posizioni: aggiornarle NON deve toccare nient'altro (20/08, Opus 5).

Il difetto trovato durante il gate delle trimestrali arretrate: `add_or_update_position`
aveva `valuta="EUR"` come parametro di DEFAULT e la riscriveva SEMPRE, non solo quando
gliela passavi. `manage_portfolio.py update --tesi` (strumento oggi in attic) — lo strumento documentato per
aggiornare una tesi — la chiama senza valuta: misurato su copia del DB, la tesi di
IOTA.L portava la posizione da GBX a EUR, cioe' un carico in pence letto come se fosse
in euro (circa cento volte il carico vero).
Riguardava tutte le posizioni non-euro del book.

Qui si misura che l'aggiornamento di un campo non ne muove altri, e che le posizioni
NUOVE continuano a nascere in EUR quando la valuta non e' dichiarata.
"""
import pytest

from bellomberg.storage.memory_db import MemoryDB


@pytest.fixture()
def db(tmp_path):
    d = MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                 chroma_path=str(tmp_path / "chroma"))
    # una posizione in GBX come IOTA.L: il caso che si rompeva (lotto FINTO dal
    # 02/09, pubblicazione B9: il test misura l'invarianza, non le cifre; il TESTO
    # della tesi e' inventato dal 03/09, P4 — il precedente era quello vero)
    d.add_or_update_position(ticker="IOTA.L", nome="Fondo Iota plc",
                             quantita=50, prezzo_medio=4321.5,
                             valuta="GBX", tesi="Quota a sconto sul valore degli attivi.")
    return d


def _riga(db, ticker="IOTA.L"):
    with db._conn() as conn:
        return dict(conn.execute("SELECT * FROM positions WHERE ticker=?", (ticker,)).fetchone())


def test_aggiornare_la_tesi_non_tocca_la_valuta(db):
    prima = _riga(db)
    assert prima["valuta"] == "GBX"

    db.add_or_update_position(ticker="IOTA.L", tesi=prima["tesi"] + "\n\n[addendum 20/08/2026]")

    dopo = _riga(db)
    assert dopo["valuta"] == "GBX", "la valuta e' stata riscritta aggiornando la sola tesi"
    assert dopo["quantita"] == prima["quantita"]
    assert dopo["prezzo_medio"] == prima["prezzo_medio"]
    assert dopo["data_apertura"] == prima["data_apertura"]
    assert dopo["is_active"] == prima["is_active"]
    assert "[addendum 20/08/2026]" in dopo["tesi"]


def test_chiudere_una_posizione_non_tocca_la_valuta(db):
    """`manage_portfolio close` (oggi in attic) passava quantita=0 e nient'altro: stessa trappola."""
    db.add_or_update_position(ticker="IOTA.L", quantita=0)
    dopo = _riga(db)
    assert dopo["valuta"] == "GBX"
    assert dopo["is_active"] == 0


def test_valuta_esplicita_viene_scritta(db):
    """La cura non deve rendere impossibile CAMBIARE la valuta quando lo si vuole."""
    db.add_or_update_position(ticker="IOTA.L", valuta="GBP")
    assert _riga(db)["valuta"] == "GBP"


def test_posizione_nuova_senza_valuta_nasce_in_eur(db):
    """Il default dichiarato resta EUR sull'INSERT: una posizione nuova non puo'
    nascere con valuta NULL (il NAV la sommerebbe come se fosse in euro senza dirlo)."""
    db.add_or_update_position(ticker="GAMMA.MI", nome="Gamma SpA", quantita=100, prezzo_medio=14.5)
    assert _riga(db, "GAMMA.MI")["valuta"] == "EUR"


# ---------------------------------------------------------------------------
# update_tesi / get_tesi — il canale che l'app usa per modificare le tesi
# (richiesta PM 20/08: "vorrei vedere e modificare le tesi dall'app")
# ---------------------------------------------------------------------------

# Testo INVENTATO (03/09, P4): fino a oggi qui c'era la tesi VERA del PM, copiata
# verbatim dal DB — B9 aveva riscritto le cifre del lotto ma non il TESTO. La forma
# conta e resta identica: lunga, con un [addendum ...] dentro e una fonte citata,
# perche' i test misurano l'accorciamento sotto meta', lo storico e l'addendum.
TESI_LUNGA = ("Quota a sconto sul valore degli attivi. Tesi di esempio, testo inventato.\n\n"
              "[addendum 01/01/2020 - relazione di esempio letta] Lo sconto e' passato dal "
              "10,0% al 20,0%; le prime due partecipazioni del veicolo hanno perso il 5% e "
              "il 6%. NAV/azione 11,11 $ da 22,22. [src: documento di esempio 01/01/2020]")


def test_update_tesi_scrive_e_conserva_la_precedente(db):
    db.update_tesi("IOTA.L", TESI_LUNGA, autore="app")
    r = db.get_tesi("IOTA.L")
    assert r["tesi"] == TESI_LUNGA
    assert r["versioni"] == 1
    assert "sconto sul valore" in r["storico"][0]["tesi"]
    assert _riga(db)["valuta"] == "GBX"


def test_testo_identico_non_scrive(db):
    db.update_tesi("IOTA.L", TESI_LUNGA)
    r = db.update_tesi("IOTA.L", TESI_LUNGA)
    assert r["ok"] and r["invariata"] and r["scritture"] == 0
    assert db.get_tesi("IOTA.L")["versioni"] == 1     # niente versione doppia


def test_accorciare_sotto_meta_chiede_conferma(db):
    """Il caso vero: il PM apre la tesi nell'app, cancella per sbaglio l'addendum
    e salva. Senza conferma non passa, e il messaggio dice dove recuperarlo."""
    db.update_tesi("IOTA.L", TESI_LUNGA)
    r = db.update_tesi("IOTA.L", "Tratta a sconto.")
    assert "error" in r and "GUARDIA TESI" in r["error"]
    assert db.get_tesi("IOTA.L")["tesi"] == TESI_LUNGA        # nessuna scrittura
    # con conferma esplicita passa, e la lunga resta nello storico
    r2 = db.update_tesi("IOTA.L", "Tratta a sconto.", conferma=True)
    assert r2["ok"] and r2["scritture"] == 1
    st = db.get_tesi("IOTA.L")
    assert st["tesi"] == "Tratta a sconto."
    assert any("addendum" in v["tesi"] for v in st["storico"])


def test_svuotare_chiede_conferma(db):
    r = db.update_tesi("IOTA.L", "   ")
    assert "error" in r and "SVUOTANDO" in r["error"]
    assert db.get_tesi("IOTA.L")["tesi"].strip() != ""
    assert db.update_tesi("IOTA.L", "", conferma=True)["ok"]
    assert db.get_tesi("IOTA.L")["tesi"] == ""


def test_allungare_non_chiede_niente(db):
    """Aggiungere un addendum e' il caso normale: nessun attrito."""
    r = db.update_tesi("IOTA.L", TESI_LUNGA)
    assert r["ok"] and r["scritture"] == 1 and "error" not in r


def test_posizione_inesistente_dichiarata(db):
    assert "error" in db.update_tesi("XXXX", "qualcosa")
    assert "error" in db.get_tesi("XXXX")


def test_tesi_non_stringa_rifiutata(db):
    """Un JSON con `tesi: 123` non deve finire nel campo come '123'."""
    assert "error" in db.update_tesi("IOTA.L", 123)
    assert "error" in db.update_tesi("IOTA.L", None)


def test_mojibake_viene_DICHIARATO_non_ingoiato(db):
    """20/08, errore mio in collaudo: la tesi di una posizione italiana e' finita nel DB come
    'scommessa Ã¨ sull'esecuzione' (UTF-8 riletto come latin-1) passando per la
    shell, e nulla l'ha detto — il campo e' libero e il mojibake e' testo valido.
    Non si rifiuta (potrebbe essere una citazione), ma si dichiara."""
    r = db.update_tesi("IOTA.L", "Tratta a sconto: la tesi Ã¨ intatta e la view Ã "
                                " lungo termine resta quella, con margine ampio.")
    assert r["ok"] and r["scritture"] == 1
    assert "avviso_encoding" in r
    assert "latin-1" in r["avviso_encoding"]


def test_testo_pulito_non_genera_avvisi(db):
    """Il rovescio: gli accenti VERI non devono far scattare l'avviso, altrimenti
    e' rumore su ogni tesi scritta in italiano."""
    r = db.update_tesi("IOTA.L", "La società è già a sconto: però la view resta "
                                "quella di prima, perché il NAV non è cambiato.")
    assert r["ok"] and "avviso_encoding" not in r
