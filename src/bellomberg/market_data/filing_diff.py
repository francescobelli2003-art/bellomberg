"""I-20, primo blocco: confronto documentale deterministico USA/Europa.

Contratto: identita', lingua, perimetro e periodi ESPLICITI; sezioni selezionate
per intestazioni esatte o pagine fisiche (base 1). Nessuna inferenza dal ticker,
nessuna traduzione/LLM, nessun DB. Il risultato descrive cambiamenti TESTUALI:
non certifica che un rischio sia nuovo o materialmente rilevante.

CLI: python -m bellomberg.market_data.filing_diff manifest.json --archivio DIR
Il manifest contiene `prima`/`dopo`: path oppure url, metadati, sezioni.
I path relativi sono relativi al manifest; output JSON su stdout.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import date
from difflib import SequenceMatcher
import hashlib
import json
from math import sqrt
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from bellomberg.market_data.lettore_trimestrali import estrai_testo, scarica_documento

MAX_CARATTERI = 5_000_000
MAX_UNITA = 10_000


def _norm(testo):
    return " ".join(unicodedata.normalize("NFC", testo).replace("\u00ad", "").split())


def _confine(testo, titolo, occorrenza=None):
    """Indice e fine di una riga completa: mai il primo duplicato per caso."""
    matches = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", testo)
               if _norm(m.group()) == _norm(titolo)]
    if occorrenza is None:
        if len(matches) != 1:
            raise ValueError(f"intestazione assente o ambigua: {titolo!r} ({len(matches)})")
        return matches[0]
    if type(occorrenza) is not int or not 1 <= occorrenza <= len(matches):
        raise ValueError(f"occorrenza non valida per {titolo!r}")
    return matches[occorrenza - 1]


def _seleziona(estrazione, selettore):
    testo = estrazione["testo"]
    if "da_pagina" in selettore:
        a, b = selettore["da_pagina"], selettore.get("a_pagina")
        riferimenti = estrazione.get("riferimenti", [])
        if (estrazione["formato"] != "pdf" or type(a) is not int or type(b) is not int
                or not 1 <= a <= b <= len(riferimenti)):
            raise ValueError("intervallo di pagine fisiche PDF non valido")
        if any(a <= p <= b for p in estrazione.get("pagine_senza_testo", [])):
            raise ValueError("sezione con pagine senza testo: contenuto non verificabile")
        return riferimenti[a - 1]["inizio"], riferimenti[b - 1]["fine"]
    if not selettore.get("inizio") or not selettore.get("fine"):
        raise ValueError("richieste intestazioni inizio/fine oppure da_pagina/a_pagina")
    _, a = _confine(testo, selettore["inizio"], selettore.get("occorrenza_inizio"))
    b, _ = _confine(testo, selettore["fine"], selettore.get("occorrenza_fine"))
    if b <= a:
        raise ValueError("fine della sezione precedente all'inizio")
    for pagina in estrazione.get("riferimenti", []):
        if a <= pagina["inizio"] < b and not pagina["testo"].strip():
            raise ValueError("sezione con pagina senza testo: contenuto non verificabile")
    return a, b


def prepara_documento(path, *, url, metadati, sezioni):
    """Lettura locale; metadati dichiarati dal chiamante, non certificati dal parser.

    `sezioni`: nomi canonici comuni ai due documenti (rischi/gestione/contenziosi)
    con selettori indipendenti dal paese. Duplicati, vuoti e sovrapposizioni
    diventano buchi espliciti e non rimozioni di rischi.
    """
    documento = {"url": url, "metadati": dict(metadati), "sezioni": {},
                 "metadati_verifica": "dichiarati dal chiamante; verificare sul documento"}
    try:
        if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            raise ValueError("URL originale HTTP(S) richiesto per le citazioni")
        grezzo = Path(path).read_bytes()
        documento["sha256"] = hashlib.sha256(grezzo).hexdigest()
        estrazione = estrai_testo(str(path), contenuto=grezzo)
        documento["estrazione"] = estrazione
        if estrazione["stato"] != "ok":
            raise ValueError(estrazione.get("motivo", "estrazione non disponibile"))
        if len(estrazione["testo"]) > MAX_CARATTERI:
            raise ValueError(f"documento oltre il limite di {MAX_CARATTERI} caratteri")
        if not sezioni:
            raise ValueError("nessuna sezione richiesta")
        for nome, selettore in sezioni.items():
            try:
                a, b = _seleziona(estrazione, selettore)
                if not estrazione["testo"][a:b].strip():
                    raise ValueError("sezione vuota")
                documento["sezioni"][nome] = {"stato": "ok", "inizio": a, "fine": b}
            except (ValueError, TypeError, KeyError) as exc:
                documento["sezioni"][nome] = {"stato": "non_disponibile", "motivo": str(exc)}
        valide = [(n, s) for n, s in documento["sezioni"].items() if s["stato"] == "ok"]
        for i, (nome, sezione) in enumerate(valide):
            for altro, intervallo in valide[i + 1:]:
                if max(sezione["inizio"], intervallo["inizio"]) < min(sezione["fine"], intervallo["fine"]):
                    for n in (nome, altro):
                        documento["sezioni"][n] = {"stato": "non_disponibile",
                                                   "motivo": "sezioni sovrapposte"}
        documento["stato"] = "ok"
    except (OSError, ValueError, TypeError) as exc:
        documento.update(stato="errore", motivo=str(exc))
    return documento


def _compatibilita(prima, dopo):
    motivi, periodi = [], []
    for lato, doc in (("prima", prima), ("dopo", dopo)):
        if doc.get("stato") != "ok":
            motivi.append(f"{lato}: {doc.get('motivo', 'documento non disponibile')}")
        meta = doc.get("metadati", {})
        for campo in ("emittente_id", "lingua", "perimetro", "tipo"):
            if not isinstance(meta.get(campo), str) or not meta[campo].strip():
                motivi.append(f"{lato}: {campo} mancante")
        try:
            a, b = (date.fromisoformat(meta[k]) for k in ("periodo_inizio", "periodo_fine"))
            durata = (b - a).days + 1
            limiti = {"annuale": (350, 380), "semestrale": (170, 195),
                      "trimestrale": (80, 100), "nove_mesi": (260, 285)}
            limite = limiti.get(meta.get("tipo"))
            if not limite or not limite[0] <= durata <= limite[1]:
                raise ValueError("durata incoerente con il tipo di relazione")
            periodi.append((a, b, durata))
        except (ValueError, TypeError, KeyError) as exc:
            motivi.append(f"{lato}: periodo non valido ({exc})")
    for campo in ("emittente_id", "lingua", "perimetro", "tipo"):
        if prima.get("metadati", {}).get(campo) != dopo.get("metadati", {}).get(campo):
            motivi.append(f"{campo} differente")
    if len(periodi) == 2:
        if periodi[1][1] <= periodi[0][1]:
            motivi.append("il periodo dopo deve essere successivo al periodo prima")
        if abs(periodi[0][2] - periodi[1][2]) > 14:
            motivi.append("durate dei periodi non confrontabili")
    return motivi


def _unita(doc, nomi):
    """Segmenti testuali con offset: gli estratti citati restano letterali."""
    testo, risultato = doc["estrazione"]["testo"], []
    for nome in sorted(nomi):
        sezione = doc["sezioni"][nome]
        a, b = sezione["inizio"], sezione["fine"]
        # Punteggiatura seguita da spazio, senza spezzare numeri decimali.
        for match in re.finditer(r"\S.*?(?:[.!?;](?=\s|$)|$)", testo[a:b], re.S):
            inizio, fine = a + match.start(), a + match.end()
            while fine > inizio and testo[fine - 1].isspace():
                fine -= 1
            if fine > inizio:
                risultato.append({"sezione": nome, "inizio": inizio, "fine": fine,
                                   "testo": testo[inizio:fine]})
    return risultato


def _citazione(doc, segmento):
    pagine = [p["pagina"] for p in doc["estrazione"].get("riferimenti", [])
              if max(p["inizio"], segmento["inizio"]) < min(p["fine"], segmento["fine"])]
    return {**segmento, "url": doc["url"], "sha256": doc["sha256"],
            "pagine_fisiche": pagine, "src": "filing_diff"}


def confronta_documenti(prima, dopo):
    motivi = _compatibilita(prima, dopo)
    risultato = {"stato": "non_confrontabile" if motivi else "ok", "motivi": motivi,
                 "cambiamenti": [], "sezioni_confrontate": [], "src": "filing_diff",
                 "limiti": ["Confronto testuale; rilevanza economica non valutata.",
                            "Identita', lingua e periodi dichiarati dal chiamante.",
                            "Spostamenti cercati soltanto nelle sezioni selezionate."]}
    if motivi:
        return risultato
    nomi = set(prima["sezioni"]) | set(dopo["sezioni"])
    comuni = set()
    for nome in sorted(nomi):
        problemi = []
        for lato, doc in (("prima", prima), ("dopo", dopo)):
            s = doc["sezioni"].get(nome, {})
            if s.get("stato") != "ok":
                problemi.append(f"{lato}: {s.get('motivo', 'sezione non selezionata')}")
        if problemi:
            motivi.append(f"{nome}: {'; '.join(problemi)}")
        else:
            comuni.add(nome)
    risultato["sezioni_confrontate"] = sorted(comuni)
    if motivi or not comuni:
        risultato["stato"] = "parziale"
    risultato["similarita_sezioni"] = {}
    for nome in sorted(comuni):
        frequenze = []
        for doc in (prima, dopo):
            s = doc["sezioni"][nome]
            testo = doc["estrazione"]["testo"][s["inizio"]:s["fine"]]
            frequenze.append(Counter(re.findall(r"\w+(?:[.,]\d+)*", _norm(testo).casefold())))
        a, b = frequenze
        unione = set(a) | set(b)
        denominatore = sqrt(sum(v*v for v in a.values()) * sum(v*v for v in b.values()))
        risultato["similarita_sezioni"][nome] = {
            "jaccard": len(set(a) & set(b)) / len(unione) if unione else None,
            "coseno": sum(v * b.get(k, 0) for k, v in a.items()) / denominatore if denominatore else None,
            "src": "filing_diff", "metodo": "token Unicode, minuscolo, frequenze; nessuna soglia decisionale"}
    old = _unita(prima, {n for n, s in prima["sezioni"].items() if s["stato"] == "ok"})
    new = _unita(dopo, {n for n, s in dopo["sezioni"].items() if s["stato"] == "ok"})
    if max(len(old), len(new)) > MAX_UNITA:
        risultato.update(stato="non_confrontabile")
        motivi.append(f"oltre il limite di {MAX_UNITA} segmenti; nessun troncamento applicato")
        return risultato
    residui_old, residui_new, abbinati = set(range(len(old))), set(range(len(new))), []
    # Conserva la sequenza: un riordino interno puo' cambiare il senso del testo.
    for nome in sorted(comuni):
        oi = [i for i, s in enumerate(old) if s["sezione"] == nome]
        ni = [i for i, s in enumerate(new) if s["sezione"] == nome]
        matcher = SequenceMatcher(None, [_norm(old[i]["testo"]) for i in oi],
                                  [_norm(new[i]["testo"]) for i in ni], autojunk=False)
        for blocco in matcher.get_matching_blocks():
            for offset in range(blocco.size):
                residui_old.remove(oi[blocco.a + offset])
                residui_new.remove(ni[blocco.b + offset])
    # Prima nella stessa sezione, poi fra sezioni: preserva anche le ripetizioni.
    for stessa_sezione in (True, False):
        indice = defaultdict(deque)
        for j in sorted(residui_new):
            chiave = (_norm(new[j]["testo"]), new[j]["sezione"] if stessa_sezione else None)
            indice[chiave].append(j)
        for i in sorted(residui_old):
            chiave = (_norm(old[i]["testo"]), old[i]["sezione"] if stessa_sezione else None)
            if indice[chiave]:
                j = indice[chiave].popleft()
                residui_old.remove(i)
                residui_new.remove(j)
                abbinati.append(("spostato", i, j))
    # Similarita' solo per accoppiare estratti, mai come giudizio sul rischio.
    coppie = []
    if len(residui_old) * len(residui_new) <= 10_000:
        for i in sorted(residui_old):
            for j in sorted(residui_new):
                a, b = _norm(old[i]["testo"]), _norm(new[j]["testo"])
                if old[i]["sezione"] != new[j]["sezione"] or max(len(a), len(b)) > 4000:
                    continue
                ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
                if ratio >= 0.6:
                    coppie.append((ratio, i, j))
    else:
        risultato["limiti"].append("Troppe coppie: estratti non identici elencati separatamente.")
    for _, i, j in sorted(coppie, key=lambda c: (-c[0], c[1], c[2])):
        if i in residui_old and j in residui_new:
            residui_old.remove(i)
            residui_new.remove(j)
            abbinati.append(("modificato", i, j))
    for tipo, i, j in abbinati:
        risultato["cambiamenti"].append({"tipo": tipo, "prima": _citazione(prima, old[i]),
                                           "dopo": _citazione(dopo, new[j])})
    risultato["segmenti_non_confrontabili"] = []
    for lato, residui, doc, unita, tipo in (("prima", residui_old, prima, old, "rimosso"),
                                           ("dopo", residui_new, dopo, new, "aggiunto")):
        for i in sorted(residui):
            if unita[i]["sezione"] not in comuni:
                risultato["segmenti_non_confrontabili"].append({lato: _citazione(doc, unita[i])})
                continue
            risultato["cambiamenti"].append({"tipo": tipo, lato: _citazione(doc, unita[i])})
    risultato["misure"] = {"segmenti_prima": len(old), "segmenti_dopo": len(new),
                            "cambiamenti": len(risultato["cambiamenti"])}
    return risultato


def esegui_manifest(manifest, *, base_dir, archivio=None):
    """Collega acquisizione ed estrazione. Nessuna scelta automatica di emittente/periodo."""
    documenti = []
    for lato in ("prima", "dopo"):
        spec = manifest[lato]
        path = spec.get("path")
        if path:
            path = Path(path)
            if not path.is_absolute():
                path = Path(base_dir) / path
        else:
            if archivio is None:
                raise ValueError("--archivio richiesto per scaricare documenti")
            acquisizione = scarica_documento(spec["url"], str(archivio))
            if acquisizione["stato"] != "ok":
                return {"stato": "errore", "motivi": [f"{lato}: {acquisizione['motivo']}"],
                        "cambiamenti": []}
            path = acquisizione["path"]
        documenti.append(prepara_documento(path, url=spec["url"],
                           metadati=spec["metadati"], sezioni=spec["sezioni"]))
    risultato = confronta_documenti(*documenti)
    risultato["documenti"] = [{k: d[k] for k in ("url", "sha256", "metadati") if k in d}
                               for d in documenti]
    return risultato


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--archivio", type=Path)
    args = parser.parse_args()
    try:
        risultato = esegui_manifest(json.loads(args.manifest.read_text(encoding="utf-8")),
                                   base_dir=args.manifest.parent, archivio=args.archivio)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        risultato = {"stato": "errore", "motivi": [str(exc)], "cambiamenti": []}
    print(json.dumps(risultato, ensure_ascii=False, indent=2))
    return 0 if risultato["stato"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
