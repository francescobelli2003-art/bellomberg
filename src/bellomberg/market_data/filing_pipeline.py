"""I-20: acquisizione e confronto documentale da profili espliciti USA/Europa.

Nessun LLM, DB o scheduler. Le copie scaricate restano nell'archivio scelto
dal chiamante; confronto riuscito e freschezza delle fonti sono esiti distinti.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def _data(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _host(url):
    parts = urlsplit(str(url or ""))
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("URL HTTP(S) senza credenziali richiesto")
    return parts.hostname.lower()


def _scarica(url, archivio, hosts):
    from bellomberg.market_data.lettore_trimestrali import scarica_documento
    if _host(url) not in hosts:
        raise ValueError(f"host documentale non autorizzato: {_host(url)}")
    result = scarica_documento(url, str(archivio), host_consentiti=hosts, public_only=True)
    if result.get("stato") != "ok":
        raise ValueError(result.get("motivo", "download non disponibile"))
    finale = result.get("url_finale") or url
    if _host(finale) not in hosts:
        raise ValueError(f"host redirect non autorizzato: {_host(finale)}")
    return result


INDICE_ESEF = "esef_indice.json"
ORIGINE_IR = "sito_emittente"  # righe IR del run: «sito dell'emittente, non archivio ufficiale (OAM)»


def _scarica_esef(url, archivio, hosts):
    """Download di uno xBRL-JSON con cache nell'archivio: i depositi del repository sono
    immutabili per URL; se il file archiviato esiste e il suo sha256 torna, niente rete."""
    import hashlib
    indice_path = Path(archivio) / INDICE_ESEF
    try:
        indice = json.loads(indice_path.read_text(encoding="utf-8"))
        if not isinstance(indice, dict):
            indice = {}
    except (OSError, ValueError):
        indice = {}
    voce = indice.get(url)
    if isinstance(voce, dict):
        try:
            if hashlib.sha256(Path(voce["path"]).read_bytes()).hexdigest() == voce["sha256"]:
                return {"stato": "ok", "url": url, "path": voce["path"], "sha256": voce["sha256"], "da_archivio": True}
        except (OSError, KeyError, TypeError):
            pass
    snapshot = _scarica(url, archivio, hosts)
    indice[url] = {"path": snapshot["path"], "sha256": snapshot["sha256"]}
    try:
        Path(archivio).mkdir(parents=True, exist_ok=True)
        tmp = indice_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(indice), encoding="utf-8")
        tmp.replace(indice_path)
    except OSError:
        pass  # cache best-effort
    return snapshot


MAX_6K_SONDATI = 80
GIORNI_6K = 800
_PAROLE_RISULTATI = re.compile(r"results?|earnings|interim|half|semi-?annual|quarter|(?<![a-z])[qh][1-4]", re.I)


def _documento_risultati(documenti):
    """Il documento di bilancio di un filing 6-K, o None.

    Prima un documento Inline XBRL (6-K o EX-99), poi uno con parole da risultati
    nel nome del file o nella descrizione. Osservato: le descrizioni SEC spesso
    ripetono solo il tipo (EX-99.1) e alcuni emittenti mettono la relazione nel
    documento principale.
    """
    utili = sorted((d for d in documenti if d.get("tipo", "").upper() == "6-K"
                    or d.get("tipo", "").upper().startswith("EX-99")), key=lambda d: d["seq"])
    ixbrl = [d for d in utili if d.get("ixbrl")]
    if ixbrl:  # copertina e relazione possono essere entrambe iXBRL: la relazione e' la piu' grande
        return max(ixbrl, key=lambda d: (d.get("dimensione", 0), -d["seq"]))
    for d in utili:
        if _PAROLE_RISULTATI.search(d["url"].rsplit("/", 1)[-1] + " " + d.get("descrizione", "")):
            return d
    return None


def _raccogli(profilo, archivio, oggi=None):
    """Cataloghi nominati e singole pagine IR: mai ricerca fuzzy di identita'."""
    candidati, fonti, limiti, calendario = [], [], [], {}
    identita = profilo["emittente_id"]
    cik = profilo.get("cik") or (identita[4:] if identita.startswith("CIK:") else None)
    lei = profilo.get("lei") or (identita[4:] if identita.startswith("LEI:") else None)
    abilitate = profilo.get("fonti")
    if abilitate is not None and (not isinstance(abilitate, list)
                                 or any(x not in ("sec", "esef", "ir") for x in abilitate)):
        raise ValueError("fonti deve essere una lista di sec, esef, ir")
    attiva = lambda nome: abilitate is None or nome in abilitate
    hosts_extra = set()
    for host in profilo.get("host_documenti", []):
        if not isinstance(host, str) or not host or any(c in host for c in "/:@?#"):
            raise ValueError("host_documenti richiede nomi host espliciti, senza URL")
        hosts_extra.add(host.lower())

    def aggiungi(fonte, row, url, hosts, motivo=None):
        candidati.append({"fonte": fonte, "url": url, "catalogo": dict(row),
                          "hosts": hosts, "motivo_preliminare": motivo})

    if attiva("sec") and (cik or profilo.get("sec") is True):
        fonte = {"nome": "SEC EDGAR", "stato": "ok", "motivi": []}
        fonti.append(fonte)
        limiti.append("SEC: catalogo entro la finestra e paginazione dichiarate da get_filing_catalog; non osservazione continua.")
        try:
            from bellomberg.market_data import sec_edgar
            catalogo = (sec_edgar.get_filing_catalog(profilo["ticker"], cik=cik) if cik
                        else sec_edgar.get_filing_catalog(profilo["ticker"]))
            fonte.update(stato=catalogo["stato"], motivi=list(catalogo.get("motivi", [])))
            rows = catalogo.get("documenti")
            if not isinstance(rows, list):
                raise ValueError("catalogo SEC senza elenco documenti")
            forme = profilo.get("forme_sec")
            soglia_6k = (oggi or date.today()).toordinal() - GIORNI_6K
            sondati, senza_risultati, rettifiche_6k = 0, 0, 0
            for row in rows:
                form = str(row.get("form") or "")
                base = form.removesuffix("/A")
                if forme is not None and base not in forme:
                    continue
                if forme is not None and form == "6-K/A":
                    rettifiche_6k += 1  # avvisi rettificati: non sono relazioni da confrontare
                    continue
                # La classificazione del catalogo esclude solo tipi inequivocabilmente
                # diversi. Un 6-K resta candidato: la relazione va provata nei byte.
                if base in ("10-K", "20-F", "40-F") and profilo["tipo"] != "annuale":
                    continue
                if base == "10-Q" and profilo["tipo"] == "annuale":
                    continue
                if base == "6-K" and forme is not None and not form.endswith("/A"):
                    # Profili automatici: dal filing si prende il solo documento di
                    # bilancio; avvisi e comunicati non diventano candidati.
                    depositato = _data(row.get("filed_date"))
                    if not depositato or depositato.toordinal() < soglia_6k:
                        continue
                    if sondati >= MAX_6K_SONDATI:
                        limiti.append(f"6-K: esaminati i {MAX_6K_SONDATI} piu' recenti degli ultimi {GIORNI_6K} giorni; gli altri non sondati.")
                        break
                    sondati += 1
                    try:
                        documento = _documento_risultati(sec_edgar.allegati_filing(cik, row["accession"]))
                    except Exception as exc:
                        fonte["motivi"].append(f"6-K {row.get('accession')}: indice del filing non letto: {type(exc).__name__}: {exc}")
                        fonte["indici_non_letti"] = fonte.get("indici_non_letti", 0) + 1
                        continue
                    if not documento:
                        senza_risultati += 1
                        continue
                    row = {**row, "url": documento["url"], "documento_sec": documento["tipo"],
                           "documento_seq": documento.get("seq")}
                motivo = "rettifica /A esclusa: versione corrente da verificare" if form.endswith("/A") else None
                aggiungi("SEC EDGAR", row, row.get("url"),
                         hosts_extra | {"sec.gov", "www.sec.gov", "data.sec.gov"}, motivo)
            if rettifiche_6k:
                limiti.append(f"6-K/A (rettifiche) esclusi: {rettifiche_6k}.")
            if senza_risultati:
                limiti.append(f"6-K senza documento di risultati: {senza_risultati} (avvisi e comunicati non di bilancio, esclusi).")
            if forme == ["6-K"]:
                limiti.append("6-K non riconosciuti come relazione del tipo richiesto: non applicabili, non bloccano il confronto.")
        except Exception as exc:
            fonte.update(stato="errore")
            fonte["motivi"].append(f"{type(exc).__name__}: {exc}")
    elif attiva("sec") and abilitate is not None and "sec" in abilitate:
        fonti.append({"nome": "SEC EDGAR", "stato": "non_disponibile",
                      "motivi": ["SEC richiesta senza CIK esplicito o sec=True; lookup non eseguito"]})

    if attiva("esef") and lei and profilo.get("esef_modo") == "blocchi":
        # Fase B: text block dello xBRL-JSON; catalogo con cache, lingua, un caricamento per esercizio.
        fonte = {"nome": "ESEF", "stato": "ok", "motivi": []}
        fonti.append(fonte)
        limiti.append("ESEF: repository filings.xbrl.org non esaustivo; solo relazioni annuali; text block IFRS "
                      "(obbligatori dal FY2022), relazione sulla gestione non etichettata.")
        try:
            from bellomberg.market_data.filing_esef import righe_catalogo
            # Inglese se c'e' in entrambi gli anni (decisione dell'utente), qualunque lingua avesse il profilo.
            catalogo_esef = righe_catalogo(lei, "en", oggi=oggi or date.today())
            limiti.extend(catalogo_esef["limiti"])
            fonte["motivi"].extend(catalogo_esef["motivi"])
            fonte["lingua"] = catalogo_esef["lingua"]
            fonte["fermo"] = catalogo_esef["fermo"]
            if catalogo_esef["origine"] == "cache_scaduta":
                fonte.update(stato="parziale", indici_non_letti=1)  # da ritentare: indice non aggiornato
            if catalogo_esef.get("dal_sito"):
                fonte["dal_sito"] = True  # fase F: pacchetti ufficiali dal sito dell'emittente
            for row in catalogo_esef["righe"]:
                if row.get("origine") == "sito":
                    aggiungi("ESEF", row, row["json_url"], hosts_extra | {urlsplit(row["json_url"]).hostname.lower()})
                else:
                    aggiungi("ESEF", row, row["json_url"], hosts_extra | {"filings.xbrl.org"})
        except Exception as exc:
            fonte.update(stato="errore")
            fonte["motivi"].append(f"{type(exc).__name__}: {exc}")
    elif attiva("esef") and lei:
        fonte = {"nome": "ESEF", "stato": "ok", "motivi": []}
        fonti.append(fonte)
        limiti.append("ESEF: repository filings.xbrl.org non esaustivo; annuali mancanti e intermedi richiedono fonti IR.")
        try:
            from bellomberg.market_data.esef import _list_filings, attendi_esef
            for row in _list_filings(lei, ritmo=attendi_esef):  # REV_G2a R-3: anche qui il ritmo comune
                url = row.get("report_url")
                motivo = None if url else "report HTML assente; ZIP/OCR non implementati"
                aggiungi("ESEF", row, url or row.get("package_url"),
                         hosts_extra | {"filings.xbrl.org"}, motivo)
        except Exception as exc:
            fonte.update(stato="errore")
            fonte["motivi"].append(f"{type(exc).__name__}: {exc}")
    elif attiva("esef") and abilitate is not None and "esef" in abilitate:
        fonti.append({"nome": "ESEF", "stato": "non_disponibile",
                      "motivi": ["LEI esplicito mancante; resolve_lei non eseguito"]})

    if attiva("ir"):
        urls = profilo.get("ir_urls")
        if urls is None:
            try:
                from bellomberg.market_data.fonti_guidance import fonte_per
                censita = fonte_per(profilo["ticker"])
                calendario = {k: censita[k] for k in ("next_report_date", "next_report_source", "verificato_il") if censita.get(k)}
                urls = [censita["ir_url"]] if censita.get("stato") == "ok" and censita.get("ir_url") else []
                if not urls:
                    fonti.append({"nome": "IR", "stato": "non_disponibile", "motivi": [
                        censita.get("motivo", "URL IR censito non disponibile")]})
            except Exception as exc:
                urls = []
                fonti.append({"nome": "IR", "stato": "errore", "motivi": [f"{type(exc).__name__}: {exc}"]})
        if not isinstance(urls, list) or any(not isinstance(x, str) for x in urls):
            raise ValueError("ir_urls deve essere una lista di URL espliciti")
        hosts_ir = hosts_extra | {_host(url) for url in urls}
        if urls:
            limiti.append("IR: copertura della singola pagina osservata e dei suoi link candidati, non dell'intero sito.")
        from bellomberg.market_data.lettore_trimestrali import candidati_comunicato, estrai_testo
        dal_sito = profilo.get("origine_collegamento") == ORIGINE_IR
        nav_sito = {}
        for url in dict.fromkeys(urls):
            fonte = {"nome": "IR", "url": url, "stato": "ok", "motivi": []}
            fonti.append(fonte)
            if dal_sito:
                # Revisione R-8 C4: profili creati dal sito dell'emittente (PDF diretti): robots.txt
                # riletto a ogni run (5xx/irraggiungibile = vietato, 401/403 = sito che blocca i bot)
                # e una sola GET per PDF (niente snapshot preliminare: la scarica la verifica sotto).
                from bellomberg.market_data import esef_sito
                dominio = esef_sito.dominio_registrabile(_host(url)) or _host(url)
                nav = nav_sito.setdefault(dominio, esef_sito.Navigatore(dominio))
                try:
                    consentito = nav.consentito(url)
                except Exception as exc:
                    consentito = False
                    nav.robots_ignoto[_host(url)] = esef_sito.motivo_eccezione(exc, 100)
                if not consentito:
                    stato, ignoto = nav.bloccato.get(_host(url)), nav.robots_ignoto.get(_host(url))
                    fonte.update(stato="non_disponibile")
                    fonte["motivi"].append(f"{esef_sito.frase_blocco(stato)} su robots.txt" if stato else
                                           f"robots.txt non leggibile ({ignoto}): documento non scaricato" if ignoto
                                           else f"robots.txt vieta {urlsplit(url).path[:80]}: documento non scaricato")
                    continue
                aggiungi("IR", {"origine": ORIGINE_IR}, url, hosts_ir)
                continue
            try:
                snapshot = _scarica(url, archivio, hosts_ir)
                fonte["snapshot"] = snapshot
                estratto = estrai_testo(snapshot["path"])
                if estratto.get("stato") != "ok":
                    raise ValueError(estratto.get("motivo", "pagina IR illeggibile"))
                # V8B 05/10 (decisione PM): ogni documento IR viene dal sito dell'emittente, non da un
                # archivio ufficiale: l'origine arriva dichiarata nella riga del run (chiave «origine»)
                if estratto.get("formato") == "pdf":
                    aggiungi("IR", {"origine": ORIGINE_IR}, url, hosts_ir)
                    continue
                html = Path(snapshot["path"]).read_bytes().decode(estratto.get("codifica", "utf-8-sig"))
                links = candidati_comunicato(html, snapshot.get("url_finale") or url)
                if not links:
                    fonte.update(stato="parziale")
                    fonte["motivi"].append("nessun link candidato nella pagina IR osservata")
                for link in links:
                    aggiungi("IR", {"pagina_ir": url, "testo_link": link.get("testo"), "origine": ORIGINE_IR},
                             link["url"], hosts_ir)
            except Exception as exc:
                fonte.update(stato="errore")
                fonte["motivi"].append(f"{type(exc).__name__}: {exc}")
    return candidati, fonti, limiti, calendario


def _riassunto(doc, candidato):
    return {"url": doc["url"], "path": candidato.get("path"), "sha256": doc["sha256"],
            "metadati": doc["metadati"], "prove_verifica": doc["prove_verifica"],
            "filed_date": candidato.get("filed_date")}


def _omologhi(prima, dopo):
    a, b = prima["metadati"], dopo["metadati"]
    if any(a.get(k) != b.get(k) for k in ("emittente_id", "lingua", "tipo", "perimetro")):
        return False
    ai, af, bi, bf = (_data(m[k]) for m, k in ((a, "periodo_inizio"), (a, "periodo_fine"),
                                               (b, "periodo_inizio"), (b, "periodo_fine")))
    return (all((ai, af, bi, bf)) and 350 <= (bi - ai).days <= 380
            and 350 <= (bf - af).days <= 380
            and abs((af - ai).days - (bf - bi).days) <= 14)


# Fase F: emittente SEC nuovo senza lo stesso trimestre dell'anno prima.
SEQUENZIALE_LIMITE = ("confronto trimestre su trimestre: manca lo stesso periodo dell'anno prima "
                      "(stagionalita' diversa)")


def _sequenziale(profilo, unici, candidati):
    """Trimestre immediatamente precedente, se l'emittente SEC e' nuovo; altrimenti None.

    Solo profili SEC automatici, variante 10-Q: la variante 10-K confronta gia' esercizi
    consecutivi (il precedente e' l'omologo) e un 10-K non si confronta con un 10-Q. Il
    trimestre fiscale coperto dal 10-K resta fuori: il primo 10-Q dopo un 10-K non ha un
    trimestre precedente adiacente e resta senza confronto. Vale solo se il catalogo non
    ha alcun deposito (anche non verificato) chiuso circa un anno prima: appena l'omologo
    esiste torna la regola anno su anno, senza flag da togliere. Mai profili manuali,
    proposte AI, ESEF o IR.
    """
    if (profilo.get("origine_collegamento") in (None, "manuale", "proposta_ai")
            or profilo.get("fonti") != ["sec"] or profilo.get("forme_sec") != ["10-Q"]
            or profilo.get("tipo") != "trimestrale" or len(unici) < 2):
        return None
    dopo, (prima, c_prima) = unici[0][0], unici[1]
    a, b = prima["metadati"], dopo["metadati"]
    if any(a.get(k) != b.get(k) for k in ("emittente_id", "lingua", "tipo", "perimetro")):
        return None
    ai, af, bi, bf = (_data(m.get(k)) for m, k in ((a, "periodo_inizio"), (a, "periodo_fine"),
                                                   (b, "periodo_inizio"), (b, "periodo_fine")))
    if not all((ai, af, bi, bf)):
        return None
    for c in candidati:
        fine = _data(c.get("report_date") or c.get("period_end") or (c.get("metadati") or {}).get("periodo_fine"))
        if fine and 350 <= (bf - fine).days <= 380:
            return None  # omologo atteso (anche se non verificato): niente scorciatoia
    # trimestri adiacenti della stessa durata: il successivo comincia dove finisce il precedente
    if not (80 <= (bf - af).days <= 100 and 0 < (bi - af).days <= 7
            and abs((af - ai).days - (bf - bi).days) <= 14):
        return None
    return prima, c_prima


def _freschezza(profilo, calendario, verificati, oggi):
    # Una data sovrascritta nel profilo non eredita la prova di un'altra data
    # del negozio: calendario e citazione si scelgono come un unico gruppo.
    campi = ("next_report_date", "next_report_source", "verificato_il")
    origine = profilo if any(k in profilo for k in campi) else calendario
    prossima, fonte, certificata = (origine.get(k) for k in campi)
    fini = [doc["metadati"]["periodo_fine"] for doc, _ in verificati]
    out = {"stato": "n.d.", "checked_at": oggi.isoformat(),
           "ultimo_periodo": max(fini) if fini else "n.d.",
           "next_report_date": prossima or "n.d.", "next_report_source": fonte or "n.d.",
           "verificato_il": certificata or "n.d.",
           "motivi": ["Ultimo periodo osservato e diff riuscito non provano l'assenza di nuovi filing."]}
    attesa = _data(prossima)
    verifica_il = _data(certificata)
    try:
        fonte_valida = bool(fonte and _host(fonte))
    except ValueError:
        fonte_valida = False
    if not attesa or not fonte_valida or not verifica_il or verifica_il > oggi:
        out["motivi"].append("Calendario non documentato: servono next_report_date valida, next_report_source HTTP(S) e verificato_il valido non futuro; freschezza n.d.")
    else:
        if attesa <= oggi and not any(_data(c.get("filed_date")) and _data(c["filed_date"]) >= attesa
                                     for _, c in verificati):
            out["stato"] = "stale"
            out["motivi"].append("Data attesa trascorsa senza prova di documento verificato pubblicato da quella data; date IR/ESEF non note restano n.d.")
        else:
            out["stato"] = "non_garantita"
    return out


def _esegui_singolo(profilo, *, archivio, oggi=None, max_documenti=20):
    """Seleziona l'ultimo periodo verificato e l'omologo dell'anno precedente.

    Eccezione (fase F): profilo SEC automatico 10-Q di un emittente nuovo, senza omologo
    atteso nel catalogo: trimestre precedente adiacente, ``coppia["regola"] = "sequenziale"``.
    ``fonti`` puo' limitare i canali a sec/esef/ir. SEC richiede un CIK
    esplicito o ``sec=True``; ESEF richiede sempre un LEI esplicito.
    ``host_documenti`` aggiunge host autorizzati a quelli delle pagine IR
    esplicite e ai domini istituzionali del catalogo selezionato.
    ``oggi`` e' la data di esecuzione/valutazione della freschezza: non
    ricostruisce fonti disponibili nel passato e non abilita un backtest.
    """
    oggi = _data(oggi) if oggi is not None else date.today()
    if oggi is None:
        raise ValueError("oggi deve essere una data ISO valida")
    if isinstance(max_documenti, bool) or not isinstance(max_documenti, int) or max_documenti < 1:
        raise ValueError("max_documenti deve essere un intero positivo")
    if not isinstance(profilo, dict) or any(not isinstance(profilo.get(k), str) or not profilo[k].strip()
                                          for k in ("ticker", "emittente_id", "lingua", "tipo", "perimetro")):
        raise ValueError("profilo incompleto: ticker, emittente_id, lingua, tipo e perimetro obbligatori")
    out = {"ticker": profilo["ticker"], "stato": "non_disponibile", "motivi": [],
           "candidati": [], "coppia": None, "confronto_corrente": None,
           "confronto_storico": None, "ultimo_non_verificato": False, "src": "filing_pipeline"}
    from bellomberg.market_data.filing_profili_auto import aggiorna_regole_6k
    profilo, nota_regole_6k = aggiorna_regole_6k(profilo)  # R-FONTI 10/10: regole 6-K automatiche pre-06/10
    candidati, fonti, limiti, calendario = _raccogli(profilo, archivio, oggi)
    if nota_regole_6k:
        limiti.append(nota_regole_6k)
    esef_blocchi = profilo.get("esef_modo") == "blocchi"
    lingua_esef = next((f.get("lingua") for f in fonti if f.get("nome") == "ESEF" and f.get("lingua")), None)
    if esef_blocchi and lingua_esef and lingua_esef != profilo["lingua"]:
        profilo = {**profilo, "lingua": lingua_esef}  # lingua della coppia (inglese se c'e' in entrambi gli anni)
    out["fonti"] = fonti
    out["copertura"] = {"limiti": limiti, "candidati_osservati": len(candidati),
                        "max_documenti": max_documenti, "documenti_tentati": 0,
                        "stato": "limitata" if fonti else "non_disponibile"}
    for fonte in fonti:
        out["motivi"].extend(f"{fonte['nome']}: {m}" for m in fonte["motivi"])
    # Un limite troncato deve rimanere visibile anche se i documenti scelti
    # consentono un ottimo confronto storico.
    candidati.sort(key=lambda c: str(c["catalogo"].get("report_date") or
                                    c["catalogo"].get("period_end") or ""), reverse=True)
    verificati = []
    for item in candidati:
        catalogo = item["catalogo"]
        c = {"fonte": item["fonte"], "url": item["url"], "stato": "non_verificato", "motivi": [],
             **{k: catalogo[k] for k in ("report_date", "period_end", "filed_date", "language", "form", "rettifica", "pagina_ir", "origine") if k in catalogo}}
        out["candidati"].append(c)
        tentato = False
        try:
            lingua_catalogo = catalogo.get("language")
            if (isinstance(lingua_catalogo, str) and lingua_catalogo.strip()
                    and lingua_catalogo.lower().split("-")[0] != profilo["lingua"]):
                c.update(stato="non_applicabile")
                c["motivi"].append(f"lingua catalogo {lingua_catalogo} diversa dal profilo {profilo['lingua']}: documento non scaricato, nessuna traduzione")
                continue
            if item["motivo_preliminare"]:
                raise ValueError(item["motivo_preliminare"])
            if out["copertura"]["documenti_tentati"] >= max_documenti:
                raise ValueError(f"limite max_documenti={max_documenti} raggiunto: candidato non verificato")
            out["copertura"]["documenti_tentati"] += 1
            tentato = True
            esef_doc = esef_blocchi and item["fonte"] == "ESEF"
            if esef_doc and catalogo.get("origine") == "sito":
                from bellomberg.market_data.esef_sito import scarica_pacchetto_json
                snapshot = scarica_pacchetto_json(item["url"], archivio, item["hosts"])
            else:
                snapshot = (_scarica_esef if esef_doc else _scarica)(item["url"], archivio, item["hosts"])
            c.update(path=snapshot["path"], sha256=snapshot["sha256"])
            if esef_doc:
                from bellomberg.market_data.filing_esef import documento_esef
                verifica = documento_esef(snapshot["path"], url=item["url"], profilo=profilo, catalogo=catalogo)
                if verifica.get("stato") == "non_applicabile":  # l'altra lingua dello stesso deposito
                    c["motivi"].extend(verifica.get("motivi", []))
                    c.update(stato="non_applicabile")
                    continue
            else:
                from bellomberg.market_data.filing_verifica import verifica_documento
                verifica = verifica_documento(snapshot["path"], url=item["url"], profilo=profilo,
                                               catalogo=catalogo)
            identita_corpo_fallita = False
            if (verifica.get("stato") == "ok" and profilo.get("forme_sec") == ["6-K"]
                    and item["fonte"] == "SEC EDGAR"):
                # R-FONTI 10/10 v2 (riserva ALTO-1): sotto il CIK del titolo la COPERTINA del 6-K porta sempre il
                # registrante, quindi la regola «emittente» del profilo (nuovo, automatico aggiornato o legacy)
                # passa anche col comunicato di un partner, di una controllata o la revisione di un'altra entita'.
                # L'identita' si prova anche nel CORPO, per OGNI 6-K verificato con regole testuali.
                from bellomberg.market_data.filing_verifica import identita_6k
                motivo_corpo, prova_corpo = identita_6k(snapshot["path"], url=item["url"], profilo=profilo,
                                                        catalogo=catalogo)
                if motivo_corpo:
                    verifica = {"stato": "non_verificato", "motivi": [motivo_corpo]}
                    identita_corpo_fallita = True
                else:
                    c["identita_verifica"] = prova_corpo
            if (verifica.get("stato") != "ok" and profilo.get("forme_sec") == ["6-K"]
                    and item["fonte"] == "SEC EDGAR" and not identita_corpo_fallita):
                # APERTO-TI 06/10: allegato 6-K FPI («quarter ended ...»), regola standard dichiarata;
                # identita' del profilo e prova nel corpo (identita_6k). I motivi del profilo restano accanto.
                from bellomberg.market_data.filing_verifica import verifica_6k_standard
                standard = verifica_6k_standard(snapshot["path"], url=item["url"], profilo=profilo,
                                                catalogo=catalogo)
                if standard.get("stato") in ("ok", "periodo_da_confermare"):
                    c["motivi_profilo"] = list(verifica.get("motivi", []))
                    c.update({k: deepcopy(standard[k]) for k in ("regola_verifica", "identita_verifica",
                                                                 "periodo_stato", "periodi_visti") if k in standard})
                    verifica = standard
                    if standard["stato"] == "periodo_da_confermare":
                        # Relazione dell'emittente con periodo non certo: byte conservati, dichiarata,
                        # fuori dal confronto (non e' «ultimo non verificato» ne' un verificato).
                        c.update(stato="periodo_da_confermare")
                        c["motivi"].extend(standard["motivi"])
                        continue
            c["motivi"].extend(verifica.get("motivi", []))
            if verifica.get("stato") != "ok" and profilo.get("forme_sec") == ["6-K"]:
                # 6-K di altra natura (produzione, avvisi): non e' la relazione cercata.
                c.update(stato="non_applicabile")
                continue
            if verifica.get("stato") == "ok" and verifica.get("documento"):
                doc = verifica["documento"]
                if doc.get("sha256") != snapshot["sha256"]:
                    c["sha256_verifica"] = doc.get("sha256")
                    raise ValueError("hash differente fra download e verifica: snapshot cambiato, documento rifiutato")
                c.update(stato="verificato", metadati=doc["metadati"])
                if doc.get('filing_verification') is not None:
                    c['filing_verification'] = deepcopy(doc['filing_verification'])
                verificati.append((doc, c))
            elif not c["motivi"]:
                c["motivi"].append("verifica documentale non disponibile")
        except Exception as exc:
            c["motivi"].append(f"{type(exc).__name__}: {exc}")
            if tentato:  # download o verifica interrotti: esito non definitivo, da ritentare
                c["errore_acquisizione"] = True

    gruppi = defaultdict(list)
    for doc, c in verificati:
        meta = doc["metadati"]
        gruppi[tuple(meta[k] for k in ("emittente_id", "lingua", "tipo", "perimetro", "periodo_inizio", "periodo_fine"))].append((doc, c))
    unici = []
    for gruppo in gruppi.values():
        if len({doc["sha256"] for doc, _ in gruppo}) > 1 and profilo.get("stesso_periodo") == "piu_lungo":
            # Opt-in: comunicato e relazione dello stesso periodo; si tiene il testo piu' lungo.
            gruppo = sorted(gruppo, key=lambda p: -len(p[0].get("estrazione", {}).get("testo", "")))
            unici.append(gruppo[0])
            for _, c in gruppo[1:]:
                c.update(stato="duplicato")
                c["motivi"].append("stesso periodo: tenuto il documento piu' lungo (regola del profilo)")
        elif len({doc["sha256"] for doc, _ in gruppo}) > 1:
            for _, c in gruppo:
                c.update(stato="versione_ambigua")
                c["motivi"].append("stesso periodo con hash diversi: versione documentale ambigua, nessuna scelta automatica")
        else:
            unici.append(gruppo[0])
            for _, c in gruppo[1:]:
                c.update(stato="duplicato")
                c["motivi"].append("stesso periodo e stessi byte: duplicato deduplicato per hash")
    unici.sort(key=lambda pair: pair[0]["metadati"]["periodo_fine"], reverse=True)
    ultimo = unici[0][0]["metadati"]["periodo_fine"] if unici else None
    for c in out["candidati"]:
        if c["stato"] not in ("verificato", "duplicato", "non_applicabile", "periodo_da_confermare"):
            periodo = c.get("report_date") or c.get("period_end") or c.get("metadati", {}).get("periodo_fine")
            if not periodo or not ultimo or periodo >= ultimo:
                out["ultimo_non_verificato"] = True
            out["motivi"].extend(f"{c['url'] or c['fonte']}: {m}" for m in c["motivi"])
    if out["ultimo_non_verificato"]:
        out["motivi"].append("Candidati recenti o senza periodo non verificati: l'eventuale confronto e' storico, non corrente.")
    if unici:
        dopo, c_dopo = unici[0]
        prima = [(d, c) for d, c in unici[1:] if _omologhi(d, dopo)]
        regola = None
        if not prima and (seq := _sequenziale(profilo, unici, out["candidati"])):
            prima, regola = [seq], "sequenziale"
        if len(prima) == 1:
            from bellomberg.market_data.filing_diff import confronta_documenti
            fine_base = prima[0][0]["metadati"]["periodo_fine"]
            base_non_verificata = any(c["stato"] in ("non_verificato", "versione_ambigua")
                                      and (c.get("report_date") or c.get("period_end")
                                           or c.get("metadati", {}).get("periodo_fine")) == fine_base
                                      for c in out["candidati"])
            # Repository ESEF fermo a un esercizio vecchio: l'ultimo verificato non e' corrente.
            fermo = any(f.get("fermo") for f in fonti)
            storico = out["ultimo_non_verificato"] or base_non_verificata or fermo
            if base_non_verificata:
                out["motivi"].append("Versione/controparte base non verificata: confronto dei documenti disponibili solo storico.")
            ambito = "storico" if storico else "ultimo_verificato"
            out["coppia"] = {"ambito": ambito, "prima": _riassunto(*prima[0]),
                              "dopo": _riassunto(dopo, c_dopo)}
            if regola:  # `ambito` resta corrente/storico; la regola della coppia si dichiara a parte
                out["coppia"]["regola"] = regola
            documenti = (prima[0][0], dopo)
            if esef_blocchi and all("esef" in d for d in documenti):
                from bellomberg.market_data.filing_esef import allinea_sezioni
                documenti = allinea_sezioni(*documenti)  # nota nuova o eliminata = testo aggiunto/rimosso
            diff = confronta_documenti(*documenti)
            ripetute = max(len(d.get("esef", {}).get("scartati", [])) for d in documenti) if esef_blocchi else 0
            if ripetute:
                diff["limiti"].append(f"ESEF: {ripetute} note fatte solo di frasi gia' presenti in altre note: "
                                      "confrontate dove il testo compare.")
            diff["ambito"] = ambito
            if regola:
                diff["regola"] = regola
                diff.setdefault("limiti", []).append(SEQUENZIALE_LIMITE)
            out["confronto_storico" if storico else "confronto_corrente"] = diff
            out["motivi"].extend(diff.get("motivi", []))
        elif len(prima) > 1:
            out["motivi"].append("piu' periodi omologhi plausibili: coppia ambigua")
        else:
            out["motivi"].append("omologo dell'anno immediatamente precedente non disponibile: nessun salto di anno o trimestre")
    else:
        out["motivi"].append("nessun documento con metadati verificati e versione univoca")
    out["freschezza"] = _freschezza(profilo, calendario, verificati, oggi)
    if out["confronto_corrente"]:
        out["stato"] = "ok" if (out["confronto_corrente"]["stato"] == "ok" and not out["motivi"]
                                and out["freschezza"]["stato"] != "stale") else "parziale"
    elif verificati or out["candidati"]:
        out["stato"] = "parziale"
    return out


def esito_completo(risultato):
    """Esito definitivo: nessuna fonte in errore, nessun indice 6-K illeggibile, nessun
    download/verifica interrotti, ultimo documento verificato. Altrimenti va ritentato."""
    if not isinstance(risultato, dict) or risultato.get("ultimo_non_verificato"):
        return False
    if any(not isinstance(f, dict) or f.get("stato") == "errore" or f.get("indici_non_letti")
           for f in risultato.get("fonti") or []):
        return False
    return not any(isinstance(c, dict) and c.get("errore_acquisizione") for c in risultato.get("candidati") or [])


def incompleto_transitorio(risultato):
    """Incompletezza da ritentare: fonte in errore, indice 6-K illeggibile, download o verifica
    interrotti. Ultimo documento non verificato per motivi deterministici (rettifica /A
    esclusa, limite max_documenti, verifica respinta) e' invece stabile: con gli stessi
    depositi il run completo darebbe lo stesso esito."""
    if not isinstance(risultato, dict):
        return True
    if any(not isinstance(f, dict) or f.get("stato") == "errore" or f.get("indici_non_letti")
           for f in risultato.get("fonti") or []):
        return True
    return any(isinstance(c, dict) and c.get("errore_acquisizione") for c in risultato.get("candidati") or [])


def collegamento_da_confermare(profilo):
    """Motivo per non eseguire un profilo SEC collegato in automatico (per nome o ticker) a un titolo
    con suffisso di listino, None altrimenti.

    Seguito revisione G1 (04/10/2026): prima bastava il nome normalizzato largo e un .L e' stato
    collegato a un'altra societa' SEC. Il codice nuovo non crea piu' questi profili (solo alias
    verificati o conferme dell'utente); quelli gia' salvati restano da confermare."""
    if (isinstance(profilo, dict) and profilo.get("cik") and "." in str(profilo.get("ticker") or "")
            and profilo.get("origine_collegamento") in ("nome", "ticker")):
        return ("collegamento SEC automatico per " + str(profilo["origine_collegamento"]) + " di un titolo con "
                "suffisso di listino, creato prima della revisione: da confermare (Scollega e conferma il CIK "
                "dalla pagina Filing); nessun documento scaricato")
    return None


def esegui_profilo(profilo, *, archivio, oggi=None, max_documenti=20, numeri_fn=None):
    """Seleziona l'ultimo periodo verificato e l'omologo dell'anno precedente.

    Con ``varianti`` (es. annuale e trimestrale) esegue ogni variante e restituisce
    la primaria: quella con confronto e periodo piu' recente; ``varianti`` riassume
    tutte. ``numeri_fn(cik, coppia)`` aggiunge i numeri chiave della coppia: un suo
    errore e' dichiarato in ``numeri`` e non rompe il confronto testuale.
    """
    from bellomberg.storage.filing_store import unisci_variante
    da_confermare = collegamento_da_confermare(profilo)
    if da_confermare:
        return {"ticker": profilo["ticker"], "stato": "non_disponibile", "motivi": [da_confermare], "candidati": [],
                "coppia": None, "confronto_corrente": None, "confronto_storico": None,
                "ultimo_non_verificato": False, "fonti": [], "src": "filing_pipeline",
                "copertura": {"stato": "non_disponibile", "limiti": [da_confermare]}}
    if not isinstance(profilo, dict) or not profilo.get("varianti"):
        out = _esegui_singolo(profilo, archivio=archivio, oggi=oggi, max_documenti=max_documenti)
    else:
        esiti = [(v["tipo"], _esegui_singolo(unisci_variante(profilo, v), archivio=archivio,
                                             oggi=oggi, max_documenti=max_documenti))
                 for v in profilo["varianti"]]

        def fine_dopo(r):
            return (((r.get("coppia") or {}).get("dopo") or {}).get("metadati") or {}).get("periodo_fine") or ""

        def chiave(indice):
            r = esiti[indice][1]
            diff = r.get("confronto_corrente") or r.get("confronto_storico")
            # Un confronto senza sezioni confrontate (intestazioni non riconosciute) non conta.
            utile = bool(diff) and bool(diff.get("sezioni_confrontate", True))
            return (utile, fine_dopo(r), bool(r.get("confronto_corrente")), -indice)

        indice = max(range(len(esiti)), key=chiave)
        tipo, primario = esiti[indice]
        # Spec §5.2 b: ogni variante conserva il proprio confronto (le altre si citano).
        out = {**primario, "variante": tipo, "varianti": [{
            "tipo": t, "stato": r.get("stato"), "ambito": (r.get("coppia") or {}).get("ambito"),
            **({"regola": r["coppia"]["regola"]} if (r.get("coppia") or {}).get("regola") else {}),
            "coppia_periodi": ([(((r["coppia"].get(k) or {}).get("metadati")) or {}).get("periodo_fine")
                                for k in ("prima", "dopo")] if r.get("coppia") else None),
            "motivi": list(r.get("motivi", []))[:5], "primaria": i == indice, "completo": esito_completo(r),
            "transitorio": incompleto_transitorio(r),
            **({} if i == indice else {"coppia": r.get("coppia"),
                                        "confronto": r.get("confronto_corrente") or r.get("confronto_storico")})}
            for i, (t, r) in enumerate(esiti)]}
        # APERTO-TI 06/10: i candidati delle varianti NON primarie non si buttano (il 20-F di un
        # titolo con 6-K primario spariva): restano accanto, etichettati con la loro variante.
        out["candidati_varianti"] = [{**deepcopy(c), "variante": t} for i, (t, r) in enumerate(esiti)
                                     if i != indice for c in r.get("candidati") or []]
        altre = sorted((r for i, (_, r) in enumerate(esiti) if i != indice and r.get("coppia")),
                       key=fine_dopo, reverse=True)
    cik = profilo.get("cik") if isinstance(profilo, dict) else None
    if isinstance(profilo, dict) and profilo.get("esef_modo") == "blocchi" and profilo.get("varianti"):
        # Profilo misto (fase C): numeri dai JSON ESEF della variante annuale, anche se la
        # variante presentata e' l'infrannuale da PDF IR (che non ha fatti XBRL).
        from bellomberg.market_data.filing_esef import numeri_esef
        tipo_esef, esito_esef = next(((t, r) for (t, r), v in zip(esiti, profilo["varianti"])
                                      if unisci_variante(profilo, v).get("esef_modo") == "blocchi"), (None, None))
        if esito_esef and esito_esef.get("coppia"):
            out["numeri"] = _numeri(lambda _id, coppia: numeri_esef(coppia), None, esito_esef["coppia"])
            if tipo_esef != out.get("variante"):
                out["numeri"] = {**out["numeri"], "variante": tipo_esef}
    elif isinstance(profilo, dict) and profilo.get("esef_modo") == "blocchi" and out.get("coppia"):
        from bellomberg.market_data.filing_esef import numeri_esef
        out["numeri"] = _numeri(lambda _id, coppia: numeri_esef(coppia), None, out["coppia"])  # dai JSON archiviati
    elif numeri_fn is not None and cik and out.get("coppia"):
        out["numeri"] = _numeri(numeri_fn, cik, out["coppia"])
        if out["numeri"].get("stato") != "ok" and isinstance(profilo, dict) and profilo.get("varianti"):
            # Le relazioni 6-K di solito non hanno fatti XBRL in companyfacts: numeri
            # dell'ultima variante che li ha, con la variante dichiarata.
            for alt_tipo, alt in ((t, r) for t, r in esiti if r in altre):
                numeri = _numeri(numeri_fn, cik, alt["coppia"])
                if numeri.get("stato") == "ok":
                    out["numeri"] = {**numeri, "variante": alt_tipo}
                    break
    return out


def _numeri(numeri_fn, cik, coppia):
    try:
        numeri = numeri_fn(cik, coppia)
    except Exception as exc:
        return {"stato": "errore", "motivo": f"{type(exc).__name__}: {exc}", "voci": []}
    if isinstance(numeri, dict):
        # P0-8: questa e' la coppia PASSATA al calcolo, non quella del contenitore
        # primario (che puo' essere infrannuale mentre i numeri sono annuali).
        from bellomberg.market_data.filing_numeri import VOCI
        modi = {voce: modo for voce, _, modo in VOCI}
        documenti = {lato: {k: (coppia.get(lato) or {}).get(k) for k in ("url", "sha256")}
                     for lato in ("prima", "dopo")}
        voci = []
        for voce in numeri.get("voci") or []:
            modo = modi.get(voce.get("voce"))
            periodi = None
            if modo:
                periodi = {}
                for lato in ("prima", "dopo"):
                    meta = (coppia.get(lato) or {}).get("metadati") or {}
                    periodi[lato] = {"fine": meta.get("periodo_fine")}
                    if modo == "durata":
                        periodi[lato]["inizio"] = meta.get("periodo_inizio")
            voci.append({**voce, "tipo_periodo": modo, "periodi": periodi})
        numeri = {**numeri, "voci": voci, "documenti": documenti}
    if isinstance(numeri, dict) and (coppia or {}).get("regola") == "sequenziale":
        # Stessi periodi della coppia: il confronto e' col trimestre precedente, mai l'anno prima.
        numeri = {**numeri, "confronto": "trimestre_precedente"}
    return numeri


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profilo", help="file JSON del profilo documentale curato")
    parser.add_argument("--archivio", required=True, help="directory per snapshot immutabili")
    parser.add_argument("--max-documenti", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        profilo = json.loads(Path(args.profilo).read_text(encoding="utf-8-sig"))
        result = esegui_profilo(profilo, archivio=args.archivio, max_documenti=args.max_documenti)
    except (OSError, ValueError, TypeError) as exc:
        result = {"stato": "errore", "motivi": [f"{type(exc).__name__}: {exc}"], "src": "filing_pipeline"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["stato"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
