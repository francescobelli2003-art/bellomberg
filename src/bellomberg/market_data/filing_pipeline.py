"""I-20: acquisizione e confronto documentale da profili espliciti USA/Europa.

Nessun LLM, DB o scheduler. Le copie scaricate restano nell'archivio scelto
dal chiamante; confronto riuscito e freschezza delle fonti sono esiti distinti.
"""
from collections import defaultdict
from datetime import date, datetime
import json
from pathlib import Path
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


def _raccogli(profilo, archivio):
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
            from bellomberg.market_data.sec_edgar import get_filing_catalog
            catalogo = get_filing_catalog(profilo["ticker"])
            fonte.update(stato=catalogo["stato"], motivi=list(catalogo.get("motivi", [])))
            rows = catalogo.get("documenti")
            if not isinstance(rows, list):
                raise ValueError("catalogo SEC senza elenco documenti")
            for row in rows:
                form = str(row.get("form") or "")
                base = form.removesuffix("/A")
                # La classificazione del catalogo esclude solo tipi inequivocabilmente
                # diversi. Un 6-K resta candidato: la relazione va provata nei byte.
                if base in ("10-K", "20-F", "40-F") and profilo["tipo"] != "annuale":
                    continue
                if base == "10-Q" and profilo["tipo"] == "annuale":
                    continue
                motivo = "rettifica /A esclusa: versione corrente da verificare" if form.endswith("/A") else None
                aggiungi("SEC EDGAR", row, row.get("url"),
                         hosts_extra | {"sec.gov", "www.sec.gov", "data.sec.gov"}, motivo)
        except Exception as exc:
            fonte.update(stato="errore")
            fonte["motivi"].append(f"{type(exc).__name__}: {exc}")
    elif attiva("sec") and abilitate is not None and "sec" in abilitate:
        fonti.append({"nome": "SEC EDGAR", "stato": "non_disponibile",
                      "motivi": ["SEC richiesta senza CIK esplicito o sec=True; lookup non eseguito"]})

    if attiva("esef") and lei:
        fonte = {"nome": "ESEF", "stato": "ok", "motivi": []}
        fonti.append(fonte)
        limiti.append("ESEF: repository filings.xbrl.org non esaustivo; annuali mancanti e intermedi richiedono fonti IR.")
        try:
            from bellomberg.market_data.esef import _list_filings
            for row in _list_filings(lei):
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
        for url in dict.fromkeys(urls):
            fonte = {"nome": "IR", "url": url, "stato": "ok", "motivi": []}
            fonti.append(fonte)
            try:
                snapshot = _scarica(url, archivio, hosts_ir)
                fonte["snapshot"] = snapshot
                estratto = estrai_testo(snapshot["path"])
                if estratto.get("stato") != "ok":
                    raise ValueError(estratto.get("motivo", "pagina IR illeggibile"))
                if estratto.get("formato") == "pdf":
                    aggiungi("IR", {}, url, hosts_ir)
                    continue
                html = Path(snapshot["path"]).read_bytes().decode(estratto.get("codifica", "utf-8-sig"))
                links = candidati_comunicato(html, snapshot.get("url_finale") or url)
                if not links:
                    fonte.update(stato="parziale")
                    fonte["motivi"].append("nessun link candidato nella pagina IR osservata")
                for link in links:
                    aggiungi("IR", {"pagina_ir": url, "testo_link": link.get("testo")}, link["url"], hosts_ir)
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


def esegui_profilo(profilo, *, archivio, oggi=None, max_documenti=20):
    """Seleziona l'ultimo periodo verificato e l'omologo dell'anno precedente.

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
    candidati, fonti, limiti, calendario = _raccogli(profilo, archivio)
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
             **{k: catalogo[k] for k in ("report_date", "period_end", "filed_date", "language", "form", "rettifica", "pagina_ir") if k in catalogo}}
        out["candidati"].append(c)
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
            snapshot = _scarica(item["url"], archivio, item["hosts"])
            c.update(path=snapshot["path"], sha256=snapshot["sha256"])
            from bellomberg.market_data.filing_verifica import verifica_documento
            verifica = verifica_documento(snapshot["path"], url=item["url"], profilo=profilo,
                                           catalogo=catalogo)
            c["motivi"].extend(verifica.get("motivi", []))
            if verifica.get("stato") == "ok" and verifica.get("documento"):
                doc = verifica["documento"]
                if doc.get("sha256") != snapshot["sha256"]:
                    c["sha256_verifica"] = doc.get("sha256")
                    raise ValueError("hash differente fra download e verifica: snapshot cambiato, documento rifiutato")
                c.update(stato="verificato", metadati=doc["metadati"])
                verificati.append((doc, c))
            elif not c["motivi"]:
                c["motivi"].append("verifica documentale non disponibile")
        except Exception as exc:
            c["motivi"].append(f"{type(exc).__name__}: {exc}")

    gruppi = defaultdict(list)
    for doc, c in verificati:
        meta = doc["metadati"]
        gruppi[tuple(meta[k] for k in ("emittente_id", "lingua", "tipo", "perimetro", "periodo_inizio", "periodo_fine"))].append((doc, c))
    unici = []
    for gruppo in gruppi.values():
        if len({doc["sha256"] for doc, _ in gruppo}) > 1:
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
        if c["stato"] not in ("verificato", "duplicato", "non_applicabile"):
            periodo = c.get("report_date") or c.get("period_end") or c.get("metadati", {}).get("periodo_fine")
            if not periodo or not ultimo or periodo >= ultimo:
                out["ultimo_non_verificato"] = True
            out["motivi"].extend(f"{c['url'] or c['fonte']}: {m}" for m in c["motivi"])
    if out["ultimo_non_verificato"]:
        out["motivi"].append("Candidati recenti o senza periodo non verificati: l'eventuale confronto e' storico, non corrente.")
    if unici:
        dopo, c_dopo = unici[0]
        prima = [(d, c) for d, c in unici[1:] if _omologhi(d, dopo)]
        if len(prima) == 1:
            from bellomberg.market_data.filing_diff import confronta_documenti
            fine_base = prima[0][0]["metadati"]["periodo_fine"]
            base_non_verificata = any(c["stato"] in ("non_verificato", "versione_ambigua")
                                      and (c.get("report_date") or c.get("period_end")
                                           or c.get("metadati", {}).get("periodo_fine")) == fine_base
                                      for c in out["candidati"])
            storico = out["ultimo_non_verificato"] or base_non_verificata
            if base_non_verificata:
                out["motivi"].append("Versione/controparte base non verificata: confronto dei documenti disponibili solo storico.")
            ambito = "storico" if storico else "ultimo_verificato"
            out["coppia"] = {"ambito": ambito, "prima": _riassunto(*prima[0]),
                              "dopo": _riassunto(dopo, c_dopo)}
            diff = confronta_documenti(prima[0][0], dopo)
            diff["ambito"] = ambito
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
