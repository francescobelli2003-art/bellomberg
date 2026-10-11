"""Numeri chiave dal PDF della relazione finanziaria semestrale (H1) dell'emittente europeo senza XBRL utile.

PRY-H1 (ordine PM 10/10/2026, Opus 5.5). La semestrale scaricata dal sito dell'emittente e verificata dalla
pipeline (identita', tipo e periodo con le regole del profilo, filing_verifica) arrivava agli agenti senza
numeri: «numeri non disponibili». Qui si leggono le TABELLE del PDF e se ne ricavano le voci chiave, con la
stessa forma di uscita di `filing_numeri.variazioni` (stato, valuta, voci, scarti, fonte, origine).

PRINCIPIO (revisione v3): un numero sbagliato e' peggio di n.d. Ogni dubbio lascia la voce n.d. col motivo.

Lettura (v3): dal PDF le righe si ricostruiscono dalle COORDINATE delle parole (pdfplumber, gia' nel repo):
lo stream puo' scrivere le celle in un ordine diverso da quello visivo, o una cella per riga. Le colonne
dell'intestazione hanno la loro x (anche su piu' righe: «12 months / ended June / 30, 2026»); ogni cella va
alla colonna con cui si sovrappone in x; due celle nella stessa colonna, una cella a cavallo di due colonne o
fuori da ogni colonna = riga n.d. Due tabelle affiancate con le stesse colonne si leggono separatamente. Le
pagine senza intestazione di periodo non si misurano (tempo: ~0,2 s per pagina candidata). L'identita' resta
sul testo pypdf, lo stesso lettore della verifica.

Copertura (requisito PM 10/10): emittenti europei in generale, nessuna regola di un singolo emittente.
Etichette, colonne e unita' in italiano, inglese, tedesco, francese, olandese e spagnolo; numeri 1,234.5 e
1.234,5 (anche migliaia con spazio, raggruppate solo se la lettura e' unica); negativi fra parentesi o col
meno; colonne corrente/confronto in entrambi gli ordini; scale migliaia, milioni, miliardi. NON coperti
(restano n.d. col motivo): PDF scansionati (OCR), colonne di variazione su una riga separata dall'intestazione
dei periodi, valute diverse fra colonne, tabelle che continuano alla pagina dopo, prospetti che stampano
grandezze diverse con la stessa etichetta, tabelle senza prova di perimetro di gruppo.

Regole (nessun ripiego silenzioso, regola PM 14/07):
- COLONNE: una riga vale solo sotto un'intestazione riconosciuta nella stessa pagina, con UNA colonna del
  periodo corrente e al piu' una del periodo di confronto (stesso periodo dell'anno prima). Per le voci di
  DURATA la colonna corrente deve dichiarare sei mesi (H1, six months, first half, 1.1.-30.6., semestre,
  Halbjahr ...); una data nuda vale solo per le voci a ISTANTE (debito/cassa), salvo che il contesto
  dell'intestazione dichiari i sei mesi e non un trimestre. Trimestri, esercizio, ultimi 12 mesi e variazioni
  sono colonne riconosciute ma mai lette come voce.
- UNITA' e VALUTA: legate alla tabella (marcatore nell'intestazione, nella riga, o fra la tabella precedente
  e questa); mai ereditate dalla tabella prima. Marcatore con scala non riconosciuta = unita' ignota, n.d.
- FORMATO NUMERICO: inglese (1,234.5) o a virgola decimale (1.234,5) deciso sull'intero documento; senza
  prova o con prove discordi, nessun numero.
- SEGNO: parentesi o meno = negativo; «-» da solo e' una cella vuota (n.d., mai zero). Debito o cassa netti
  stampati negativi, o di segno opposto fra tabelle: convenzione non determinabile, n.d.
- ETICHETTE: corrispondenza ESATTA dell'etichetta normalizzata. Valori correnti diversi fra tabelle: decidono
  le tabelle del gruppo se concordi, altrimenti n.d. coi valori. Nessuna tabella di gruppo: n.d.
- PERIMETRO: escluse tabelle di segmento/area, attivita' cessate, utile per azione, conto economico
  complessivo; un valore esce solo se almeno una tabella di gruppo/consolidata lo riporta.
- MISURA: ogni voce dichiara «riportato», «rettificato» o «misura dell'emittente»; l'utile netto e' la
  quota del gruppo (riga «attributable/davon/di cui» subito dopo l'utile netto); l'utile totale esce come
  voce a parte, col perimetro dichiarato solo se la tabella stampa la quota delle minoranze.
- IDENTITA': byte verificati (sha256 della coppia) e prova dell'emittente con la regola del profilo nella
  parte iniziale (stessa guardia di filing_verifica); fonte esterna = mai numeri. NIENTE LOOK-AHEAD: periodo o
  data di deposito oltre `fino_al` = rifiuto; data di deposito ignota = dichiarata.
"""
import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from itertools import combinations
from pathlib import Path

ORIGINE = "pdf_semestrale"
MAX_CARATTERI_IDENTITA = 20_000  # come filing_verifica: il nome dell'emittente nella parte iniziale

_MESI = {}
for _n, _nomi in enumerate((
        ("january", "jan", "gennaio", "gen", "januar", "jänner", "janvier", "janv", "januari", "enero", "ene"),
        ("february", "feb", "febbraio", "februar", "février", "fevrier", "févr", "februari", "febrero"),
        ("march", "mar", "marzo", "märz", "maerz", "mars", "maart", "mrt"),
        ("april", "apr", "aprile", "avril", "avr", "abril", "abr"), ("may", "maggio", "mag", "mai", "mei", "mayo"),
        ("june", "jun", "giugno", "giu", "juni", "juin", "junio"),
        ("july", "jul", "luglio", "lug", "juli", "juillet", "juil", "julio"),
        ("august", "aug", "agosto", "ago", "août", "aout", "augustus"),
        ("september", "sep", "sept", "settembre", "set", "septembre", "septiembre"),
        ("october", "oct", "ottobre", "ott", "oktober", "okt", "octobre", "octubre"),
        ("november", "nov", "novembre", "noviembre"),
        ("december", "dec", "dicembre", "dic", "dezember", "dez", "décembre", "decembre", "déc", "diciembre")), 1):
    for _nome in _nomi:
        _MESI[_nome] = _n
_MESE = "|".join(sorted(map(re.escape, _MESI), key=len, reverse=True))
_DATA = (rf"(?:(?:{_MESE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
         rf"|\d{{1,2}}(?:st|nd|rd|th|°|er)?\.?\s+(?:de\s+)?(?:{_MESE})\.?,?\s+(?:de\s+)?\d{{4}}"
         r"|\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})(?!\d))")
_ANNO = r"'?(?:\d{4}|\d{2})(?!\d)"
# Intervallo di date («1.1.-30.6.2026», «1 January - 30 June 2026», «January 1 - June 30, 2026»)
_INIZIO = (rf"(?:\d{{1,2}}[./]\d{{1,2}}[./]?(?:\d{{2,4}})?|\d{{1,2}}\.?\s+(?:{_MESE})\.?(?:\s+\d{{4}})?"
           rf"|(?:{_MESE})\.?\s+\d{{1,2}}(?:,\s*\d{{4}})?)")
_INTERVALLO = rf"{_INIZIO}\s*[-–]\s*{_DATA}"

# Colonne riconosciute, in ordine di priorita' alla stessa posizione (la prima alternativa che combacia).
_COLONNE = (
    ("intervallo", _INTERVALLO),
    ("altro", rf"(?:nine|9|twelve|12)[\s-]*months?\s+(?:period\s+)?(?:ended|to)\s+{_DATA}"
              r"|\bLTM\b|(?:last|ultimi)\s+(?:twelve|12)\s+(?:months|mesi)|\b(?:9M|12M|LTM)\b"
              # colonna «di cui parti correlate» (IAS 24) accanto al periodo: riconosciuta, mai letta
              r"|of\s+which\s+(?:with\s+)?related\s+parties|di\s+cui\s+(?:con\s+|verso\s+)?parti\s+correlate"
              r"|davon\s+(?:mit\s+)?nahestehenden?\s+(?:unternehmen|personen)|dont\s+parties\s+liées"),
    ("sei_mesi", rf"(?:six|6)[\s-]*months?\s+(?:period\s+)?(?:ended|to)\s+{_DATA}"
                 rf"|(?:sei\s+mesi|primo\s+semestre|semestre)\s+(?:chius[oi]\s+)?al\s+{_DATA}"
                 rf"|(?:semestre|six\s+mois|premier\s+semestre)\s+(?:clos\s+)?(?:au|le)\s+{_DATA}"
                 rf"|seis\s+meses\s+(?:terminados?\s+el|al|cerrados?\s+a)\s+{_DATA}"),
    ("trimestre", rf"(?:three|3)[\s-]*months?\s+(?:period\s+)?(?:ended|to)\s+{_DATA}"
                  rf"|tre\s+mesi\s+(?:chiusi\s+)?al\s+{_DATA}"
                  rf"|(?<![\w])(?:Q[1-4]|[1-4]Q|T[1-4]|[1-4]T|K[1-4])\s*{_ANNO}"
                  r"|(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter(?:\s+of)?\s+\d{4}"
                  r"|(?:I|II|III|IV|1°|2°|3°|4°|primo|secondo|terzo|quarto)\s+trimestre\s+\d{4}"
                  r"|[1-4]\.\s*quartal\s+\d{4}|(?:1er|2e|2ème|3e|3ème|4e|4ème)\s+trimestre\s+\d{4}"
                  r"|(?:primer|segundo|tercer|cuarto)\s+trimestre\s+\d{4}"
                  r"|(?:eerste|tweede|derde|vierde)\s+kwartaal\s+\d{4}"),
    ("semestre", rf"(?<![\w])(?:H1|1H|HY|1HY|6M|S1|1S|HJ1|1HJ|1\.\s*HJ|HJ)\s*(?:FY\s*)?{_ANNO}"
                 r"|(?<![\w])\d{4}\s*H1(?![\w])"
                 r"|(?:first|1st)\s+half(?:[\s-]+year)?(?:\s+of)?\s+\d{4}"
                 r"|(?:1°|1\^|I|primo)\s+semestre\s+\d{4}"
                 r"|(?:1\.|erstes)\s*halbjahr(?:es)?\s+\d{4}"
                 r"|(?:1er|premier|primer)\s+semestre\s+(?:de\s+)?\d{4}"
                 r"|(?:eerste|1e)\s+halfjaar\s+\d{4}"),
    ("altro", rf"(?<![\w])(?:FY|GJ)\s*{_ANNO}"
              r"|(?:full[\s-]year|year|esercizio|geschäftsjahr|exercice|boekjaar|ejercicio)\s+\d{4}"),
    ("data", _DATA),
    ("var_pct", r"%\s*(?:change|chg\.?|var\.?|variation|variazione|ch\.|veränd\.?|veränderung|variación)"
                r"|(?:change|chg\.?|var\.?|variation|variazione|delta|Δ|veränderung|veränd\.?|écart|ecart"
                r"|verschil|mutatie|variación)\s*(?:in\s+)?\(?%\)?|%"),
    ("var_abs", r"(?:abs\.\s*)?(?:change|chg\.|variation|variazione|var\.|delta|Δ|veränderung|veränd\."
                r"|écart|ecart|verschil|mutatie|variación)"),
    ("nota", r"\b(?:notes?|nota|note\s+ref\.?|rif\.?|anhang|toelichting)\b"),
)
_RX_COLONNE = re.compile("|".join(f"(?P<c{i}>{rx})" for i, (_, rx) in enumerate(_COLONNE)), re.I)
_PERIODI = {"altro", "sei_mesi", "trimestre", "semestre", "data", "intervallo"}
_VARIAZIONI = {"var_pct", "var_abs"}
# Prova dei sei mesi o di un trimestre nel contesto dell'intestazione (intestazione + 2 righe sopra).
def _rx_mesi(da, a):
    """Intervallo di mesi senza anno («April - June», «1 April – 30 June», «April bis Juni», «aprile-giugno»)."""
    m1 = "|".join(sorted((re.escape(n) for n, k in _MESI.items() if k == da), key=len, reverse=True))
    m2 = "|".join(sorted((re.escape(n) for n, k in _MESI.items() if k == a), key=len, reverse=True))
    return (rf"(?<![\w])(?:\d{{1,2}}\.?\s*)?(?:{m1})\.?(?:\s+\d{{1,2}})?,?\s*(?:[-–]|bis|to|through|until|al|au|à|a)"
            rf"\s*(?:\d{{1,2}}\.?\s*)?(?:{m2})(?![\w])")


# v4: anche gli intervalli di mesi senza anno e le date numeriche senza anno («1.4.-30.6.»)
_RX_SEI = re.compile(r"six[\s-]*months?|\b6[\s-]*months?|first\s+half|half[\s-]*year|\bH1\b|\b1H\b|semestr"
                     r"|halbjahr|six\s+mois|halfjaar|sechs\s+monate|seis\s+meses|\b1\.\s?1\.\s*[-–]\s*30\.\s?6\."
                     r"|" + _rx_mesi(1, 6), re.I)
_RX_TRE = re.compile(r"three[\s-]*months?|\b3[\s-]*months?|quarter|trimestr|quartal|kwartaal|\bQ[1-4]\b|\b[1-4]Q\b"
                     r"|\b[1-4]T\b|\bT[1-4]\b|\bK[1-4]\b|tre\s+mesi|drei\s+monate|trois\s+mois|tres\s+meses"
                     r"|\b1\.\s?(?:1|4|7|10)\.\s*[-–]\s*3[01]\.\s?(?:3|6|9|12)\."
                     r"|" + "|".join(_rx_mesi(m, m + 2) for m in (1, 4, 7, 10)), re.I)

# Unita' e valuta del prospetto. Mai una cifra davanti o fra valuta e unita' («€11 million», «5 Mio. €»,
# «1,000 euros» sono prosa, non marcatori).
_VALUTE = (("EUR", r"€|eur(?:o|os)?\b"), ("USD", r"us\s*\$|\$|usd\b|us\s+dollars?"),
           ("GBP", r"£|gbp\b|pounds?(?:\s+sterling)?"),
           ("CHF", r"chf\b"), ("SEK", r"sek\b"), ("NOK", r"nok\b"), ("DKK", r"dkk\b"))
_SCALE = (("miliardi", 10 ** 9, r"bn\b|bln\b|billions?\b|miliardi\b|mrd\b\.?|mld\b|milliards?\b|mil\s+millones\b"),
          ("milioni", 10 ** 6, r"m\b|mn\b|mln\b|mio\b\.?|millions?\b|milioni\b|miljoen(?:en)?\b|millones\b"),
          ("migliaia", 10 ** 3, r"k\b|thousands?\b|migliaia\b|tsd\b\.?|tausend\b|milliers\b|duizend(?:en)?\b"
                                r"|miles\b|'000|/000\b|000\b"))
_V = "|".join(f"(?:{rx})" for _, rx in _VALUTE)
_S = "|".join(f"(?:{rx})" for _, _, rx in _SCALE)
_RX_UNITA = re.compile(
    rf"(?<![\w\d.,])(?<!\d\s)(?<!\d )(?P<v1>{_V})\s*[/']?\s*(?:in\s+|x\s+)?(?P<s1>{_S})"
    rf"|(?<![\w\d.,])(?<!\d\s)(?<!\d )(?:in\s+|en\s+)?(?P<s2>{_S})\s*(?:of\s+|di\s+|d'|de\s+|van\s+)?(?P<v2>{_V})"
    r"|(?<![\w])(?P<k>teur|keur|meur|t€|k€|m€)(?![\w])", re.I)
# Riga breve che ha l'aspetto di un marcatore di unita' («(€bn)», «(EUR mn)», «(in € millones)») ma che
# _RX_UNITA non sa leggere: unita' IGNOTA, mai quella della tabella prima.
_RX_MARCATORE = re.compile(r"^\(?\s*(?:(?:amounts?|figures|importi|valori|dati|beträge|montants|cifras|bedragen)"
                           r"\s+)?(?:(?:in|en|em|x)\s+)?(?:€|eur\b|euros?\b|us\$|\$|usd\b|£|gbp\b|chf\b)[^\d]{0,20}\)?$"
                           r"|^\([^\d()]{0,25}(?:€|\beur\b|\beuros?\b|\$|\busd\b|£)[^\d()]{0,25}\)$", re.I)
_RX_UNITA_UNO = re.compile(r"^\(?\s*(?:in\s+|en\s+)?(?:€|eur|euro|euros)\s*\)?$", re.I)

_CELLA = re.compile(r"^(?:\(?[-–−+]?\d[\d.,]*%?\)?%?|[-–—]|n\.?\s?[asmd]\.?|n\.?m\.?|nm|n\.?s\.?)$", re.I)
# richiamo di nota fra etichetta e celle: «(1)», «(*)», «*». Una cifra isolata vale come nota SOLO se le celle
# sono esattamente le colonne + 1 (v. _allinea).
_NOTA_PIEDE = re.compile(r"^\((?:\*+|\d{1,2}|[a-z])\)$|^\*+$", re.I)
# Migliaia separate da spazio NON divisibile (francese «1 234,5» con U+00A0/U+202F/U+2009): cifre unite prima
# di dividere la riga in celle. Con lo spazio normale si raggruppa solo se la lettura e' unica (_partizioni).
_SPAZIO_MIGLIAIA = re.compile(r"(?<=\d)[   ](?=\d{3}(?!\d))")

# Parole ammesse in un'intestazione oltre alle colonne (nient'altro con lettere: altrimenti e' una riga).
_PAROLE_INTESTAZIONE = set(_MESI) | {
    "restated", "rideterminato", "riesposto", "reported", "published", "pubblicato", "unaudited", "audited",
    "amounts", "importi", "values", "valori", "pro", "forma", "proforma", "at", "as", "of", "al", "and", "e",
    "the", "for", "period", "ended", "periodo", "chiuso", "to", "eur", "euro", "euros", "million", "millions",
    "thousand", "thousands", "milioni", "migliaia", "m", "mn", "mln", "k", "in", "months", "month", "mesi",
    "twelve", "six", "nine", "three", "sei", "nove", "tre", "dodici", "ultimi", "last", "chiusi",
    "mio", "tsd", "angepasst", "berichtet", "retraité", "retraite", "publié", "publie", "en", "milliers", "au",
    "le", "aangepast", "gerapporteerd", "miljoen", "duizend", "x", "und", "et", "millones", "miles", "de", "del",
    "y", "bn", "billion", "billions", "miliardi", "mrd"}

_NCI = (r"non[\s-]?controlling|minorit|\bterzi\b|minderheit|nicht\s+beherrschend|ne\s+donnant\s+pas"
        r"|minoritarios|no\s+dominantes|minderheidsbelang")
_RX_NCI = re.compile(_NCI, re.I)

_VOCI = (
    # (voce, tipo_periodo, misura, pattern dell'etichetta normalizzata, extra)
    ("ricavi", "durata", "riportato",
     r"(?:total\s+|net\s+)?(?:revenues?|sales|turnover)"
     r"|(?:totale\s+)?ricavi(?:\s+(?:delle\s+vendite(?:\s+e\s+(?:delle\s+)?prestazioni)?|netti|consolidati|totali))?"
     r"|umsatz(?:erl[oö]se)?|konzernumsatz|chiffre\s+d'affaires(?:\s+consolidé)?|produits\s+des\s+activités\s+ordinaires"
     r"|(?:netto[\s-]?)?omzet|opbrengsten|ingresos(?:\s+ordinarios)?|importe\s+neto\s+de\s+la\s+cifra\s+de\s+negocios"
     r"|cifra\s+de\s+negocios|ventas(?:\s+netas)?", {}),
    ("ebitda_rettificato", "durata", "rettificato",
     r"(?:adjusted|adj)\s+ebitda|ebitda\s+(?:adjusted|adj|rettificato|normali[sz]zato|ricorrente)"
     r"|(?:recurring|underlying)\s+ebitda|ebitda\s+\((?:adjusted|adj)\)|ebitda\s+before\s+special\s+items"
     r"|bereinigtes\s+ebitda|ebitda\s+(?:bereinigt|vor\s+sondereinflüssen)|ebitda\s+(?:ajusté|courant|récurrent)"
     r"|(?:aangepaste|onderliggende|genormaliseerde)\s+ebitda|ebitda\s+\((?:aangepast|bereinigt|ajusté)\)"
     r"|ebitda\s+(?:ajustado|recurrente)", {}),
    ("ebitda", "durata", "riportato",
     r"ebitda(?:\s+reported)?|margine\s+operativo\s+lordo(?:\s+\(ebitda\))?|ebitda\s+\(margine\s+operativo\s+lordo\)"
     r"|ebitda\s+(?:berichtet|publié)|excédent\s+brut\s+d'exploitation(?:\s+\(ebitda\))?|resultado\s+bruto\s+de\s+explotación",
     {}),
    ("utile_operativo_rettificato", "durata", "rettificato",
     r"(?:adjusted|adj)\s+(?:ebit|operating\s+(?:income|profit|result))"
     r"|(?:ebit|risultato\s+operativo|utile\s+operativo)\s+(?:adjusted|adj|rettificato)|ebit\s+before\s+special\s+items"
     r"|bereinigtes\s+(?:ebit|operatives\s+ergebnis|betriebsergebnis)|ebit\s+(?:bereinigt|ajusté|ajustado)"
     r"|résultat\s+opérationnel\s+(?:courant|ajusté)|(?:aangepast|onderliggend)\s+(?:ebit|bedrijfsresultaat)", {}),
    # «Operating result» (o «operatives Ergebnis» senza EBIT) e' una misura definita dall'emittente: mai
    # l'utile operativo riportato (revisione v3, punto 11).
    ("utile_operativo_rettificato", "durata", "misura dell'emittente",
     r"operating\s+result|operatives\s+ergebnis",
     {"definizione": "misura dell'emittente («operating result»), definizione dell'emittente"}),
    ("utile_operativo", "durata", "riportato",
     r"operating\s+(?:income|profit)(?:\s+\(ebit\))?|ebit|earnings\s+before\s+interest\s+and\s+taxe?s?(?:\s+\(ebit\))?|risultato\s+operativo(?:\s+\(ebit\))?"
     r"|utile\s+operativo|reddito\s+operativo|operatives\s+ergebnis\s+\(ebit\)|betriebsergebnis"
     r"|résultat\s+(?:opérationnel|d'exploitation)|bedrijfsresultaat|resultado\s+(?:de\s+explotación|operativo)"
     r"|beneficio\s+de\s+explotación", {}),
    ("utile_netto", "durata", "riportato",
     r"(?:net\s+)?(?:profit|income|result|earnings)(?:\s*/?\s*\(loss\))?(?:\s+for\s+the\s+period)?\s+attributable\s+to"
     r"\s+(?:the\s+)?(?:owners|equity\s+holders|shareholders|stockholders)\s+of\s+(?:the\s+)?.{2,60}"
     r"|group\s+net\s+(?:profit|income|result)|net\s+(?:profit|income|result)\s+(?:-\s+)?(?:of\s+the\s+)?group(?:\s+share)?"
     r"|(?:utile|risultato)(?:\s+\(perdita\))?\s+netto\s+(?:di\s+(?:competenza\s+del\s+)?gruppo|del\s+gruppo"
     r"|attribuibile\s+(?:ai\s+soci|agli\s+azionisti)\s+della\s+(?:controllante|capogruppo))"
     r"|(?:konzern)?ergebnis\s+nach\s+anteilen\s+(?:anderer\s+gesellschafter|nicht\s+beherrschender\s+gesellschafter)"
     r"|(?:den\s+)?(?:aktionären|anteilseignern)\s+der\s+.{2,60}\s+zuzurechnendes\s+(?:konzern)?(?:ergebnis|periodenergebnis)"
     r"|résultat\s+net(?:\s*[,(-]?\s*part\s+du\s+groupe\)?)|résultat\s+net\s+attribuable\s+aux\s+actionnaires\s+de\s+la\s+société\s+mère"
     r"|(?:netto\s?winst|nettoresultaat|netto\s+resultaat)\s+toe\s+te\s+rekenen\s+aan\s+(?:de\s+)?aandeelhouders.{0,60}"
     r"|(?:beneficio|resultado)\s+(?:neto\s+)?(?:atribuido|atribuible)\s+a\s+(?:la\s+sociedad\s+dominante"
     r"|los\s+accionistas\s+de\s+la\s+sociedad\s+dominante)",
     {"perimetro": "quota del gruppo"}),
    ("utile_netto_totale", "durata", "riportato",
     r"net\s+(?:profit|income|result|earnings)(?:\s*/?\s*\(loss\))?(?:\s+for\s+the\s+period)?"
     r"|profit(?:\s*/?\s*\(loss\))?\s+for\s+the\s+period|(?:earnings|profit|income)\s+after\s+tax(?:es)?|net\s+earnings"
     r"|net\s+income\s+\(from\s+continuing\s+and\s+discontinued\s+operations\)"
     r"|(?:utile|risultato)(?:\s*/?\s*\(perdita\))?\s+netto(?:\s+del\s+periodo)?|utile(?:\s+\(perdita\))?\s+del\s+periodo"
     r"|konzern(?:perioden)?ergebnis|periodenergebnis|ergebnis\s+nach\s+steuern|konzernergebnis\s+nach\s+steuern"
     r"|résultat\s+net(?:\s+consolidé|\s+de\s+la\s+période|\s+de\s+l'ensemble\s+consolidé)?"
     r"|netto\s?winst|nettoresultaat|netto\s+resultaat"
     r"|beneficio\s+neto|resultado\s+neto|resultado\s+del\s+(?:periodo|ejercicio)|beneficio\s+del\s+periodo", {}),
    ("debito_netto", "istante", "riportato",
     r"net\s+(?:financial\s+)?debt|net\s+indebtedness|net\s+borrowings|indebitamento\s+finanziario\s+netto"
     r"|debito\s+(?:finanziario\s+)?netto|netto[\s-]?finanzverschuldung|nettoverschuldung|nettofinanzschulden"
     r"|dette\s+(?:financière\s+)?nette|endettement\s+(?:financier\s+)?net|netto[\s-]?schuld"
     r"|deuda\s+(?:financiera\s+)?neta",
     {"segno": "come stampato (positivo); convenzione dell'emittente non verificata"}),
    ("cassa_netta", "istante", "riportato",
     r"net\s+cash(?:\s+position)?|cassa\s+netta|liquidit[aà]\s+netta|net\s+liquidity|nettoliquidit[aä]t"
     r"|netto[\s-]?finanzmittel|trésorerie\s+nette|netto\s?kas(?:positie)?|caja\s+neta|tesorería\s+neta",
     {"segno": "come stampato (positivo); convenzione dell'emittente non verificata"}),
    ("posizione_finanziaria_netta", "istante", "riportato",
     r"(?:net\s+financial\s+position|posizione\s+finanziaria\s+netta)(?:\s+\((?:debt|indebitamento|debito)\))?"
     r"|netto[\s-]?finanzposition|position\s+financière\s+nette|netto\s+financiële\s+positie"
     r"|posición\s+financiera\s+neta",
     {"segno": "come stampato; convenzione di segno dell'emittente ignota (non verificata)"}),
    ("flusso_cassa_operativo", "durata", "riportato",
     r"(?:net\s+)?cash\s+flows?\s+(?:from|generated\s+(?:from|by)|provided\s+by)\s+operating\s+activities"
     r"|net\s+cash\s+(?:from|generated\s+(?:from|by)|provided\s+by)\s+operating\s+activities"
     r"|flussi?\s+(?:di\s+cassa\s+)?(?:netti?\s+)?(?:generati?\s+)?(?:dalle|da)\s+attivit[aà]\s+operative"
     r"|cash\s+flow\s+from\s+operations"
     r"|(?:cash[\s-]?flow|mittelzufluss|mittelabfluss|zahlungsmittelzufluss)\s+aus\s+(?:der\s+)?(?:laufenden\s+)?"
     r"(?:geschäftstätigkeit|betrieblicher\s+tätigkeit)"
     r"|flux\s+(?:nets?\s+)?de\s+trésorerie\s+(?:nets?\s+)?(?:liés\s+aux|générés\s+par\s+les|provenant\s+des)\s+"
     r"activités\s+(?:opérationnelles|d'exploitation)"
     r"|(?:netto\s+)?kasstro(?:om|men)\s+uit\s+(?:operationele|bedrijfs)\s*activiteiten"
     r"|flujos?\s+(?:netos?\s+)?de\s+(?:efectivo|caja)\s+(?:netos?\s+)?(?:de|procedentes\s+de)\s+las\s+actividades"
     r"\s+de\s+explotación", {}),
    ("free_cash_flow", "durata", "riportato",
     r"free\s+cash[\s-]?flows?(?:\s+\(fcf\))?|fcf|flusso\s+di\s+cassa\s+libero|freier\s+cash[\s-]?flow"
     r"|flux\s+de\s+trésorerie\s+disponible|vrije\s+kasstroom|flujo\s+de\s+caja\s+libre", {"definizione": "come stampata"}),
    ("free_cash_flow", "durata", "riportato",
     r"operating\s+free\s+cash[\s-]?flows?|operativer\s+free\s+cash[\s-]?flow",
     {"definizione": "operating free cash flow (definizione dell'emittente), come stampata"}),
    ("free_cash_flow", "durata", "riportato",
     r"levered\s+free\s+cash[\s-]?flows?", {"definizione": "levered (dopo gli oneri finanziari), come stampata"}),
    ("free_cash_flow", "durata", "riportato",
     r"unlevered\s+free\s+cash[\s-]?flows?", {"definizione": "unlevered (prima degli oneri finanziari), come stampata"}),
)
_RX_VOCI = [(v, t, m, re.compile(rx), extra) for v, t, m, rx, extra in _VOCI]
_PRIORITA_DEFINIZIONE = [None] + list(dict.fromkeys(e["definizione"] for *_, e in _VOCI if e.get("definizione")))
_GRUPPO_DOPO_ATTRIBUIBILE = re.compile(
    r"(?:the\s+)?(?:owners|equity\s+holders|shareholders|stockholders)\s+of\s+(?:the\s+)?.{2,60}"
    r"|soci\s+della\s+controllante|azionisti\s+della\s+capogruppo|gruppo|group|di\s+competenza\s+del\s+gruppo"
    r"|(?:aktionäre|anteilseigner)\s+(?:der|des)\s+.{2,60}|part\s+du\s+groupe|groupe"
    r"|actionnaires\s+de\s+la\s+société\s+mère|aandeelhouders\s+van\s+.{2,60}|groep"
    r"|(?:la\s+)?sociedad\s+dominante|accionistas\s+de\s+la\s+sociedad\s+dominante")
_ATTRIBUIBILE = re.compile(r"(?:attributable\s+to|attribuibile\s+a|di\s+cui|of\s+which|davon(?:\s+entfallen\s+auf)?"
                           r"|dont|attribuable\s+(?:à|aux)|toe\s+te\s+rekenen\s+aan|waarvan|atribuible\s+a)\s*:?$")
_RX_COMPLESSIVO = re.compile(r"comprehensive|complessiv|gesamtergebnis|résultat\s+global|global\s+income"
                             r"|totaalresultaat|resultado\s+global", re.I)

# Ordine di uscita e gruppi: la prima voce trovata del gruppo esce, le altre del gruppo restano alternative.
ORDINE = ("ricavi", "ebitda", "ebitda_rettificato", "utile_operativo", "utile_operativo_rettificato",
          "utile_netto", "debito_netto", "cassa_netta", "posizione_finanziaria_netta",
          "flusso_cassa_operativo", "free_cash_flow")
_RICHIESTE = {"ricavi": ("ricavi",), "ebitda": ("ebitda", "ebitda_rettificato"),
              "utile_operativo": ("utile_operativo", "utile_operativo_rettificato"),
              "utile_netto": ("utile_netto",),
              "debito_o_cassa_netta": ("debito_netto", "cassa_netta", "posizione_finanziaria_netta"),
              "free_cash_flow": ("free_cash_flow",)}
_SEGNO_POSITIVO = ("debito_netto", "cassa_netta")


# Tabelle che non sono del gruppo intero: segmenti/settori/aree (titolo sopra l'intestazione) e prospetti
# dell'utile per azione (utile rettificato per gli strumenti ibridi, numero di azioni): righe mai lette come voce.
_RX_SEGMENTO = re.compile(r"\bsegment|\bsegmento|\bsettore\b|\bsettori\b|\bdivision|\bbusiness\s+(?:unit|area)"
                          r"|\bregion|area\s+geografica|geographic|\bsparte|\bsegmente?\b|gesch[aä]ftsbereich"
                          r"|\bsecteurs?\b|\bdivisie|\bsegmentos?\b", re.I)
# Tabelle del gruppo intero (titolo della tabella o testata della pagina): decidono fra valori discordi e sono
# la prova di perimetro senza la quale nessun valore esce.
_RX_GRUPPO = re.compile(r"consolidat|konzern|highlights|principali\s+dati|dati\s+di\s+sintesi|key\s+figures"
                        r"|\bgroup\b|\bgruppo\b|\bgroupe\b|consolidé|chiffres\s+clés|\bgroep\b|geconsolideerd"
                        r"|kerncijfers|kennzahlen|\bgrupo\b|consolidad|principales\s+magnitudes", re.I)
# Prospetti primari: in una relazione CONSOLIDATA senza bilancio separato sono del gruppo intero.
_RX_PROSPETTO = re.compile(r"income\s+statement|statements?\s+of\s+(?:income|profit|cash\s+flows?|financial\s+position)"
                           r"|cash\s+flows?\s+statement|conto\s+economico|rendiconto\s+finanziario|stato\s+patrimoniale"
                           r"|gewinn[-\s]und[-\s]verlustrechnung|kapitalflussrechnung|bilanz\b|compte\s+de\s+résultat"
                           r"|tableau\s+des\s+flux|winst[-\s]en[-\s]verliesrekening|kasstroomoverzicht"
                           r"|cuenta\s+de\s+resultados|estado\s+de\s+flujos", re.I)
_RX_SEPARATO = re.compile(r"separate\s+financial\s+statements|bilancio\s+separato|bilancio\s+d'esercizio\s+della"
                          r"|einzelabschluss|comptes\s+sociaux|estados\s+financieros\s+individuales"
                          r"|enkelvoudige\s+jaarrekening", re.I)
# Attivita' cessate / destinate alla vendita: perimetro parziale, mai il gruppo intero.
_RX_DISMESSE = re.compile(r"discontinued\s+operations|held\s+for\s+sale|attivit[aà]\s+(?:operative\s+)?cessate"
                          r"|destinat[ei]\s+alla\s+(?:dismissione|vendita)|aufgegebene\s+gesch[aä]ftsbereiche"
                          r"|activités\s+abandonnées|beëindigde\s+bedrijfsactiviteiten|actividades\s+interrumpidas", re.I)
# Titolo di sezione: riga breve tutta in maiuscolo (con eventuale numerazione «(6)», «2.», «G.1»).
_TITOLO_SEZIONE = re.compile(r"(?:\(?[A-Z]?\d{1,2}(?:\.\d{1,2})*[.)]?\s+)?[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ&'’,\- ]{3,80}")
# Parole di gruppo «forti» (non «key figures»/«highlights», che titolano anche le tabelle di segmento).
_RX_GRUPPO_FORTE = re.compile(r"consolidat|konzern|\bgroup\b|\bgruppo\b|\bgroupe\b|consolidé|\bgroep\b"
                              r"|geconsolideerd|\bgrupo\b|consolidad", re.I)
_RX_PER_AZIONE = re.compile(r"per\s+share|per\s+azione|weighted\s+average\s+number|numero\s+medio\s+ponderato"
                            r"|je\s+aktie|par\s+action|per\s+aandeel|nombre\s+moyen\s+pondéré|gewichtete|por\s+acción", re.I)


class Rifiuto(ValueError):
    """Documento che non puo' dare numeri (identita', look-ahead, byte cambiati, fonte esterna)."""


def _data(testo):
    t = " ".join(testo.lower().replace(",", " ").replace(".", " . ").split())
    m = re.fullmatch(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})", testo.strip())
    try:
        if m:
            g, mm, a = (int(x) for x in m.groups())
            return date(a + 2000 if a < 100 else a, mm, g)
        parole = [p for p in re.split(r"[\s.]+", t) if p and p != "de"]
        mese = next((_MESI[p] for p in parole if p in _MESI), None)
        numeri = [int(re.sub(r"\D", "", p)) for p in parole if re.match(r"\d", p)]
        anni = [n for n in numeri if n >= 1900]
        giorni = [n for n in numeri if n < 32]
        if mese and len(anni) == 1 and len(giorni) == 1:
            return date(anni[0], mese, giorni[0])
    except ValueError:
        return None
    return None


def _anno(testo):
    m = re.search(r"(\d{4}|\d{2})(?!.*\d)", testo)
    if not m:
        return None
    a = int(m.group(1))
    return a + 2000 if a < 100 else a


def _un_anno_prima(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 febbraio
        return d.replace(year=d.year - 1, day=28)


def _mesi_intervallo(grezzo):
    """(mesi, fine) di un intervallo «1.1.-30.6.2026» / «1 January - 30 June 2026»; (None, None) se illeggibile."""
    sinistra, destra = re.split(r"\s*[-–]\s*", grezzo, maxsplit=1)
    fine = _data(re.search(_DATA, destra, re.I).group(0)) if re.search(_DATA, destra, re.I) else None
    if fine is None:
        return None, None
    m = re.match(r"\s*(\d{1,2})[./](\d{1,2})", sinistra)
    if m:
        mese_inizio = int(m.group(2))
    else:
        mese_inizio = next((_MESI[p] for p in re.split(r"[\s.,]+", sinistra.lower()) if p in _MESI), None)
    if not mese_inizio:
        return None, fine
    return (fine.month - mese_inizio) % 12 + 1, fine


def classifica_colonne(testo, fine, contesto="", contesto_tre=""):
    """[(tipo, testo)] delle colonne di un'intestazione.

    tipo: corrente / confronto (sei mesi dichiarati: valgono per durate e istanti), corrente_istante /
    confronto_istante (data nuda senza prova dei sei mesi: SOLO per le voci a istante), altro, var_pct, var_abs,
    nota. Corrente = periodo che chiude il `fine`; confronto = stesso periodo un anno prima. H1 dell'anno solo
    con chiusura al 30/06. `contesto` = righe sopra l'intestazione (prova dei sei mesi o del trimestre)."""
    colonne = []
    solare = (fine.month, fine.day) == (6, 30)
    prima = _un_anno_prima(fine)
    tutto = f"{contesto} {testo}"
    sei = bool(_RX_SEI.search(tutto)) and not _RX_TRE.search(tutto)
    sei = sei and not _RX_TRE.search(contesto_tre)  # v4: un trimestre due righe sopra basta a escludere

    def lato(d, sei_mesi):
        suffisso = "" if sei_mesi else "_istante"
        return "corrente" + suffisso if d == fine else "confronto" + suffisso if d == prima else "altro"

    for m in _RX_COLONNE.finditer(testo):
        tipo = _COLONNE[int(m.lastgroup[1:])][0]
        grezzo = m.group(0)
        if tipo == "intervallo":
            mesi, d = _mesi_intervallo(grezzo)
            tipo = lato(d, True) if mesi == 6 else "altro"
        elif tipo == "sei_mesi":
            d = _data(re.search(_DATA, grezzo, re.I).group(0)) if re.search(_DATA, grezzo, re.I) else None
            tipo = lato(d, True)
        elif tipo == "data":
            tipo = lato(_data(grezzo), sei)
        elif tipo == "semestre":
            a = _anno(grezzo)
            tipo = ("corrente" if solare and a == fine.year else
                    "confronto" if solare and a == fine.year - 1 else "altro")
        elif tipo == "trimestre":
            tipo = "altro"
        colonne.append((tipo, grezzo))
    return colonne


def _residuo_ammesso(riga):
    """True se, tolte colonne, unita', note e parole d'intestazione, non restano parole: e' un'intestazione."""
    resto = _RX_UNITA.sub(" ", _RX_COLONNE.sub(" ", riga))
    resto = re.sub(r"\((?:\*+|\d{1,2}|[a-z])\)|\*+|[()€$£%,.;:/'’\-–]", " ", resto, flags=re.I)
    parole = re.findall(r"[^\W\d_]+", resto)
    return all(p.lower() in _PAROLE_INTESTAZIONE for p in parole)


def _ha_periodo(riga):
    return any(_COLONNE[int(m.lastgroup[1:])][0] in _PERIODI for m in _RX_COLONNE.finditer(riga))


def _solo_variazioni(riga):
    """Riga d'intestazione fatta solo di colonne di variazione («Change», «Change (%)»), senza periodi."""
    tipi = [_COLONNE[int(m.lastgroup[1:])][0] for m in _RX_COLONNE.finditer(riga)]
    return bool(tipi) and all(t in _VARIAZIONI for t in tipi) and _residuo_ammesso(riga)


def _unita(riga):
    """(scala_nome, fattore, valuta) dell'ultimo marcatore nella riga, None se non c'e'.

    Fattore None = marcatore di unita' non riconosciuto (unita' ignota): la tabella non ha numeri."""
    trovati = list(_RX_UNITA.finditer(riga))
    if not trovati:
        pulita = riga.strip()
        if len(pulita) <= 40 and not re.search(r"\d", pulita):
            if _RX_UNITA_UNO.fullmatch(pulita):
                return "unita", 1, "EUR"
            if _RX_MARCATORE.fullmatch(pulita):
                return "ignota", None, None
        return None
    m = trovati[-1]
    if m.group("k"):
        k = m.group("k").lower()
        return ("migliaia", 10 ** 3, "EUR") if k in ("teur", "keur", "t€", "k€") else ("milioni", 10 ** 6, "EUR")
    valuta_txt, scala_txt = (m.group("v1"), m.group("s1")) if m.group("v1") else (m.group("v2"), m.group("s2"))
    valuta = next(c for c, rx in _VALUTE if re.fullmatch(rx, valuta_txt.strip(), re.I))
    nome, fattore = next((n, f) for n, f, rx in _SCALE if re.fullmatch(rx, scala_txt.strip(), re.I))
    return nome, fattore, valuta


def formato_numerico(testo):
    """'en' (1,234.5), 'it' (virgola decimale: 1.234,5 in it/de/fr/es/nl) o None (nessuna prova o prove discordi)."""
    en = len(re.findall(r"(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d+(?![\d,])|(?<![\d.,])\d{1,3}\.\d{1,2}\s?%", testo))
    it = len(re.findall(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+,\d+(?![\d.])|(?<![\d.,])\d{1,3},\d{1,2}\s?%", testo))
    if en and en >= 5 * it:
        return "en"
    if it and it >= 5 * en:
        return "it"
    return None


def _valore(cella, formato):
    """Decimal della cella stampata; None per cella vuota («-», n.s.)."""
    c = cella.strip()
    if re.fullmatch(r"[-–—]|n\.?\s?[asmd]\.?|n\.?m\.?|nm|n\.?s\.?", c, re.I):
        return None
    negativo = c.startswith("(") and c.endswith(")") or c.lstrip("(")[:1] in "-–−"
    cifre = c.strip("()%").lstrip("-–−+")
    if formato == "en":
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", cifre):
            return None
        cifre = cifre.replace(",", "")
    else:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?", cifre):
            return None
        cifre = cifre.replace(".", "").replace(",", ".")
    try:
        v = Decimal(cifre)
    except InvalidOperation:
        return None
    return -v if negativo else v


def _celle_finali(riga):
    """(etichetta, [celle]) con le celle numeriche in coda alla riga («16.4 %» unito)."""
    pezzi = riga.split()
    celle = []
    while pezzi:
        p = pezzi[-1]
        if p == "%" and len(pezzi) > 1 and re.fullmatch(r"\(?[-–−]?\d[\d.,]*\)?", pezzi[-2]):
            pezzi[-2:] = [pezzi[-2] + "%"]
            continue
        if not _CELLA.match(p):
            break
        celle.insert(0, pezzi.pop())
    return " ".join(pezzi), celle


def _partizioni(celle, limite=64):
    """Raggruppamenti delle celle quando le migliaia sono separate da uno spazio normale («41 873 46 219»):
    un numero = primo gruppo di 1-3 cifre (con «(» o meno davanti) + gruppi di esattamente 3 cifre (l'ultimo
    puo' portare decimali, «)» o «%»). Ogni raggruppamento e' una lista di (cella, [indici]); al piu' `limite`."""
    testa = re.compile(r"^\(?[-–−+]?\d{1,3}$")
    coda = re.compile(r"^\d{3}$")
    fine = re.compile(r"^\d{3}(?:,\d+)?\)?%?$")
    out = []

    def passo(i, corrente):
        if len(out) >= limite:
            return
        if i == len(celle):
            out.append(list(corrente))
            return
        passo(i + 1, corrente + [(celle[i], [i])])  # la cella da sola
        if testa.match(celle[i]):
            numero, indici = celle[i], [i]
            for j in range(i + 1, len(celle)):
                if fine.match(celle[j]):
                    passo(j + 1, corrente + [(numero + celle[j], indici + [j])])
                if not coda.match(celle[j]):
                    break
                numero += celle[j]
                indici = indici + [j]
    passo(0, [])
    return out


def _mappa(colonne, celle):
    """{indice colonna: cella} (celle allineate a destra) o None se nessuna o piu' mappe valide.

    Possono mancare solo colonne di variazione e di nota. Le colonne di variazione accettano qualsiasi cella
    (%, numero, n.s.: non si leggono mai); le altre mai una percentuale."""
    n, k = len(colonne), len(celle)
    if k < n:
        mancabili = [i for i, (t, _) in enumerate(colonne) if t in _VARIAZIONI or t == "nota"]
        tentativi = [[i for i in range(n) if i not in tolte] for tolte in combinations(mancabili, n - k)] \
            if n - k <= len(mancabili) else []
    else:
        tentativi = [list(range(n))]
    valide = []
    for indici in tentativi:
        scelte = celle[-len(indici):] if indici else []
        if len(scelte) != len(indici):
            continue
        ok = True
        for i, cella in zip(indici, scelte):
            pct = "%" in cella or re.fullmatch(r"n\.?\s?[sm]\.?|nm|n\.?m\.?", cella, re.I) is not None
            if colonne[i][0] not in _VARIAZIONI and pct:
                ok = False
        if ok:
            valide.append(dict(zip(indici, scelte)))
    return valide[0] if len(valide) == 1 else None


def _allinea(colonne, celle, formato, incerte):
    """Mappa unica celle -> colonne. Con la virgola decimale si provano anche i raggruppamenti delle migliaia
    separate da spazio; una sola lettura deve combaciare. A sinistra delle celle lette possono restare solo
    richiami di nota «(1)»/«(*)»; una cifra isolata vale come nota solo se le celle sono le colonne + 1."""
    letture = _partizioni(celle) if formato == "it" else [[(c, [i]) for i, c in enumerate(celle)]]
    trovate = {}
    for gruppo in letture:
        g_celle = [c for c, _ in gruppo]
        m = _mappa(colonne, g_celle)
        if m is None:
            continue
        resto = g_celle[:len(g_celle) - len(m)]
        diretta = all(len(ii) == 1 for _, ii in gruppo)
        ok = True
        for pos, c in enumerate(resto):
            if _NOTA_PIEDE.match(c):
                continue
            cifra_nota = (diretta and not incerte and c.isdigit() and len(c) == 1 and c != "0"
                          and len(resto) == 1 and len(g_celle) == len(colonne) + 1
                          and not (formato == "it" and re.match(r"^\d{3}(?:,\d+)?\)?$", g_celle[pos + 1])))
            if not cifra_nota:
                ok = False
        if ok:
            trovate[tuple(sorted(m.items()))] = m
    return next(iter(trovate.values())) if len(trovate) == 1 else None


def _normalizza(etichetta):
    e = re.sub(r"\s+[A-Z]$", "", etichetta.strip())  # lettera di rimando («Net financial debt G»)
    e = re.sub(r"(?<=[^\W\d_]{3})\d{1,2}(?:,\s*\d{1,2})*(?=\s|$)", "", e)  # nota in apice incollata («EBITDA1»)
    e = e.lower().replace("’", "'").replace("adj.", "adj").replace("&", " and ")
    e = re.sub(r"\((?:\*+|\d{1,2}|[a-z]|[ivx]+)\)|\*+|\b\d{1,2}\)$", " ", e)
    e = re.sub(r"\s*/\s*", "/", e)
    e = e.replace("profit/(loss)", "profit (loss)").replace("utile/(perdita)", "utile (perdita)")
    e = re.sub(r"[\s:;,.–-]+$", "", " ".join(e.split()))
    return re.sub(r"^[\s:;,.–-]+", "", e)


def _voce_di(etichetta):
    for voce, tipo, misura, rx, extra in _RX_VOCI:
        if rx.fullmatch(etichetta):
            if voce == "utile_netto" and _RX_NCI.search(etichetta):
                # «attributable to owners of the parent AND non-controlling interests»: e' il totale
                return next((v, t, m, x) for v, t, m, r, x in _RX_VOCI if v == "utile_netto_totale")
            return voce, tipo, misura, extra
    return None


def _testo_linea(linea):
    return " ".join(w for w, _, _ in linea)


def _spazi_parole(linea):
    """[(inizio, fine)] dei caratteri di ogni parola nel testo della riga visiva."""
    out, pos = [], 0
    for w, _, _ in linea:
        out.append((pos, pos + len(w)))
        pos += len(w) + 1
    return out


def _colonne_visive(linee, fine, contesto, contesto_tre=""):
    """(colonne, [(x0, x1)], testo) dell'intestazione dalle righe visive del blocco.

    Ogni riga si divide in colonne riconosciute (regex) e parole avanzate; le parole avanzate e le colonne di
    righe diverse che si sovrappongono in x si impilano (un'intestazione su piu' righe: «12 months» / «ended
    June» / «30, 2026»). Ogni pila si legge dall'alto in basso; ogni colonna ha la x delle sue parole."""
    pezzi = []
    for k, linea in enumerate(linee):
        testo, spazi = _testo_linea(linea), _spazi_parole(linea)
        usate = set()
        for m in _RX_UNITA.finditer(testo):
            usate.update(n for n, (a, b) in enumerate(spazi) if a < m.end() and b > m.start())
        for m in _RX_COLONNE.finditer(testo):
            idx = [n for n, (a, b) in enumerate(spazi) if a < m.end() and b > m.start() and n not in usate]
            if not idx:
                continue
            usate.update(idx)
            pezzi.append({"riga": k, "testo": " ".join(linea[n][0] for n in idx), "x0": min(linea[n][1] for n in idx),
                          "x1": max(linea[n][2] for n in idx), "colonna": True})
        for n, (w, x0, x1) in enumerate(linea):
            if n in usate or re.fullmatch(r"\(?\*+\)?|\(\d{1,2}\)|[€$£]", w):
                continue
            pezzi.append({"riga": k, "testo": w, "x0": x0, "x1": x1, "colonna": False})
    padre = list(range(len(pezzi)))

    def radice(a):
        while padre[a] != a:
            padre[a] = padre[padre[a]]
            a = padre[a]
        return a

    for a in range(len(pezzi)):
        for b in range(a + 1, len(pezzi)):
            pa, pb = pezzi[a], pezzi[b]
            if pa["colonna"] and pb["colonna"]:
                continue  # due colonne riconosciute non si fondono mai
            sovrapposte = min(pa["x1"], pb["x1"]) - max(pa["x0"], pb["x0"]) > -1
            vicine = pa["riga"] == pb["riga"] and abs(min(pa["x1"], pb["x1"]) - max(pa["x0"], pb["x0"])) < 6 \
                and not (pa["colonna"] or pb["colonna"])
            if (pa["riga"] != pb["riga"] and sovrapposte) or vicine:
                padre[radice(a)] = radice(b)
    pile = {}
    for n, pz in enumerate(pezzi):
        pile.setdefault(radice(n), []).append(pz)
    colonne_x, testi = [], []
    for pila in sorted(pile.values(), key=lambda q: min(z["x0"] for z in q)):
        pila = sorted(pila, key=lambda z: (z["riga"], z["x0"]))
        testo, spazi = "", []
        for z in pila:
            spazi.append((len(testo) + (1 if testo else 0), z))
            testo = f"{testo} {z['testo']}" if testo else z["testo"]
        for m in _RX_COLONNE.finditer(testo):
            coperti = [z for a, z in spazi if a < m.end() and a + len(z["testo"]) > m.start()]
            colonne_x.append((min(z["x0"] for z in coperti), max(z["x1"] for z in coperti)))
            testi.append(m.group(0))
    confine = None
    n = len(testi)
    norm = [" ".join(t.lower().split()) for t in testi]
    if n >= 4 and n % 2 == 0 and norm[:n // 2] == norm[n // 2:] and colonne_x[n // 2 - 1][1] < colonne_x[n // 2][0]:
        # due tabelle affiancate con le stesse colonne: si legge la sinistra; la destra si rilegge a parte
        confine = colonne_x[n // 2 - 1][1] + 4
        testi, colonne_x = testi[:n // 2], colonne_x[:n // 2]
    unito = " ; ".join(testi)
    colonne = classifica_colonne(unito, fine, contesto=contesto, contesto_tre=contesto_tre)
    if len(colonne) != len(colonne_x):
        return None, None, unito, None
    return colonne, colonne_x, unito, confine


def _parole_cella(linea, formato):
    """Parole della riga con le celle ricomposte: «16.4» «%» -> «16.4%»; con la virgola decimale le migliaia
    separate da uno spazio stretto (meno di 4 punti) si uniscono («41 873» -> «41873»)."""
    out = []
    for w, x0, x1 in linea:
        if out and w == "%" and re.fullmatch(r"\(?[-–−]?\d[\d.,]*\)?", out[-1][0]):
            out[-1] = (out[-1][0] + "%", out[-1][1], x1)
            continue
        if (out and formato == "it" and x0 - out[-1][2] < 4 and re.fullmatch(r"\(?[-–−+]?\d{1,3}(?:\d{3})*", out[-1][0])
                and re.fullmatch(r"\d{3}(?:,\d+)?\)?%?", w)):
            out[-1] = (out[-1][0] + w, out[-1][1], x1)
            continue
        out.append((w, x0, x1))
    return out


def _mappa_visiva(colonne, colonne_x, linea, formato, confine=None):
    """(mappa {colonna: cella} o None, etichetta, numero di celle sotto le colonne).

    Le celle si assegnano alla colonna con cui si sovrappongono in x (o alla piu' vicina entro il margine);
    due celle nella stessa colonna, una cella fuori da ogni colonna o una parola non numerica sotto le colonne
    = riga non allineata (None). Le parole oltre l'ultima colonna (tabella accanto) non contano."""
    centri = sorted((a + b) / 2 for a, b in colonne_x)
    passo = min((b - a for a, b in zip(centri, centri[1:])), default=40)
    margine = max(6.0, 0.45 * passo)
    xmin, xmax = min(a for a, _ in colonne_x) - margine, max(b for _, b in colonne_x) + margine
    if confine is not None:
        xmax = min(xmax, confine)  # tabella affiancata: oltre il confine e' l'altra tabella
    parole = _parole_cella(linea, formato)
    # etichetta = parole prima della prima cella sotto le colonne (un'etichetta lunga puo' sconfinare);
    # dopo la prima cella, una parola non numerica sotto le colonne rende la riga non allineata
    sinistra, celle = [], []
    for w, x0, x1 in parole:
        if x0 > xmax:
            continue  # tabella accanto
        if not celle and (not _CELLA.match(w) or (x0 + x1) / 2 < xmin):
            sinistra.append((w, x0, x1))
            continue
        if not _CELLA.match(w):
            return None, " ".join(q[0] for q in sinistra), len(celle)
        celle.append((w, x0, x1))
    # v4: un grande stacco in x separa l'etichetta da cio' che sta a sinistra (indice laterale, colonna di
    # navigazione): l'etichetta e' solo il gruppo di parole piu' vicino alle colonne
    # v5: i gruppi separati da stacchi; un gruppo fatto solo di marcatori di unita' («€ million», colonna
    # d'unita' fra etichetta e numeri) non e' l'etichetta: l'etichetta e' l'ultimo gruppo con parole proprie
    gruppi, corrente = [], []
    for q in sinistra:
        if corrente and q[1] - corrente[-1][2] > 25:
            gruppi.append(corrente)
            corrente = []
        corrente.append(q)
    if corrente:
        gruppi.append(corrente)
    propri = [g for g in gruppi if not _solo_unita(" ".join(w for w, _, _ in g))
              and any(re.search(r"[^\W\d_]{2,}", w) for w, _, _ in g)]
    sinistra = propri[-1] if propri else []
    etichetta = " ".join(w for w, _, _ in sinistra if not _NOTA_PIEDE.match(w) and not re.fullmatch(r"[1-9]", w))
    if not celle:
        return None, etichetta, 0
    mappa = {}
    for w, x0, x1 in celle:
        sovr = [(min(x1, b) - max(x0, a), n) for n, (a, b) in enumerate(colonne_x) if min(x1, b) - max(x0, a) > 0]
        if len(sovr) == 1 or (len(sovr) > 1 and sorted(sovr)[-1][0] > 2 * sorted(sovr)[-2][0]):
            n = max(sovr)[1]
        elif not sovr:
            dist = sorted((abs((x0 + x1) / 2 - (a + b) / 2), n) for n, (a, b) in enumerate(colonne_x))
            if dist[0][0] > margine or (len(dist) > 1 and dist[1][0] - dist[0][0] < 0.25 * passo):
                return None, etichetta, len(celle)
            n = dist[0][1]
        else:
            return None, etichetta, len(celle)
        if n in mappa:
            return None, etichetta, len(celle)
        pct = "%" in w or re.fullmatch(r"n\.?\s?[sm]\.?|nm|n\.?m\.?", w, re.I) is not None
        if colonne[n][0] not in _VARIAZIONI and pct:
            return None, etichetta, len(celle)
        mappa[n] = w
    return mappa, etichetta, len(celle)


def _righe_pagina(testo_pagina, fine, formato, visive=None, documento=None, eredita=None):
    """Righe di tabella lette nella pagina: dict con etichetta, colonne, celle, unita'.

    `visive` = righe della pagina in ordine visivo, [[(parola, x0, x1)]] (pdfplumber): righe, intestazioni e
    celle si leggono dalle coordinate (colonne per sovrapposizione x). Senza (prove su testo): righe del testo,
    celle assegnate per ordine da destra."""
    documento = documento or {}
    if visive is not None:
        righe = [_testo_linea(linea) for linea in visive]
    else:
        righe = [_SPAZIO_MIGLIAIA.sub("", r).rstrip() for r in testo_pagina.split("\n")]
    colonne_x, affiancate, confine_tab = None, [], None
    # testata corrente della pagina = prima riga non vuota (le righe dopo possono essere l'indice laterale
    # ripetuto su ogni pagina, che nomina «group» e «segments» ovunque)
    testa_pagina = next((r.strip() for r in righe if r.strip()), "")
    if eredita:
        testa_pagina = eredita["testa"]  # tabella affiancata: testata e titolo della pagina originale
    out, colonne, unita, testa_colonne, tabella, tabelle = [], None, None, None, None, []
    unita_in_attesa, righe_tabella = None, 0
    etichette = []          # righe di sola etichetta (etichette spezzate su piu' righe)
    viste = []              # etichette delle righe di IMPORTI gia' lette (contesto «attributable to»)
    i = 0
    while i < len(righe):
        riga = righe[i]
        riga_tab = riga
        if visive is not None and colonne_x:
            # v4: unita' e celle solo dalle parole dentro il confine della tabella (non dalla tabella accanto)
            limite_tab = confine_tab if confine_tab is not None else max(b for _, b in colonne_x) + 6
            riga_tab = " ".join(w for w, x0, _ in visive[i] if x0 <= limite_tab)
        con_celle = bool(_celle_finali(riga_tab)[1]) and bool(re.search(r"[^\W\d_]{3,}", _RX_UNITA.sub(" ", riga_tab)))
        u = _unita(riga_tab)  # la prosa con cifre («a fee of 1,000 euros») non e' un marcatore: lookbehind in _RX_UNITA
        unita_riga = None
        if u and colonne is not None and con_celle:
            unita_riga = u  # unita' nella riga («Sales € million 3,289 ...»): vale per la riga, la tabella resta
        elif u and not _ha_periodo(riga):
            if colonne is not None and righe_tabella == 0:
                unita = u  # marcatore subito sotto l'intestazione: e' di questa tabella
            else:
                colonne = None  # marcatore di unita' = nuova tabella: l'intestazione precedente non vale piu'
                unita_in_attesa = u
        if riga.strip() and _ha_periodo(riga) and _residuo_ammesso(riga):
            blocco = [riga]
            j = i + 1
            while j < len(righe) and righe[j].strip():
                seguente = righe[j]
                testa_cella = _CELLA.match(seguente.split()[0])
                if len(seguente) <= 60 and _residuo_ammesso(seguente) and not testa_cella:
                    blocco.append(seguente)
                elif (re.search(rf"(?:{_MESE})\.?\s*$", blocco[-1], re.I)
                      and re.match(r"\s*\d{1,2},?\s+\d{4}", seguente) and _residuo_ammesso(seguente)):
                    blocco.append(seguente)  # data spezzata su due righe («December» / «31, 2025»)
                elif (visive is not None and len(seguente) <= 25 and _residuo_ammesso(seguente)
                      and all(any(min(x1, b) - max(x0, a) > 0 for r in visive[i:j] for _, a, b in r)
                              for _, x0, x1 in visive[j])):
                    blocco.append(seguente)  # pezzo d'intestazione sotto le parole dell'intestazione («30, 2026»)
                else:
                    break
                j += 1
            sopra = [r.strip() for r in righe[max(0, i - 6):i] if r.strip()]
            titolo = [r for r in sopra if not _solo_unita(r)][-3:]
            # prova dei sei mesi / del trimestre: titolo e righe sopra l'intestazione, MAI la testata ricorrente
            # della pagina («Half-year financial report» vale anche sopra una tabella del secondo trimestre)
            vicine = [r for r in sopra if not _solo_unita(r) and r != testa_pagina][-2:]
            testa = " ".join(blocco)
            if visive is not None:
                # righe subito sopra fatte di sole parole d'intestazione («Three months ended», «Change (%)»)
                # entrano nelle pile delle colonne: hanno la loro x
                k = i
                while k > 0 and i - k < 2 and righe[k - 1].strip() and _residuo_ammesso(righe[k - 1]) \
                        and not _celle_finali(righe[k - 1])[1] and not _solo_unita(righe[k - 1]):
                    k -= 1
                colonne, colonne_x, testa, confine = _colonne_visive(visive[k:j], fine, " ".join(vicine[-1:]), " ".join(vicine))
                confine_tab = confine
                if confine is not None:
                    affiancate.append((k, confine, i))
                colonne = colonne or []
            else:
                colonne = classifica_colonne(testa, fine, contesto=" ".join(vicine[-1:]), contesto_tre=" ".join(vicine))
            u = _unita(" ".join(blocco))
            unita = u or unita_in_attesa  # mai l'unita' della tabella precedente
            unita_in_attesa, righe_tabella = None, 0
            testa_colonne, etichette, viste = testa, [], []
            # sezione: l'ultimo titolo in maiuscolo sopra la tabella nella pagina («(6) DISCONTINUED OPERATIONS»)
            sezione = next((r.strip() for r in reversed(righe[:i]) if _TITOLO_SEZIONE.fullmatch(r.strip())), "")
            if eredita and not titolo:
                titolo, sezione = eredita["titolo"], eredita["sezione"] or sezione
            tabella = {"titolo": " | ".join(titolo), "sezione": sezione, "etichette": [],
                       "incerte": visive is None and any(_solo_variazioni(r) for r in vicine),
                       "ordine_ignoto": visive is not None and (not colonne_x or any(
                           min(b1, b2) - max(a1, a2) > 0.5 * min(b1 - a1, b2 - a2)
                           for n1, (a1, b1) in enumerate(colonne_x) for (a2, b2) in colonne_x[n1 + 1:]))}
            tabelle.append(tabella)
            i = j
            continue
        if re.fullmatch(r"\s*(?:(?:19|20)\d{2}\s*){2,}\s*", riga):
            colonne = None  # intestazione a soli anni: non riconosciuta, e la precedente non vale piu'
        if colonne is None:
            i += 1
            continue
        if visive is not None and colonne_x:
            mappa, etichetta, n_celle = _mappa_visiva(colonne, colonne_x, visive[i], formato, confine_tab)
            celle = list((mappa or {}).values()) or (["0"] * n_celle)
        else:
            etichetta, celle = _celle_finali(riga)
            mappa = None
        if unita_riga:
            etichetta = " ".join(_RX_UNITA.sub(" ", etichetta).split())
        if not celle:
            if visive is not None and colonne_x:
                # solo le parole della tabella: il testo accanto (altra colonna di pagina o tabella) non entra
                limite = confine_tab if confine_tab is not None else max(b for _, b in colonne_x) + 6
                riga = " ".join(w for w, x0, _ in visive[i] if x0 <= limite)
            if re.search(r"[^\W\d_]{2,}", riga):
                etichette = (etichette + [riga.strip()])[-3:]
            i += 1
            continue
        righe_tabella += 1
        if visive is None:
            mappa = _allinea(colonne, celle, formato, tabella["incerte"])
        candidati = []
        if re.search(r"[^\W\d_]{2,}", etichetta):
            candidati.append(etichetta)
            if etichette:
                candidati.append(etichette[-1] + " " + etichetta)
                if len(etichette) > 1:
                    candidati.append(etichette[-2] + " " + etichette[-1] + " " + etichetta)
        elif etichette and not re.search(r"[^\W\d_]{2,}", _RX_UNITA.sub(" ", riga_tab)):
            # v5: si eredita l'etichetta delle righe sopra SOLO se la riga non ha parole proprie (riga di soli
            # numeri sotto un'etichetta spezzata); una riga con parole proprie rimaste senza etichetta resta senza
            candidati += [" ".join(etichette[-n:]) for n in (1, 2, 3) if len(etichette) >= n]
        # etichetta che continua sotto la riga dei numeri («Net cash flow from operating activities 1,263 ...» /
        # «(before changes in net working capital)»): la riga seguente senza numeri che comincia minuscola o con
        # «(» (non un richiamo di nota) completa l'etichetta; la voce va riconosciuta sull'etichetta INTERA.
        if i + 1 < len(righe) and candidati:
            seguente = righe[i + 1].strip()
            senza_numeri = not (_mappa_visiva(colonne, colonne_x, visive[i + 1], formato, confine_tab)[2] if visive is not None
                                and colonne_x else _celle_finali(seguente)[1])
            if (seguente and senza_numeri and re.match(r"[a-zà-ÿ(]", seguente)
                    and not re.match(r"\(\s*(?:\*+|\d{1,2}|[a-z])\s*\)", seguente)
                    and not _unita(seguente) and not _ha_periodo(seguente) and not seguente.endswith(":")
                    and not _ATTRIBUIBILE.search(_normalizza(seguente))):
                candidati = [f"{c} {seguente}" for c in candidati]
                i += 1
        out.append({"etichette": [_normalizza(c) for c in candidati], "stampate": candidati, "mappa": mappa,
                    "colonne": colonne, "unita": unita_riga or unita, "intestazione": testa_colonne,
                    "contesto": list(viste[-4:]), "prima_etichetta": etichette[-1] if etichette else None,
                    "tabella": tabella, "ordine_ignoto": False})
        # riga di soli rapporti («% of revenues»): celle stampate tutte in % (o vuote)
        rapporto = all("%" in c or not re.search(r"\d", c) for c in (_celle_finali(riga)[1] or celle))
        if not rapporto:
            viste.append(_normalizza(candidati[0]) if candidati else "")
        tabella["etichette"].append(_normalizza(candidati[0]) if candidati else "")
        etichette = []
        i += 1
    for k, confine, i_testa in affiancate:
        # la tabella di destra: solo le parole oltre il confine, dalla sua intestazione in giu'
        sopra = [r.strip() for r in righe[max(0, i_testa - 6):i_testa] if r.strip()]
        destra = [[q for q in linea if q[1] > confine] for linea in visive[k:]]
        out += _righe_pagina("", fine, formato, destra, documento, eredita={
            "testa": testa_pagina, "titolo": [r for r in sopra if not _solo_unita(r)][-3:],
            "sezione": next((r.strip() for r in reversed(righe[:i_testa]) if _TITOLO_SEZIONE.fullmatch(r.strip())), "")})
    for t in tabelle:
        # segmento: dal titolo della tabella, o dalla testata della pagina («Business performance of the
        # segments») quando il titolo non e' di gruppo
        t["segmento"] = bool(_RX_SEGMENTO.search(t["titolo"] + " | " + t["sezione"])) or (
            bool(_RX_SEGMENTO.search(testa_pagina)) and not _RX_GRUPPO_FORTE.search(t["titolo"])) or bool(
            _RX_DISMESSE.search(t["titolo"] + " | " + t["sezione"]))
        per_azione = sum(1 for e in t["etichette"] if _RX_PER_AZIONE.search(e))
        # prospetto dell'utile per azione: almeno due righe e un terzo delle righe (il conto economico chiude
        # con due righe per azione e resta del gruppo)
        t["per_azione"] = per_azione >= 2 and per_azione * 3 >= len(t["etichette"])
        t["gruppo"] = bool(_RX_GRUPPO.search(testa_pagina + " | " + t["titolo"]))
        # prospetto primario in una relazione consolidata senza bilancio separato: del gruppo intero
        t["gruppo"] = t["gruppo"] or bool(documento.get("consolidata") and not documento.get("separato")
                                          and _RX_PROSPETTO.search(t["titolo"] + " | " + t["sezione"]))
        # titolo di sezione col nome dell'emittente («PRYSMIAN'S PERFORMANCE AND RESULTS»), non la testata
        # ripetuta della pagina: la tabella e' della societa' intera (i segmenti sono gia' esclusi)
        # v4: il nome e' la prova d'identita' INTERA normalizzata; la sezione deve essere il nome + sole parole di
        # risultato («PRYSMIAN'S PERFORMANCE AND RESULTS»); «RISULTATI DELLA BANCA», «… LEASING GMBH», una
        # filiale o la capogruppo non sono il gruppo
        nome = documento.get("nome")
        t["gruppo"] = t["gruppo"] or bool(nome and t["sezione"] != testa_pagina
                                          and _sezione_del_gruppo(t["sezione"], nome))
        # v4: tabella delle sole attivita' continuative (titolo o sezione)
        t["continuative"] = bool(_RX_CONTINUATIVE.search(t["titolo"] + " | " + t["sezione"]))
        t["complessivo"] = bool(_RX_COMPLESSIVO.search(" | ".join([t["titolo"], t["sezione"]] + t["etichette"])))
        t["minoranze"] = any(_RX_NCI.search(e) for e in t["etichette"])
    return out


_PAROLE_RISULTATO = {"performance", "and", "results", "result", "s", "key", "figures", "highlights", "financial",
                     "risultati", "andamento", "e", "dati", "principali", "del", "ergebnisse", "und", "geschäftsverlauf",
                     "kennzahlen", "résultats", "et", "chiffres", "clés", "resultados", "y", "cifras", "principales"}
_RX_CONTINUATIVE = re.compile(r"continuing\s+operations|fortgef[uü]hrte[nr]?\s+(?:aktivit[aä]ten|gesch[aä]ftsbereiche)"
                              r"|attivit[aà]\s+(?:in\s+funzionamento|continuative)|activit[eé]s\s+poursuivies"
                              r"|actividades\s+continuadas|voortgezette\s+bedrijfsactiviteiten", re.I)


def _norm_nome(testo):
    return " ".join(re.findall(r"[^\W_]+", testo.lower().replace("’", "'").replace("'s", " ")))


def _sezione_del_gruppo(sezione, nome):
    """True se la sezione e' il nome INTERO dell'emittente + sole parole di risultato (niente di proprio)."""
    n, s = _norm_nome(nome), _norm_nome(sezione)
    if not n or f" {n} " not in f" {s} ":
        return False
    resto = f" {s} ".replace(f" {n} ", " ", 1).split()
    return bool(resto) and all(w in _PAROLE_RISULTATO for w in resto)


def _solo_unita(riga):
    """True per la riga fatta solo del marcatore di unita' («(€m)»)."""
    return bool(_unita(riga)) and not re.search(r"[^\W\d_]{3,}", _RX_UNITA.sub(" ", riga))


def _riconosci(riga):
    """(voce, tipo, misura, extra, etichetta stampata) della riga, o None."""
    for norm, stampata in zip(riga["etichette"], [s for s in riga["stampate"]]):
        trovata = _voce_di(norm)
        if trovata:
            return (*trovata, stampata)
    # «Net profit / Attributable to: / Owners of the parent 569 ...»: quota del gruppo SOLO se la riga di
    # importi immediatamente sopra e' l'utile netto (le righe di soli rapporti non contano) e la tabella non e'
    # un conto economico complessivo.
    norm = riga["etichette"][0] if riga["etichette"] else ""
    prima = _normalizza(riga["prima_etichetta"] or "")
    precedente = riga["contesto"][-1] if riga["contesto"] else ""
    if (_GRUPPO_DOPO_ATTRIBUIBILE.fullmatch(norm) and not _RX_NCI.search(norm)
            and not riga["tabella"]["complessivo"] and _ATTRIBUIBILE.search(prima)
            and (_voce_di(precedente) or ("",))[0] == "utile_netto_totale"):
        voce, tipo, misura, rx, extra = next(x for x in _RX_VOCI if x[0] == "utile_netto")
        return voce, tipo, misura, extra, f"{riga['prima_etichetta'] or 'attributable to'} {riga['stampate'][0]}"
    return None


def _leggi(testo_pagine, fine, formato, nome=None):
    """{voce: [osservazioni]} dalle tabelle del documento; e i motivi delle righe riconosciute ma scartate.
    `nome` = parola principale del nome dell'emittente (dalla prova d'identita'), per i titoli di sezione."""
    tutto = "\n".join(p[1] for p in testo_pagine)
    documento = {"consolidata": bool(re.search(r"consolidat|konzern|consolidé|geconsolideerd|consolidad", tutto, re.I)),
                 "separato": bool(_RX_SEPARATO.search(tutto)), "nome": nome,
                 "dismesse": bool(_RX_DISMESSE.search(tutto))}
    trovate, scartate = {}, {}
    for pagina in testo_pagine:
        numero, testo = pagina[0], pagina[1]
        visive = pagina[2] if len(pagina) > 2 else None
        for riga in _righe_pagina(testo, fine, formato, visive, documento):
            r = _riconosci(riga)
            if not r:
                continue
            voce, tipo, misura, extra, stampata = r
            colonne = riga["colonne"]
            if riga["tabella"]["segmento"] or riga["tabella"]["per_azione"]:
                genere = "di segmento/settore" if riga["tabella"]["segmento"] else "dell'utile per azione"
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: tabella {genere} ({riga['tabella']['titolo'][:60]!r}), non del gruppo intero")
                continue
            continuative = riga["tabella"]["continuative"] or bool(_RX_CONTINUATIVE.search(stampata))
            if continuative and documento["dismesse"]:
                # v4: «(continuing operations)» in un documento con attivita' cessate: non e' il gruppo intero
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: solo attivita' continuative in un documento con attivita' cessate: perimetro "
                    "diverso dal gruppo intero")
                continue
            if riga["tabella"]["ordine_ignoto"] or riga["ordine_ignoto"]:
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: ordine visivo delle colonne o delle celle non verificabile sulle coordinate")
                continue
            if riga["tabella"]["incerte"]:
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: colonne di variazione su una riga separata dall'intestazione: allineamento non provato")
                continue
            if tipo == "istante":
                tipi = [{"corrente_istante": "corrente", "confronto_istante": "confronto"}.get(t, t) for t, _ in colonne]
            else:
                tipi = ["altro" if t in ("corrente_istante", "confronto_istante") else t for t, _ in colonne]
            if tipi.count("corrente") != 1 or tipi.count("confronto") > 1:
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: intestazione senza una sola colonna del periodo corrente"
                    + (" di sei mesi (data nuda o trimestre)" if tipo == "durata" else "")
                    + f" ({riga['intestazione'][:80]!r})")
                continue
            if riga["mappa"] is None:
                scartate.setdefault(voce, []).append(f"p. {numero}: celle non allineate alle colonne («{stampata[:60]}»)")
                continue
            if riga["unita"] is None or riga["unita"][1] is None:
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: unita' e valuta " + ("non riconosciute" if riga["unita"] else "non dichiarate")
                    + " per questa tabella")
                continue
            ic = tipi.index("corrente")
            if ic not in riga["mappa"]:
                scartate.setdefault(voce, []).append(f"p. {numero}: cella del periodo corrente assente")
                continue
            corrente = _valore(riga["mappa"][ic], formato)
            if corrente is None:
                scartate.setdefault(voce, []).append(
                    f"p. {numero}: cella del periodo corrente vuota o non numerica ({riga['mappa'][ic]!r})")
                continue
            confronto, cella_conf = None, None
            if "confronto" in tipi and tipi.index("confronto") in riga["mappa"]:
                cella_conf = riga["mappa"][tipi.index("confronto")]
                confronto = _valore(cella_conf, formato)
            scala, fattore, valuta = riga["unita"]
            if voce == "utile_netto_totale":
                extra = {**extra, "perimetro": "totale, incluse le minoranze" if riga["tabella"]["minoranze"] else
                         "non determinato: la tabella non stampa la quota delle minoranze"}
            if continuative:
                extra = {**extra, "perimetro": "solo attivita' continuative" + (
                    f"; {extra['perimetro']}" if extra.get("perimetro") else "")}
            trovate.setdefault(voce, []).append({
                "tipo_periodo": tipo, "misura": misura, **extra, "etichetta": " ".join(stampata.split())[:120],
                "pagina": numero, "corrente": corrente * fattore, "confronto": None if confronto is None
                else confronto * fattore, "stampato": {"corrente": riga["mappa"][ic], "confronto": cella_conf},
                "unita": scala, "valuta": valuta, "intestazione": " ".join(riga["intestazione"].split())[:160],
                "di_gruppo": riga["tabella"]["gruppo"]})
    return trovate, scartate


def _sceglie(osservazioni):
    """(osservazione, motivo): un valore esce solo con almeno una tabella di gruppo; valori discordi: decidono le
    tabelle di gruppo se concordi, altrimenti ambigua."""
    per_def = {}
    for o in osservazioni:  # free cash flow: la definizione «come stampata» prima di levered e unlevered
        per_def.setdefault(o.get("definizione"), []).append(o)
    gruppo = per_def[min(per_def, key=lambda d: _PRIORITA_DEFINIZIONE.index(d))]
    if not any(o.get("di_gruppo") for o in gruppo):
        elenco = "; ".join(f"p. {o['pagina']} {o['stampato']['corrente']}" for o in gruppo[:4])
        return None, ("perimetro non provato: nessuna tabella di gruppo/consolidata riporta la voce (tabelle senza "
                      f"titolo di gruppo: {elenco})")
    nota_scelta = None
    if len({(o["corrente"], o["valuta"]) for o in gruppo}) > 1:
        # Valori discordi: decidono le sole tabelle del gruppo intero (titolo o testata «consolidated»,
        # «highlights», «gruppo»), se ci sono e sono concordi; le altre (aree, business) si dichiarano.
        di_gruppo = [o for o in gruppo if o.get("di_gruppo")]
        altre = [o for o in gruppo if not o.get("di_gruppo")]
        if di_gruppo and len({(o["corrente"], o["valuta"]) for o in di_gruppo}) == 1 and altre:
            scelto = (di_gruppo[0]["corrente"], di_gruppo[0]["valuta"])
            diversi = [o for o in altre if (o["corrente"], o["valuta"]) != scelto]
            nota_scelta = ("valori diversi in tabelle senza titolo di gruppo/consolidato, ignorati: " + "; ".join(
                f"p. {o['pagina']} {o['stampato']['corrente']}" for o in diversi[:4])
                + (f" (e altri {len(diversi) - 4})" if len(diversi) > 4 else ""))
            gruppo = di_gruppo + [o for o in altre if (o["corrente"], o["valuta"]) == scelto]
        else:
            elenco = "; ".join(f"p. {o['pagina']} «{o['etichetta'][:40]}» {o['stampato']['corrente']} {o['unita']}"
                               for o in gruppo[:4])
            return None, f"valori diversi del periodo corrente in tabelle diverse ({elenco}): ambigua"
    scelta = dict(gruppo[0])
    if nota_scelta:
        scelta["nota_scelta"] = nota_scelta
    scelta["pagine"] = sorted({o["pagina"] for o in gruppo})
    confronti = {o["confronto"] for o in gruppo if o["confronto"] is not None}
    if len(confronti) > 1:
        scelta["confronto"] = None
        scelta["nota_confronto"] = "valori diversi del periodo di confronto fra le tabelle: confronto n.d."
    elif confronti:
        scelta["confronto"] = next(iter(confronti))
    return scelta, None


def _segno_non_determinabile(voce, scelta, osservazioni):
    """Motivo se il segno di debito/cassa netti non si puo' affermare: valore stampato negativo o segno
    opposto dello stesso importo in un'altra tabella. None se positivo e coerente."""
    if voce not in _SEGNO_POSITIVO:
        return None
    if scelta["corrente"] < 0 or (scelta["confronto"] is not None and scelta["confronto"] < 0):
        return "convenzione di segno non determinabile: importo stampato negativo"
    if any(o["corrente"] == -scelta["corrente"] for o in osservazioni):
        pagine = sorted({o["pagina"] for o in osservazioni if o["corrente"] == -scelta["corrente"]})
        return f"convenzione di segno non determinabile: stesso importo col segno opposto a p. {pagine}"
    return None


def _pagina_candidata(testo):
    """Pagina che puo' contenere una tabella di periodi: un'intestazione di periodo e almeno 6 numeri."""
    return _ha_periodo(testo) and len(re.findall(r"\d[\d.,]*\d|\d", testo)) >= 6


def pagine_visive(raw, testi_stream=None):
    """[righe] per pagina in ORDINE VISIVO dalle coordinate delle parole (pdfplumber, gia' nel repo).

    righe = [[(parola, x0, x1)]] dall'alto in basso e da sinistra a destra. Con `testi_stream` (testo pypdf
    per pagina) si leggono le coordinate solo delle pagine candidate; le altre hanno [] (nessuna riga
    verificabile: le loro righe di tabella restano n.d.)."""
    import pdfplumber
    out = []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        for numero, pagina in enumerate(pdf.pages, 1):
            if testi_stream is not None and not _pagina_candidata(testi_stream[numero - 1] if numero <= len(testi_stream)
                                                                  else ""):
                out.append([])
                pagina.close()
                continue
            parole = [p for p in pagina.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False,
                                                       use_text_flow=False) if p.get("upright", True)]
            pagina.close()
            righe = []
            for p in sorted(parole, key=lambda p: (p["top"], p["x0"])):
                altezza = max(p["bottom"] - p["top"], 1.0)
                w = _SPAZIO_MIGLIAIA.sub("", p["text"])
                if righe and abs(righe[-1]["top"] - p["top"]) <= max(2.0, 0.45 * altezza):
                    righe[-1]["parole"].append((w, p["x0"], p["x1"]))
                else:
                    righe.append({"top": p["top"], "parole": [(w, p["x0"], p["x1"])]})
            righe = [sorted(r["parole"], key=lambda q: q[1]) for r in righe]
            out.append(righe)
    return out


def estrai_numeri_semestrale(path, *, periodo_fine, periodo_inizio=None, regola_emittente=None, url=None,
                             sha256_atteso=None, fino_al=None, filed_date=None, testo_pagine=None):
    """Voci chiave del PDF della semestrale: {"stato", "voci": {voce: osservazione}, "scarti": {voce: motivo}, ...}.

    stato: ok | vuoto | rifiutato | illeggibile. Le osservazioni hanno valori ASSOLUTI (unita' applicata),
    il valore stampato, l'etichetta, la pagina, l'unita', la valuta e la misura (riportato/rettificato).
    `testo_pagine` (prove): [(pagina, testo)] gia' in ordine visivo, senza coordinate. Dal PDF: testo dello
    stream (pypdf, righe e intestazioni) + righe visive (pdfplumber) per verificare e riordinare le celle."""
    fine = date.fromisoformat(str(periodo_fine)[:10])
    base = {"periodo": {"inizio": periodo_inizio, "fine": fine.isoformat()}, "voci": {}, "scarti": {}}
    avvisi = []
    try:
        if fino_al and fine.isoformat() > str(fino_al)[:10]:
            raise Rifiuto(f"periodo chiuso il {fine.isoformat()} oltre il cutoff {str(fino_al)[:10]}: nessun look-ahead")
        if fino_al and filed_date:
            from bellomberg.market_data.sec_xbrl import depositato_entro
            if not depositato_entro(str(filed_date)[:10], str(fino_al)[:10]):
                raise Rifiuto(f"documento pubblicato il {str(filed_date)[:10]}, dopo il cutoff {str(fino_al)[:10]}")
        elif fino_al:
            avvisi.append("data di pubblicazione ignota: il look-ahead e' controllato solo sulla fine del periodo")
        if testo_pagine is None:
            raw = Path(path).read_bytes()
            if sha256_atteso and hashlib.sha256(raw).hexdigest() != sha256_atteso:
                raise Rifiuto("byte del PDF diversi da quelli verificati (sha256): documento rifiutato")
            if raw[:5] != b"%PDF-":
                return {**base, "stato": "illeggibile", "motivo": "il documento non e' un PDF"}
            from bellomberg.market_data.lettore_trimestrali import estrai_testo
            estrazione = estrai_testo(str(path), contenuto=raw)
            if estrazione.get("stato") != "ok":
                return {**base, "stato": "illeggibile", "motivo": estrazione.get("motivo") or "PDF illeggibile"}
            testo = estrazione["testo"]  # identita' sul testo della verifica (stesso lettore)
            stream = [r["testo"] for r in estrazione.get("riferimenti") or []]
            testo_pagine = [(k, t, v) for k, (t, v) in enumerate(zip(stream, pagine_visive(raw, stream)), 1)]
        else:
            testo = "\n".join(p[1] for p in testo_pagine)
        if regola_emittente is None:
            raise Rifiuto("regola d'identita' dell'emittente assente: il documento non si lega all'emittente")
        from bellomberg.market_data import regex_sandbox
        from bellomberg.market_data.filing_verifica import _cerca_prova
        try:
            with regex_sandbox.budget(regex_sandbox.TEMPO_DOCUMENTO_S):
                prova = _cerca_prova(testo[:MAX_CARATTERI_IDENTITA], regola_emittente, "emittente", url, None)
        except ValueError as exc:
            raise Rifiuto(f"documento di un altro emittente o non riconosciuto: identita' dell'emittente non "
                          f"provata nel PDF ({exc})") from exc
    except Rifiuto as exc:
        return {**base, "stato": "rifiutato", "motivo": str(exc)}
    formato = formato_numerico(_SPAZIO_MIGLIAIA.sub("", "\n".join(p[1] for p in testo_pagine)))
    if formato is None:
        return {**base, "stato": "vuoto", "prova_emittente": prova["testo"][:120], "avvisi": avvisi,
                "motivo": "formato numerico del documento non determinabile (1,234.5 o 1.234,5): nessun numero letto"}
    nome = " ".join(prova["testo"].split()) or None  # v4: la prova d'identita' intera, non la prima parola
    trovate, scartate = _leggi(testo_pagine, fine, formato, nome)
    voci, scarti = {}, {}
    for voce in ORDINE + ("utile_netto_totale",):
        if voce == "utile_netto_totale" and "utile_netto" in voci:
            continue
        if voce in trovate:
            scelta, motivo = _sceglie(trovate[voce])
            motivo = motivo or _segno_non_determinabile(voce, scelta, trovate[voce])
            if motivo:
                scarti[voce] = motivo
            else:
                voci[voce] = scelta
        elif voce in scartate:
            scarti[voce] = "riga trovata ma scartata: " + "; ".join(dict.fromkeys(scartate[voce]))[:300]
    return {**base, "stato": "ok" if voci else "vuoto", "voci": voci, "scarti": scarti, "formato_numerico": formato,
            "prova_emittente": prova["testo"][:120], "pagine": len(testo_pagine), "avvisi": avvisi,
            **({} if voci else {"motivo": "nessuna voce chiave trovata nelle tabelle del PDF"})}


def _nome(url):
    from urllib.parse import unquote, urlsplit
    try:
        parti = urlsplit(str(url or ""))
        return f"{parti.hostname or ''}/{unquote(parti.path.rsplit('/', 1)[-1])}".strip("/") or "PDF"
    except ValueError:
        return "PDF"


def numeri_semestrale(coppia, *, profilo, fino_al=None):
    """Numeri della coppia di semestrali da PDF, nella forma di `filing_numeri.variazioni`.

    `dopo` = semestrale corrente: corrente e (se c'e') colonna di confronto dello stesso documento (il
    comparativo rideterminato vince, come nello storico ESEF); senza colonna di confronto vale il corrente
    della semestrale `prima`, dichiarato per voce. Ogni voce chiave non trovata e' uno scarto col motivo."""
    fonte_base = {"voci": [], "scarti": [], "origine": ORIGINE}
    lati = {lato: (coppia or {}).get(lato) or {} for lato in ("prima", "dopo")}
    esterni = (profilo or {}).get("documenti_esterni") if isinstance(profilo, dict) else None
    fonte = f"PDF relazione semestrale {_nome(lati['dopo'].get('url'))} (sito dell'emittente), tabelle lette in locale"
    for lato, doc in lati.items():
        meta = doc.get("metadati") or {}
        if meta.get("tipo") != "semestrale":
            return {**fonte_base, "fonte": fonte, "stato": "non_disponibile",
                    "motivo": f"documento {lato} non e' una relazione semestrale verificata"}
        if isinstance(esterni, dict) and doc.get("url") in esterni:
            return {**fonte_base, "fonte": fonte, "stato": "non_disponibile",
                    "motivo": f"documento {lato} da fonte esterna ({esterni[doc.get('url')]}): contesto, mai numeri"}
        if not (doc.get("prove_verifica") or {}).get("emittente"):
            return {**fonte_base, "fonte": fonte, "stato": "non_disponibile",
                    "motivo": f"documento {lato} senza prova d'identita' dell'emittente nella verifica"}
    regola = ((profilo or {}).get("verifica") or {}).get("emittente")

    def leggi(lato):
        doc, meta = lati[lato], lati[lato]["metadati"]
        return estrai_numeri_semestrale(doc.get("path"), periodo_fine=meta["periodo_fine"],
                                        periodo_inizio=meta.get("periodo_inizio"), regola_emittente=regola,
                                        url=doc.get("url"), sha256_atteso=doc.get("sha256"), fino_al=fino_al,
                                        filed_date=doc.get("filed_date"))

    dopo = leggi("dopo")
    if dopo["stato"] != "ok":
        return {**fonte_base, "fonte": fonte, "stato": "non_disponibile" if dopo["stato"] != "rifiutato" else "rifiutato",
                "motivo": "semestrale corrente: " + dopo.get("motivo", dopo["stato"]),
                "scarti": [{"voce": v, "motivo": m} for v, m in dopo.get("scarti", {}).items()]}
    prima = leggi("prima")
    fonte_semestrale_prima = (f"PDF relazione semestrale {_nome(lati['prima'].get('url'))} (sito dell'emittente), "
                              "periodo corrente di quella relazione")
    voci, scarti, rideterminazioni = [], [], []
    for voce, o in dopo["voci"].items():
        a, fonte_prima = o["confronto"], fonte
        p = (prima.get("voci") or {}).get(voce) if prima.get("stato") == "ok" else None
        if p and (p.get("definizione") != o.get("definizione") or p["valuta"] != o["valuta"]):
            p = None  # definizione o valuta diverse: non e' la stessa voce
        if a is None and p:
            a, fonte_prima = p["corrente"], fonte_semestrale_prima
        elif a is not None and p and p["corrente"] != a:
            # comparativo rideterminato nella semestrale corrente: vale il documento piu' recente, dichiarato
            rideterminazioni.append({"voce": voce, "fine": prima["periodo"]["fine"], "valore_precedente": float(p["corrente"]),
                                     "fonte_precedente": fonte_semestrale_prima, "valore_usato": float(a),
                                     "fonte_usata": fonte})
        riga = {"voce": voce, "prima": float(a) if a is not None else None, "dopo": float(o["corrente"]),
                "delta_pct": (round(float((o["corrente"] - a) / abs(a) * 100), 1) if a else None),
                "valuta": o["valuta"], "unita": o["unita"], "misura": o["misura"], "tipo_periodo": o["tipo_periodo"],
                "tag": f"PDF p. {o['pagina']}: «{o['etichetta']}»", "pagine": o["pagine"],
                "valore_stampato": o["stampato"], "fonte_dopo": fonte, "fonte_prima": fonte_prima if a is not None else None,
                **{k: o[k] for k in ("perimetro", "segno", "definizione", "nota_confronto", "nota_scelta") if o.get(k)}}
        voci.append(riga)
        if a is None:
            precedente = ("voce non trovata nella semestrale precedente" if prima.get("stato") == "ok" else
                          f"semestrale precedente senza numeri ({str(prima.get('motivo') or prima.get('stato'))[:120]})")
            scarti.append({"voce": voce, "motivo": o.get("nota_confronto") or (
                "periodo di confronto n.d.: nessuna colonna dello stesso periodo dell'anno prima nel documento e "
                + precedente)})
        elif a == 0:
            scarti.append({"voce": voce, "motivo": "base zero nel periodo di confronto: variazione non calcolabile"})
    for richiesta, gruppo in _RICHIESTE.items():
        if not any(v in dopo["voci"] for v in gruppo):
            motivi = [f"{v}: {dopo['scarti'][v]}" for v in gruppo if v in dopo["scarti"]]
            extra = (" (presente solo l'utile netto totale)"
                     if richiesta == "utile_netto" and "utile_netto_totale" in dopo["voci"] else "")
            scarti.append({"voce": richiesta, "motivo": (("n.d.: " + "; ".join(motivi)) if motivi else
                                                         "n.d.: voce non trovata nelle tabelle della semestrale") + extra})
    valute = {v["valuta"] for v in voci}
    out = {"stato": "ok", "valuta": next(iter(valute)) if len(valute) == 1 else None, "voci": voci, "scarti": scarti,
           "fonte": fonte, "origine": ORIGINE, "periodo": dopo["periodo"],
           "unita": sorted({v["unita"] for v in voci}), "formato_numerico": dopo["formato_numerico"],
           "nota_fonte": ("numeri letti dalle tabelle del PDF della semestrale (nessun XBRL): colonna del periodo "
                          "corrente e colonna dello stesso periodo dell'anno prima riconosciute dall'intestazione")}
    avvisi = list(dict.fromkeys(dopo.get("avvisi") or []))
    if len(valute) > 1:
        avvisi.append(f"voci in valute diverse: {', '.join(sorted(valute))} (valuta per voce)")
    if avvisi:
        out["avvisi"] = avvisi
    if rideterminazioni:
        out["rideterminazioni"] = rideterminazioni
    return out
