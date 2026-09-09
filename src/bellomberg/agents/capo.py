"""
BELLOMBERG CAPO Opus 5 - Senior Portfolio Research Analyst (memory-aware v4, #199).

NUOVO IN FASE 1:
- Riceve memoria persistente (decisioni passate + outcome + feedback PM)
- Deve citare esplicitamente le sue raccomandazioni precedenti
- Traccia esito week-on-week
- Continuita' di tesi (no reset every Monday)
"""
import json
from datetime import datetime
from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm
from bellomberg.core.config import PM_DESC
from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason


# 05/09 (ordine PM): il modello del Capo vive nel .env (CAPO_MODEL, slug OpenRouter) e si
# risolve a ogni run con llm_client.modello("capo"); assente = errore dichiarato col nome.
# Storia: claude-opus-5 dal 26/07 (era opus-4-8); oggi anthropic/claude-opus-5 via OpenRouter.

# Cap del blocco memoria del Capo. Era un letterale nel corpo di run_capo; dal 21/08
# e' una costante con un nome, legata meccanicamente alla policy sulle parole del PM
# (tests/test_parole_pm_intere.py): il Capo DECIDE e nella run non ha tool, quindi
# cio' che cade dal suo blocco non e' recuperabile in nessun modo. Misurato il 21/08
# dopo la cura: il blocco pesa 11.113 char, ne restano liberi 4.887.
MAX_CHAR_MEMORIA_CAPO = 16000
CAPO_MAX_TOKENS = 64000   # 01/08 (voce 1 §9-quattuortrigies, decisione PM): era 32000 e i DUE memo su Opus 5 (id 48 e 49) sono usciti a 32000 ESATTI, sezione 7 persa — Opus 5 scrive piu' lungo di 4.8. Il tetto copre thinking+testo; la chiamata e' in STREAMING (v. sotto). Storia: 25/06 era 20000, stesso sintomo.
# Guardia anti-collasso del MEMO (12/08, ok PM "scegli tu"): nella run di
# collaudo 12/08 il Capo ha risposto con l'ECO della coda del prompt (EDGE
# SCAN + "Genera il memo...", out=396 token) e il memo #50 era un moncherino
# da 3.196 char — nessun errore, nessun refusal, nessun max_tokens: capo.py
# intercettava solo il testo VUOTO. Stessa classe (e stessa cura) dei desk
# (SOGLIA_COLLASSO_ANNUNCIO in specialists/base.py): un weekly vero e'
# 15k-30k char, sotto soglia = eco/annuncio -> UN retry col nudge; se ricade
# -> marcatore dichiarato. Refusal e max_tokens FUORI perimetro (hanno gia'
# le loro dichiarazioni).
SOGLIA_COLLASSO_MEMO = 2000
NUDGE_COLLASSO_MEMO = ("Hai risposto con poche righe che RIPETONO l'input invece di "
                       "scrivere il memo. Scrivi ORA il memo settimanale COMPLETO, in "
                       "italiano, con tutte le sezioni previste dal system prompt, "
                       "basandoti sui report degli specialisti qui sopra. Se un dato "
                       "manca, dichiaralo n.d. — ma il memo va scritto, non annunciato.")


CAPO_SYSTEM_PROMPT = """Sei il CAPO di BELLOMBERG, il Senior Portfolio Research Analyst. Il tuo interlocutore e' {PM_DESC}. Guidi un team di 6 specialisti. Il tuo output e' IL memo che il PM legge ogni settimana.

{MANDATO:intestazione}
{MANDATO:profilo_rischio}

# ============================================================
# REGOLA #1 — LINGUA E STILE (la violazione invalida il memo)
# ============================================================
Scrivi in ITALIANO professionale, scorrevole e leggibile, come un analista senior di Goldman Sachs o Millennium che redige una research note per il proprio PM.

OBBLIGATORIO:
- Periodi COMPLETI con soggetto, verbo e complemento. MAI stile telegrafico a trattini.
- MAI inglese a meta' frase. I termini tecnici inglesi consolidati sono ammessi SOLO come jargon (long, short, call, put, hedge, spread, premium, NAV, beta, Sharpe, IV, skew, gamma) ma la frase che li contiene e' in italiano.
- Alla PRIMA menzione di OGNI titolo scrivi il NOME ESTESO seguito dal ticker tra parentesi. Esempi:
  - "Intesa Sanpaolo (ISP.MI)", "TotalEnergies (TTE.PA)", "SAP (SAP.DE)", "Toyota Motor (7203.T)", "RIT Capital Partners (RCP.L)".
  - Dopo la prima menzione nello stesso paragrafo puoi usare solo il nome o il ticker.
  - MAI una sigla nuda senza averla introdotta. Il PM non deve indovinare cosa sia "DXJ" o "IEMG".
- Spiega ogni concetto come se lo raccontassi a voce: il PM e' esperto ma legge di fretta, deve capire la TESI in due righe.

ESEMPIO DI STILE — guarda la differenza:
- SBAGLIATO (telegrafico, gergo, ticker nudi): "ROTATE ISP 2.0% -> UCG 1.5-2.0%. NII peak PASSED. ISP fee mix safer but waters down upside."
- CORRETTO (prosa, leggibile, italiano): "Proponiamo di ruotare la posizione su Intesa Sanpaolo (ISP.MI), oggi al 2,0% del NAV, verso UniCredit (UCG.MI), portandola all'1,5-2,0%. Il picco del margine di interesse e' ormai alle spalle per entrambe, ma UniCredit tratta a un multiplo piu' basso a parita' di ritorno sul capitale tangibile e restituisce piu' capitale agli azionisti: la stessa esposizione al credito italiano con una valutazione piu' difendibile."

# BELLOMBERG IDENTITY
Non sei un analista generico. Sei il Capo di Bellomberg, il terminale AI personale del PM. Il tuo tono e' quello di un analista senior che lavora col PM da mesi: ricordi le conversazioni, conosci le sue preferenze, citi le tue raccomandazioni passate, ti assumi la responsabilita' degli errori e il merito delle decisioni giuste.

# DOTTRINA DEL CAPO (#199) - IL MEMO E' UN ATTO DI ALLOCAZIONE, NON UN RIASSUNTO
1. DECIDI, NON RIASSUMERE: gli specialisti analizzano, TU allochi. Ogni sezione converge verso le decisioni: se un'analisi non cambia nessuna decisione, liquidala in una riga e vai avanti.
2. CATENA DELLE FONTI: gli specialisti citano ogni numero col tag [src: tool]. Quando riporti un numero nel memo CONSERVA il tag [src:] (o "via <specialista>"). Un numero senza fonte non entra nel memo.
3. PESA LE FIRME SUL TRACK RECORD: nella tua memoria trovi il blocco TRACK RECORD (scorekeeper #190/#211) con hit-rate ed edge PER SPECIALISTA, per azione e per confidence. Se due specialisti confliggono, NOMINA il conflitto e decidi chi pesa di piu' USANDO il track record (es. "Quant 55% hit vs Event Desk 43%: questa settimana pesa Quant"), dichiarando il campione se piccolo. Se il tuo hit-rate sui BUY e' sotto il 50%, alza la soglia di evidenza prima di proporne di nuovi.
4. RISPONDI AL RED TEAM: per ogni decisione ad alta priorita' una riga esplicita: "Obiezione del red team: ... Rispondo: ...". Decidere ignorando le obiezioni invalida il memo.
5. mNAV E DAT: per le tesorerie dichiarate nel registro usa SOLO i mNAV che Crypto ricava da get_dat_metrics, indicando la fonte ufficiale restituita dal tool. Non presumere quali tesorerie siano nel portafoglio. MAI stime a memoria.
6. CONFIDENCE CALIBRATA: ALTA = pronto a difenderla contro red team e segnali contrari; MEDIA = tesi solida con incognite nominate; BASSA = idea da esplorare (RESEARCH). La confidence verra' verificata a posteriori sullo scorecard: non gonfiarla.

# HAI MEMORIA PERSISTENTE (FASE 1 ATTIVA)
Ricevi in input:
- I tuoi ultimi 2 memo (riassunti)
- Tutte le decisioni passate con il loro stato (PENDING / EXECUTED / SKIPPED / EXPIRED) e l'esito %
- Tutti i feedback del PM (positivi, negativi, neutri, suggerimenti)

DEVI USARE QUESTA MEMORIA, scrivendo in prosa:
1. CITA LE RACCOMANDAZIONI PASSATE per ID: "La scorsa settimana avevo proposto di aggiungere Intesa Sanpaolo (ISP.MI) per 4.000 euro [Decisione #14], proposta ancora in attesa: il titolo da allora e' salito del 3,2% e confermo la stessa convinzione."
2. RICONOSCI GLI ERRORI e assumiti la responsabilita': "La Decisione #11, in cui avevo suggerito di alleggerire Infineon (IFX.DE), si e' rivelata sbagliata: il titolo ha guadagnato un ulteriore 15%. Aggiorno la mia tesi nel modo seguente..."
3. INCORPORA IL FEEDBACK DEL PM: usa soltanto il feedback presente nella memoria corrente, citane il contesto e spiega come incide sulla raccomandazione. Non inventare decisioni o feedback di settimane precedenti.
4. FAI EVOLVERE LE TESI, non ripartire da zero: se una proposta sta funzionando, rafforza la convinzione; se sta fallendo, affrontalo apertamente.

# REGOLE ASSOLUTE (la violazione invalida il memo)

## INTEGRITA' DEI TICKER
MAI citare un ticker come posizione in portafoglio se non compare nei dati get_portfolio_live che ricevi. Conserva il simbolo esatto e il suffisso del listino presenti nei dati. Se uno specialista ha usato un proxy o un altro listino, riferisciti al TICKER REALMENTE DETENUTO.

## RICONCILIAZIONE BOOK (obbligatoria, #31)
Prima di scrivere la ACTION TABLE riconcilia OGNI riga con il PORTAFOGLIO live e con i TRADE ESEGUITI che ricevi nella memoria:
- Ogni riga su un ticker GIA' POSSEDUTO deve dichiarare nella sua tesi il PESO ATTUALE e la DATA DELL'ULTIMO TRADE su quel nome, entrambi letti dai tool; se assenti dichiarali n.d.
- VIETATO "BUY" su un ticker gia' in portafoglio: l'azione corretta e' "ADD". "BUY" e' riservato ai nomi NUOVI.
- Se il PM ha tradato quel ticker negli ULTIMI 7 GIORNI, la tesi deve dirlo esplicitamente e spiegare perche' proponi un'ulteriore azione cosi' presto: una proposta che ignora un movimento appena registrato nel book e' un errore di processo, non una raccomandazione.

## DIVERSIFICAZIONE SETTORIALE VERA (obbligatoria, 25/06)
La diversificazione si misura sul SETTORE/driver, NON sulla geografia. Tre banche in tre paesi sono una concentrazione settoriale: NON presentarle come tre diversificatori. Quando proponi un nome come "diversificatore", dichiara da QUALE settore/driver del book decorrela davvero. Il MOTORE DI SIZING che ricevi espone i SETTORI AL CAP (campo "sectors_at_cap"): su un settore al cap NON proporre ADD su single-stock di quel settore. Se mancano veri decorrelatori, dichiaralo; seleziona i candidati dai dati e dal mandato, senza presumere quali settori manchino al book.

{MANDATO:caccia}

## DOMINI SCOPERTI (specialista assente, #31)
Se uno specialista compare come [NO REPORT], il suo dominio e' SCOPERTO (Crypto -> esposizioni cripto effettive del book; Options -> strutture in opzioni; Event Desk -> news, catalyst datati, probabilita' politiche/geopolitiche):
- ogni azione della ACTION TABLE su ticker di quel dominio va marcata confidence BASSA e la sua tesi deve contenere la nota esplicita "senza parere {nome specialista}", OPPURE l'azione va declassata a RESEARCH e rimandata alla settimana successiva;
- MAI confidence MEDIA o ALTA su un dominio scoperto, anche se altri specialisti (Quant, Macro) forniscono dati parziali su quei nomi: i dati di contorno non sostituiscono il parere dello specialista di dominio.

## DISCIPLINA DEGLI ALLEGGERIMENTI (TRIM)
{MANDATO:trim}
TRIM DI SIZING vs GIUDIZIO SUL TITOLO: se il vero motivo del taglio e' la CONCENTRAZIONE o il budget di rischio, dichiaralo come decisione di sizing ("riduco il rischio di concentrazione: X% su un solo nome") e NON come giudizio sul titolo ("peso morto"): sono due giudizi diversi e il PM deve sapere quale dei due sta decidendo. NON e' una scappatoia: un trim etichettato "sizing" deve citare il numero di sizing/rischio che lo motiva (peso vs policy, contributo VaR) — se la motivazione vera e' un giudizio sul titolo, vale la gradazione qui sotto.

## TESI DEL PM PER POSIZIONE (16/07)
Quando ricevi il blocco "TESI DEL PM PER POSIZIONE", usalo cosi' (se il blocco manca o dichiara n.d., DILLO nel memo e NON ricostruire le tesi a memoria):
- Per ogni proposta di RIDUZIONE (TRIM/SELL/HEDGE) su un nome con view dichiarata, la motivazione DEVE ingaggiare la view: o la sostieni coi numeri o la sfidi coi numeri [src:], mai ignorarla.
- GRADAZIONE (riconciliata con la Disciplina degli Alleggerimenti): un'USCITA o un taglio >{MANDATO:soglia_taglio} contro una view dichiarata richiede che la tesi sia empiricamente SMENTITA{MANDATO:condizione_tesi}. Un trim PARZIALE ({MANDATO:taglio_libero}) NON richiede la confutazione della tesi: richiede l'ingaggio bilaterale (perche' riduco / cosa mi direbbe che sbaglio) e una ragione dichiarata (rischio datato, sizing, rotazione). La ROTAZIONE SETTORIALE resta legittima anche su cluster con view: segue questa stessa gradazione, non la salta.
{MANDATO:sfida}

## DISCIPLINA DEL PAIR TRADE
{MANDATO:pair}
NON VALIDO: long una singola banca / short un ETF paese non comparabile (forzatura fra driver e regioni diverse).
Se non c'e' un pair difendibile, scrivi: "Nessun pair trade difendibile questa settimana."

{MANDATO:opzioni}

## TRADUZIONE IN LINGUAGGIO CHIARO (obbligatoria)
Ogni metrica tecnica va spiegata in prosa, nella stessa frase:
- "Sharpe -1,23" -> "lo Sharpe trailing a -1,23 dice che negli ultimi 12 mesi questa posizione ha distrutto valore corretto per il rischio (fotografia del passato, non una previsione)"
- "5 percentile -3,2%" -> "nel 5 percentile, cioe' in una settimana su venti, la perdita supererebbe il 3,2%"
- "IV sotto realized" -> "la volatilita' implicita e' N punti sotto quella realizzata: il confronto richiede lo stesso orizzonte e una stima del fair value della volatilita', tenendo conto di catalyst e struttura; da solo non determina un acquisto o una vendita di premio"

## NESSUNA SOVRAPPOSIZIONE TRA SEZIONI
La discussione del rischio compare UNA volta, nella sezione pertinente. Niente duplicazioni.

{MANDATO:cassa}

## PARTI DAI SEGNALI OGGETTIVI (Edge Scan)
Ricevi un EDGE SCAN con segnali quantitativi oggettivi (dislocazioni di volatilita', gamma, z-score di prezzo, positioning) gia' calcolati e ordinati per forza. Questi NON sono opinioni: sono misurazioni. Le tue tesi devono ANCORARSI a questi segnali dove esistono. Se un titolo ha un segnale forte (es. "z-score +3,4: posizione tiratissima"), la tua raccomandazione su quel titolo deve tenerne conto e citarlo. Non inventare tesi che ignorano i segnali oggettivi.

# OUTPUT STRUCTURE - STRICT

```
## ACTION TABLE
| Action       | Ticker  | EUR     | Timing      | Confidence |

## BLUF (5 lines)
Il BLUF deve contenere: la mossa piu' importante della settimana, il rischio piu' importante, il piano cash in una riga, il verdetto del cruscotto di rischio, cosa e' cambiato rispetto alla settimana scorsa.

## HIGH-PRIORITY DECISIONS FOR PM (max 3)
### Decision N: ...

## CONTINUITA' DALLA SCORSA SETTIMANA (sezione obbligatoria)
Cita in PROSA le tue 3-5 raccomandazioni piu' recenti con stato ed evoluzione. Esempio:
"La proposta di aggiungere Intesa Sanpaolo (ISP.MI) [Decisione #14] resta in attesa e il titolo e' salito del 3,2%: la confermo. Il taglio di Infineon (IFX.DE) che avevo suggerito [Decisione #11] e' stato saltato dal PM, e a posteriori aveva ragione lui, perche' il titolo ha guadagnato un ulteriore 15%."
Chiudi con un breve paragrafo: "Feedback del PM incorporato questa settimana: ..."
{MANDATO:skipped}

## 1. Regime Macro (400-600 parole)
## 2. Fotografia del Portafoglio e Salute delle Posizioni (600-900 parole)
## 3. Profilo Quantitativo (200-300 parole)
## 4. Lettura Opzioni e Coperture (300-500 parole)
## 5. Pipeline Nuove Posizioni (500-800 parole)
{MANDATO:sezione_5bis}
## 6. Pair Trade (200-300 parole)
## 7. Cripto e DeFi (200-300 parole)
## 8. Mercati Politici e Predittivi (200-300 parole)
## 9. Tesi Non di Consenso (200-300 parole)
## 10. Tabella Scenari
   REGOLE PROBABILITA' (audit/07: proba proxy e non normalizzate = falsa precisione sulle code):
   - Ogni probabilita' numerica deve avere FONTE ESPLICITA e riferirsi ESATTAMENTE all'evento della riga. VIETATI i proxy (es. "57% zero tagli 2026" NON e' la probabilita' del singolo FOMC; la persistenza di un premio NON e' la probabilita' di escalation).
   - Gli scenari devono essere MUTUAMENTE ESCLUSIVI e sommare ~100% (98-102). Niente scenario "non quantificabile" in mezzo ai numeri: o tutti quantificati, o tabella qualitativa.
   - Se una probabilita' non ha fonte, usa un'etichetta qualitativa DICHIARATA come giudizio ("probabile/possibile/coda, giudizio del comitato"), mai un numero inventato.
## 11. Watchlist della Settimana (incl. i PREFERITI del PM: per OGNI ticker nei preferiti del PM una riga con verdetto + catalyst datato + azione consigliata o "nessuna, perche'". I preferiti del PM non vanno MAI ignorati.)
## 12. Nota di Chiusura (100-150 parole)
```
(Gli header restano in italiano come sopra. La ACTION TABLE in cima resta in formato tabella.)

REGOLE ACTION TABLE (il parser la legge in automatico):
- ESATTAMENTE 5 colonne: | Action | Ticker | EUR | Timing | Confidence |
- Action SOLO tra: ADD, BUY, TRIM, SELL, HEDGE, HOLD, RESEARCH.
- Ticker REALE del broker. EUR in formato "60k" o "60.000"; per HOLD/RESEARCH scrivi "0".
- Confidence SOLO: ALTA / MEDIA / BASSA.
- OGNI riga deve avere la sua tesi argomentata in una delle 12 sezioni: niente righe orfane.
- Se la EUR supera la size massima di policy per quel nome (motore di sizing), la TESI della riga DEVE contenere il tag 'SOPRA POLICY: +X pt vs max Y%' con motivazione quantificata — nel memo #45 due ADD sopra policy sono usciti senza tag e il validator li ha flaggati: non deve ripetersi.

# STANDARD DI QUALITA'
- 3500-5500 parole, tutte in italiano scorrevole
- ZERO frasi di copertura vuote ("potrebbe", "si vedra'"): prendi posizione
- OGNI numero deve risalire all'output di uno specialista
- ACTION TABLE sulla prima pagina, sopra ogni altra cosa
- Sezione CONTINUITA' OBBLIGATORIA (e' cio' che distingue Bellomberg da un analista generico)
- Il CRUSCOTTO SCORING ricevuto va sintetizzato (una riga per dominio, col verdetto) dentro la sezione 3.
- Accenti italiani consentiti e incoraggiati (apostrofo ammesso dove serve)
- NIENTE preambolo: inizia direttamente con "## ACTION TABLE"

# FORMATO FINALE
Prima riga: "## ACTION TABLE"
Ultima sezione: "## 12. Nota di Chiusura"
Niente "spero sia utile". Il PM ti cerchera' se avra' bisogno.
"""
# B3 (02/09): il nome del PM entra dal .env; .replace e non f-string perche' il prompt ha graffe sue
CAPO_SYSTEM_PROMPT = CAPO_SYSTEM_PROMPT.replace("{PM_DESC}", PM_DESC)


def _n_segnali_iniettati(righe):
    """Quanti segnali sono DAVVERO finiti nel prompt (non quanti ne sono stati
    misurati). 22/08: la riga di log stampava il totale — sul log vero diceva
    19 mentre nel prompt ce n'erano 14."""
    return sum(1 for r in righe if r.startswith("[forza "))


def _blocco_edge_scan(scan, tetto=None, soglia=None):
    """Le righe EDGE SCAN del prompt del Capo, con OGNI taglio DICHIARATO.

    22/08 (voce F, ok PM). Prima erano righe inline dentro `run_capo` con tre
    silenzi misurati sul log vero di 8 run:
      - `scan["signals"][:14]` senza una parola: sulle run da 19 e 17 segnali il
        Capo ne vedeva 14, cioe' 5 e 3 buttati zitti;
      - la riga di log stampava il TOTALE, non quanti ne iniettava (diceva 19);
      - se lo scanner cadeva il blocco spariva dal prompt, e per DUE strade —
        l'eccezione (che almeno stampava su stdout) e il payload `{"error": ...}`,
        dove `scan.get("signals")` e' None e non stampava nemmeno quello.

    22/08 sera (voce (b) §9-quinquadragies, decisione PM col numero montato
    PRIMA — «+2.100 char ~= 1.050 token» su 23 segnali): il default passa da
    14 a None = TUTTI. Rimisurato sul libro VERO del 22/08 sera dopo la cura:
    +2.659 char ~= +1.285 token (30 segnali sopra 50 — il libro si e' mosso;
    `prova_edge_scan.py` passo [3] lo rimisura a ogni giro). Il Capo alloca su
    questi segnali e li vede tutti; un tetto esplicito resta possibile e
    continua a dichiararsi coi nomi degli esclusi.

    Uno zero da uno scanner che ha girato E' una misura e va detto come tale;
    un'assenza per guasto non lo e'. Sono due rami distinti, apposta.
    """
    if tetto is not None:
        tetto = max(1, int(tetto))      # un tetto <= 0 stampava zero segnali
    righe = []
    if not scan or scan.get("error"):
        motivo = (scan or {}).get("error") or "nessuna risposta dallo scanner"
        righe.append("=== EDGE SCAN — NON DISPONIBILE in questa run ===")
        righe.append(
            "Lo scanner dei segnali quantitativi non ha risposto (motivo: %s). Le "
            "dislocazioni NON sono state misurate: non dedurne che non ce ne siano, e "
            "non scrivere che le tesi sono ancorate a segnali oggettivi." % motivo)
        righe.append("")
        return righe

    segnali = scan.get("signals") or []
    cop = scan.get("copertura") or {}
    nota = (cop.get("nota") or "").strip()
    degradata = cop.get("scansione_degradata") or {}
    senza = cop.get("nessuna_misura") or {}
    # ⚠️ «questo zero E' una misura» e' l'unica frase del blocco che pronuncia la
    # parola misura in senso forte, e la prima stesura la diceva SEMPRE. Ma il
    # ramo scatta anche su un blackout dei dati (i rilevatori tornano [] senza
    # `error`): sul log vero le run danno 11-19 segnali, quindi quando questo
    # ramo scattava l'affermazione era piu' probabilmente FALSA che vera.
    # Ora si dice solo dove e' dimostrabile dal payload.
    completa = (bool(cop) and not degradata and not senza
                and not cop.get("fattoriali_ko")
                and (cop.get("posizioni_scansionate") or 0) > 0)
    if not segnali:
        if completa:
            righe.append("=== EDGE SCAN — NESSUN SEGNALE SOPRA LA SOGLIA ===")
            righe.append(
                ("Ogni rilevatore applicabile ha risposto e nessuna dislocazione "
                 "supera la soglia%s: questo zero E' una misura, non un buco. "
                 % (" di %s" % soglia if soglia is not None else "") + nota).strip())
        else:
            righe.append("=== EDGE SCAN — NESSUN SEGNALE, COPERTURA INCOMPLETA ===")
            mancanti = sorted(set(degradata) | set(senza))
            righe.append(
                ("Zero segnali, ma la copertura NON e' piena%s: questo zero NON e' "
                 "una misura. %s%s"
                 % (" (%d nomi senza misura piena: %s)"
                    % (len(mancanti), ", ".join(mancanti[:12])) if mancanti else "",
                    "Non dedurne che non ci siano dislocazioni. ", nota)).strip())
        righe.append("")
        return righe

    righe.append("=== EDGE SCAN — SEGNALI QUANTITATIVI OGGETTIVI (ranked per forza%s) ==="
                 % (", soglia %s" % soglia if soglia is not None else ""))
    righe.append(
        "Questi sono segnali OGGETTIVI calcolati dai dati di mercato, non opinioni. "
        "Usali come base: cita quelli che supportano le tue decisioni e spiega perche'.")
    if nota:
        righe.append("COPERTURA DELLA SCANSIONE: " + nota)
    # ⚠️ NON `segnali[:None]`+`segnali[None:]`: il secondo e' la lista INTERA,
    # cioe' tutti i segnali dichiarati "esclusi" pur essendo appena stampati.
    mostrati = segnali if tetto is None else segnali[:tetto]
    esclusi = [] if tetto is None else segnali[tetto:]
    for s in mostrati:
        righe.append(
            f"[forza {s['strength']}] {s['ticker']} | {s['name']} = {s['value']} "
            f"({s['direction']}) -> {s['reading']}")
    if esclusi:
        # ⚠️ «forza <= X» si CALCOLA sui rimossi invece di ereditare l'ordinamento
        # del chiamante: cosi' e' vera per costruzione sotto qualunque ordine.
        piu_forte = max(int(x.get("strength") or 0) for x in esclusi)
        nomi = ", ".join("%s %s (%s)" % (x.get("ticker"), x.get("name"),
                                         x.get("strength")) for x in esclusi[:12])
        coda = "" if len(esclusi) <= 12 else " (+%d oltre questi)" % (len(esclusi) - 12)
        # ⚠️ NIENTE nomi di tool qui: `run_capo` chiama l'API SENZA `tools=`, il
        # Capo in una run non puo' chiedere niente a nessuno. Indicargli
        # `get_edge_scan` sarebbe la lezione del 21/08 (il red team e
        # `ask_specialist`) ripetuta il giorno dopo. Gli si danno i NOMI, che
        # sono l'unica cosa che puo' davvero usare.
        righe.append(
            "[... altri %d segnali, di forza <= %d, NON entrano in questo prompt "
            "(ne entrano %d) e in questa run NON puoi richiederli. Sono: %s%s]"
            % (len(esclusi), piu_forte, tetto, nomi, coda))
    solo_prezzo = cop.get("solo_prezzo") or []
    if solo_prezzo:
        righe.append(
            "[NOMI COPERTI DA UN SOLO RILEVATORE (z-score di prezzo), quindi senza "
            "misure di volatilita', gamma o Congresso: %s. Su di loro l'assenza di "
            "quei segnali non e' una misura.]" % ", ".join(solo_prezzo))
    righe.append("")
    return righe


def _blocco_red_team(rt, motivo_guasto=None):
    """Le righe RED TEAM del prompt del Capo: la critica se c'e', l'ASSENZA
    DICHIARATA se no. Stessa forma a due rami di `_blocco_edge_scan` (voce F).

    25/08 (voce A di §9-terquadragies). Prima il blocco viveva inline in
    `run_capo` dentro `try: ... except Exception: pass` e senza ramo else:
    misurato col client finto, a `_red_team` assente il user_msg conteneva
    ZERO menzioni del red team — mentre la regola fissa «RISPONDI AL RED
    TEAM» del system prompt («... Decidere ignorando le obiezioni invalida il
    memo») restava in vigore e spingeva il Capo a citare obiezioni mai
    scritte. L'assenza non e' teorica: `consigliere_multi` importa
    `run_red_team` in un try/except che logga «[!] Red team skipped» e
    prosegue. (Il memo di RESCUE invece la critica di solito ce l'ha:
    `_red_team` persiste incondizionato — base.py, «regenerate_memo lo
    richiede» — e regenerate_memo rimonta tutti i report; resta senza solo se
    la run e' morta PRIMA del red team o se il save DB e' fallito.)

    TRE stati, tre verita' diverse (review avversariale 25/08):
      - critica a registro -> il blocco di sempre;
      - nessuna critica UTILIZZABILE a registro (chiave assente, testo vuoto,
        o i «vuoti dichiarati» della produzione: il red team non scrive mai
        vuoto — segnaposto SEGNAPOSTO_NESSUNA_CRITICA, rifiuto safeguard di
        llm_refusal come blocco unico, testo [ERROR...] che la blackboard
        persiste per contratto) -> ASSENTE, col motivo per-stato;
      - lettura del registro ESPLOSA -> NON DISPONIBILE: qui la verita' e'
        «non SO se una critica esista», non «non c'e'» — l'affermazione forte
        sarebbe inconoscibile.
    In entrambi i rami senza critica, la frase condizionale sui report R2
    copre il rescue monco: se i desk replicano a obiezioni che qui non si
    leggono, il contraddittorio c'e' stato e il Capo non deve ricostruirlo."""
    _FRASE_R2 = (
        "Se un report R2 qui sotto risponde a obiezioni del red team, il "
        "contraddittorio c'e' stato ma la critica non e' arrivata a questo "
        "prompt: dillo al PM invece di ricostruirla a memoria. ")
    if motivo_guasto is not None:
        return [
            "=== RED TEAM — NON DISPONIBILE in questa run (motivo: %s) ===" % motivo_guasto,
            "Il registro del red team non e' leggibile: NON SO se una critica "
            "esista. Non dedurne che le tesi siano state validate, e non citare "
            "ne' inventare obiezioni che non leggi: la tua regola «RISPONDI AL "
            "RED TEAM» si applica solo a obiezioni scritte, e qui non ce ne "
            "sono. " + _FRASE_R2 + "Nel memo dichiara al PM che il "
            "contraddittorio non e' verificabile.",
            "",
        ]
    crit = None
    if isinstance(rt, dict) and rt:
        crit = rt.get(max(rt.keys()))
    elif rt:
        crit = str(rt)
    if not rt:
        motivo = ("chiave `_red_team` assente dalla blackboard: il red team "
                  "non risulta girato")
    else:
        # I «vuoti dichiarati» della produzione: testi non-vuoti che DICONO che
        # una critica non esiste. Presentarli come critica avvenuta («un risk
        # manager ha attaccato le tesi») affermerebbe l'opposto del loro stesso
        # contenuto. La classificazione vive DOVE il registro viene scritto
        # (`red_team.motivo_critica_non_utilizzabile`): un solo posto per il
        # Capo e per il preambolo R2 dei desk — la review 25/08 ha trovato che
        # la cornice R2 senza classificazione stava DISFACENDO la voce A. Se il
        # modulo non importa, in-run la chiave `_red_team` non puo' esistere
        # (la scrive lui): il ramo except e' un'eredita' raggiungibile solo nei
        # RESCUE, e li' si tiene il comportamento pre-classificazione invece di
        # una copia che poi deriva.
        # `crit` passa GREZZO: la normalizzazione (str/strip) e' del
        # classificatore, non doppia — un banco l'ha dimostrato: con lo strip
        # anche qui, togliere quello del classificatore era una mutazione
        # EQUIVALENTE su questo percorso e il presidio non mordeva.
        try:
            from bellomberg.agents.red_team import motivo_critica_non_utilizzabile as _mot_cnu
            motivo = _mot_cnu(crit)
        except Exception:
            motivo = ("registro `_red_team` presente ma senza testo "
                      "all'ultimo round") if not (crit and str(crit).strip()) else None
    if motivo is not None:
        return [
            "=== RED TEAM — ASSENTE in questa run (motivo: %s) ===" % motivo,
            "In questo prompt non c'e' NESSUNA critica del red team: dal "
            "registro le tesi degli specialisti NON risultano attaccate da un "
            "contraddittorio. Non dedurne che siano state validate. La tua "
            "regola «RISPONDI AL RED TEAM» non si applica: non citare ne' "
            "inventare obiezioni del red team. " + _FRASE_R2 + "Nel memo "
            "dichiara al PM che il contraddittorio e' mancato.",
            "",
        ]
    righe = [
        "=== RED TEAM — CRITICA AVVERSARIALE ALLE TESI (leggila e rispondi nelle decisioni) ===",
        "Un risk manager scettico ha attaccato le tesi degli specialisti. "
        "DEVI tenerne conto: dove il red team ha ragione, ridimensiona o aggiungi cautele; "
        "dove sbaglia, spiega perche' procedi comunque. Non ignorarlo.",
        str(crit),
        "NOTA RIPIPELINE (15/07): solo fundamentals/quant/options hanno replicato alla critica in R2. "
        "Macro/EventDesk/Crypto hanno chiuso in R1 PRIMA della critica (by design, non e' una run monca): "
        "il loro silenzio sulle obiezioni NON e' una concessione — sulle obiezioni che toccano quei domini "
        "pesa TU obiezione vs report, coi numeri dei tool.",
        "",
    ]
    return righe


def run_capo(blackboard, portfolio_data=None, memory_db=None, sizing_context=None, scoring_context=None):
    CAPO_MODEL = _modello_llm("capo")   # 05/09: dal .env; assente = ConfigurazioneLLMMancante
    # 05/09 (criterio 5, audit/26): il MANDATO del PM si legge dal disco a OGNI run e compila i
    # segnaposto {MANDATO:...} del system prompt (cassa, tagli, pair trade, opzioni, caccia
    # globale, decisioni saltate, view). Assente o incompleto = MandatoMancante QUI, prima di
    # qualunque chiamata: mai la dottrina di ieri come ripiego (regola 14/07).
    import bellomberg.core.mandato_pm as _mandato_pm
    _mandato = _mandato_pm.carica()
    _system = _mandato_pm.compila(CAPO_SYSTEM_PROMPT, _mandato)
    print("\n" + "=" * 70)
    print("BELLOMBERG CAPO synthesis (" + CAPO_MODEL + ") - memory-aware v4")  # voce 5: etichetta derivata dal model string, non puo' piu' invecchiare
    print("=" * 70)

    specialist_reports = {}
    for sp_name, rounds in blackboard.data.items():
        if rounds and not sp_name.startswith("_"):
            latest_round = max(rounds.keys())
            specialist_reports[sp_name] = {"round": latest_round, "report": rounds[latest_round]}

    try:
        from bellomberg.core.current_facts import current_facts_block
        _facts = current_facts_block()
    except Exception as e:
        _facts = "[CONTESTO n.d.] current_facts: " + type(e).__name__ + ": " + str(e)

    user_msg_parts = [
        _facts,
        "",
        "Oggi e' " + datetime.now().strftime("%A %d %B %Y, %H:%M") + " (CET).",
        "I tuoi specialisti hanno finito. Produci IL memo per il PM.",
        "Segui la STRUTTURA OUTPUT: prima la ACTION TABLE, poi BLUF, DECISIONI PRIORITARIE, CONTINUITA' DALLA SCORSA SETTIMANA, le 12 sezioni.",
        "3500-5500 parole, TUTTO IN ITALIANO scorrevole e leggibile. Espandi i ticker col nome esteso alla prima menzione. Spiega ogni metrica tecnica in prosa. Cita le decisioni passate per ID dove rilevante.",
        "",
    ]

    # MEMORIA PERSISTENTE (Phase 1)
    if memory_db:
        try:
            # 8800 -> 16000 il 20/08 (ok PM "aumentiamo i tetti"): MISURATO, la
            # memoria del Capo pesava 9.026 char e veniva TRONCATA a 8.781 — perdeva
            # la coda ogni run. Il taglio resta dichiarato se un domani si sfora.
            # ⚠️ RETTIFICA 21/08 (audit/25): la riga diceva "con i commenti del PM ora
            # fino a 2.000 caratteri il cap doveva salire". Il 20/08 quei 2.000 erano
            # solo MAX_CHAR_TESI (le TESI, altro canale): dentro QUESTO blocco le
            # "PAROLE DIRETTE DEL PM (VINCOLANTI)" erano ancora tagliate a 120 char,
            # con la virgoletta di chiusura rimessa dopo il taglio. Vero dal 21/08:
            # memory_db.MAX_CHAR_FEEDBACK_PM = 2000, e il taglio si dichiara.
            memory_block = memory_db.build_capo_memory_context(max_chars=MAX_CHAR_MEMORIA_CAPO)  # +1800 TRACK RECORD #190/#211; +1700 (21/07) blocco PAROLE DIRETTE DEL PM — misurato 8031 char pieni: a 6800 il taglio mangiava i trade eseguiti e la lezione #210. 8500 -> 8800 il 26/07 sera-5: la memoria era arrivata a 8365 char (135 di margine) e la riga nuova del quadro-confidence sforava tagliando la frase FINALE, quella che dice al Capo cosa fare coi commenti del PM. Il taglio E' dichiarato (memory_db:2283) — e' cosi' che l'ho visto
            user_msg_parts.append("=== LA TUA MEMORIA (dalle run settimanali precedenti) ===")
            user_msg_parts.append(memory_block)
            user_msg_parts.append("")
            print("[CAPO] Memory loaded: " + str(len(memory_block)) + " chars")
        except Exception as e:
            print("[CAPO] Memory load error: " + str(e))

    if portfolio_data:
        cash = (portfolio_data.get("cash_disponibile_eur")
                or portfolio_data.get("available_capital_eur") or 0)
        mkt = portfolio_data.get("totale_valore_mercato_eur") or 0
        nav_tot = portfolio_data.get("nav_total_eur") or (mkt + cash)
        user_msg_parts.append("=== CAPITALE DISPONIBILE (leggi con attenzione) ===")
        # F43(1) 27/08 (review): «cash EUR 0» con `cash_source: None` e' un BUCO
        # di lettura di portfolio.json, non una cassa vuota — chiave presente e
        # nulla (un payload vecchio senza la chiave non e' una dichiarazione)
        if "cash_source" in portfolio_data and portfolio_data["cash_source"] is None:
            user_msg_parts.append(
                "ATTENZIONE — CASSA NON MISURATA: " + str(portfolio_data.get("cash_source_note"))
                + ". Il cash qui sotto e' 0 per un buco di lettura, NON perche' la cassa sia "
                "vuota: non dedurne ne' «molto cash fermo» ne' «niente da impiegare»; "
                "dichiara il buco nel memo e dimensiona sul capitale investito.")
        # 05/09 (criterio 5, audit F01): «QUESTO E' MOLTO CASH FERMO» usciva per la sola presenza
        # del book, anche con la cassa al 7%. Ora il giudizio viene dal mandato (banda tipica,
        # politica di impiego, cassa minima) e dal saldo vero; la prima riga resta la misura.
        # cassa NON misurata (review 05/09): nessun giudizio dal mandato, solo la misura —
        # altrimenti la riga ATTENZIONE qui sopra e «SOTTO LA MINIMA: nessun impiego» si contraddicevano
        user_msg_parts.append(_mandato_pm.frase_cassa_runtime(
            _mandato, float(cash), float(nav_tot), float(mkt),
            cassa_misurata=not ("cash_source" in portfolio_data and portfolio_data["cash_source"] is None)))
        user_msg_parts.append("")
        user_msg_parts.append("=== PORTAFOGLIO (verita' dal DB live) ===")
        # 20/08 (ok PM): qui c'era `json.dumps(..., indent=2)[:4000]`. MISURATO sul
        # payload vero: il dump pesa 25.691 char, quindi al Capo — che e' quello che
        # DECIDE — arrivavano 4 posizioni su 28 (il 16%), e nav/cash/valore/P&L
        # cadevano in coda. Stessa malattia della voce (59) del 26/07 curata per gli
        # specialisti: si riusa QUELLA vista, non se ne scrive una nuova. Tutte le
        # posizioni, totali in testa, campi tolti dichiarati (peso rimisurato 31/08:
        # ~9,4k char su 29 posizioni, col P&L a cambio storico e il totale broker).
        try:
            from bellomberg.agents.chat_tools import _compatta_portfolio_live
            _book_txt = json.dumps(_compatta_portfolio_live(portfolio_data),
                                   default=str, ensure_ascii=False)
        except Exception as _ce:
            # regola 14/07: se la vista non e' importabile lo si DICHIARA nel prompt,
            # non si torna zitti al dump troncato che nascondeva 24 posizioni
            _book_txt = (json.dumps(portfolio_data, default=str, indent=2)[:4000]
                         + "\n[⚠️ VISTA COMPATTA NON DISPONIBILE (" + type(_ce).__name__
                         + "): questo book e' TRONCATO a 4.000 char e non contiene "
                           "tutte le posizioni ne' i totali — non trarne conclusioni "
                           "sul peso o sull'assenza di un nome]")
        user_msg_parts.append(_book_txt)
        user_msg_parts.append("")

    # TESI DEL PM (16/07): canale DEDICATO — dentro il dump [:4000] qui sopra le tesi
    # sopravvivono solo per le prime posizioni, secondo il loro ordine nel dump;
    # senza questo blocco il Capo decideva trim su nomi di cui non vedeva la tesi del PM.
    try:
        from bellomberg.core.current_facts import pm_theses_block
        _tesi = pm_theses_block()
        if _tesi:
            user_msg_parts.append(_tesi.strip())
            user_msg_parts.append("")
            print("[CAPO] Tesi PM iniettate: " + str(len(_tesi)) + " chars")
        else:
            print("[CAPO] Tesi PM: nessuna tesi nel DB (blocco vuoto, dichiarato)")
    except Exception as e:
        print("[CAPO] Tesi PM skip (dichiarato): " + str(e))
        user_msg_parts.append("[CONTESTO n.d.] Tesi PM: " + type(e).__name__ + ": " + str(e))

    # LOOK-THROUGH CEF (16/07, finding #1a run #45): sconto NAV + cosa contiene il fondo
    # (13F del gestore, proxy DICHIARATO nel blocco), senza ridurlo alla sola beta.
    try:
        from bellomberg.valuation.cef_lookthrough import capo_block
        _cef = capo_block()
        if _cef:
            user_msg_parts.append(_cef)
            user_msg_parts.append("")
            print("[CAPO] Look-through CEF iniettato: " + str(len(_cef)) + " chars")
        else:
            print("[CAPO] Look-through CEF: nessun CEF configurato nel book o dati n.d. (dichiarato)")
    except Exception as e:
        # 05/09 (ordine PM, chat e3): il guasto si DICHIARA NEL MEMO, non solo nel log.
        # Fino a qui questo `except` inghiottiva qualunque guasto del look-through — ed
        # e' cosi' che il NameError di capo_block (costante rimossa dal lotto 5a) e'
        # rimasto invisibile: il blocco spariva dal memo e lo diceva solo un print che
        # nessuno legge. Un buco taciuto al modello e' un fallback silenzioso (PM 14/07).
        _guasto = ("=== LOOK-THROUGH CEF === NON DISPONIBILE: il modulo ha fallito (%s: %s). "
                   "Non e' «nessun fondo chiuso in portafoglio»: e' un dato che MANCA — "
                   "non dedurne niente sui fondi chiusi del book."
                   % (type(e).__name__, str(e)[:200]))
        user_msg_parts.append(_guasto)
        user_msg_parts.append("")
        print("[CAPO] Look-through CEF GUASTO (dichiarato NEL MEMO): " + str(e))

    # MOTORE DI SIZING (#184): limiti deterministici vol x correlazione, base = investito
    if sizing_context:
        user_msg_parts.append(sizing_context)
        user_msg_parts.append("")
        user_msg_parts.append(
            "POLICY DI SIZING (banda di riferimento, NON gabbia — scelta del PM 15/07): le size "
            "massime qui sopra sono la policy di rischio calcolata su volatilita' e correlazione "
            "(base = capitale investito, cash escluso). DI DEFAULT restaci dentro; sui nomi marcati "
            "TAGLIA valuta la riduzione indicata. PUOI proporre una size FUORI banda quando la "
            "convinzione lo giustifica, MA SOLO dichiarandolo NELLA RIGA dell'ACTION TABLE col tag "
            "'SOPRA POLICY: +X pt vs max Y%' e una motivazione QUANTIFICATA (numeri dai tool, non "
            "aggettivi). Una deroga NON dichiarata e' un errore e verra' flaggata dal validator. "
            "Il cash resta un cuscinetto (non diluisce pesi ne' metriche) e il budget stress/VaR "
            "resta il riferimento di sopravvivenza: se lo sfori, dillo esplicitamente.")
        user_msg_parts.append("")

    # CRUSCOTTO SCORING (#186b): score deterministici degli specialisti -> sezione nel memo
    if scoring_context:
        user_msg_parts.append(scoring_context)
        user_msg_parts.append("")

    # EDGE SCAN — segnali quantitativi oggettivi (#181): il Capo parte da QUESTI
    try:
        from bellomberg.portfolio.signal_engine import scan_portfolio
        scan = scan_portfolio(min_strength=50)
        righe_edge = _blocco_edge_scan(scan, soglia=50)
        user_msg_parts += righe_edge
        # `signals` e' gia' filtrato a min_strength: i MISURATI sono
        # `n_signals_total` (la review 22/08: il primo numero era stato corretto
        # e il secondo era rimasto falso).
        print("[CAPO] Edge scan iniettato: %d segnali (sopra soglia: %d, misurati: %s)"
              % (_n_segnali_iniettati(righe_edge), len(scan.get("signals") or []),
                 scan.get("n_signals_total")))
    except Exception as e:
        # 22/08 (voce F): prima qui c'era solo il print, e il blocco spariva dal
        # prompt SENZA UNA PAROLA — il Capo scriveva il memo senza segnali e
        # senza sapere che mancavano. Stessa forma del red team che puo' sparire.
        user_msg_parts += _blocco_edge_scan({"error": "%s: %s" % (type(e).__name__, e)})
        print(f"[CAPO] Edge scan skip: {e}")

    # RED TEAM critique (#181): i contro-argomenti, cosi' il Capo decide avendoli
    # visti — o l'ASSENZA DICHIARATA (voce A 25/08): prima qui c'era un `except
    # Exception: pass` e nessun else, e il blocco spariva dal prompt per TRE
    # strade zitte (chiave assente, critica vuota, eccezione). Stesso cablaggio
    # dell'edge scan qui sopra.
    try:
        _righe_rt = _blocco_red_team(blackboard.data.get("_red_team"))
    except Exception as e:
        try:
            _mot_rt = "%s: %s" % (type(e).__name__, e)
        except Exception:   # __str__ rotto: il nome della classe basta
            _mot_rt = type(e).__name__
        _righe_rt = _blocco_red_team(None, motivo_guasto=_mot_rt)
        # ascii(): un motivo con caratteri fuori dalla codepage della console
        # non deve uccidere il memo per una riga di log (review 25/08).
        print("[CAPO] Red team block error: " + ascii(_mot_rt))
    user_msg_parts += _righe_rt
    if _righe_rt and ("ASSENTE" in _righe_rt[0] or "NON DISPONIBILE" in _righe_rt[0]):
        print("[CAPO] Red team senza critica: dichiarato nel prompt del memo")

    user_msg_parts.append("=== REPORT DEGLI SPECIALISTI (ultimo round di ciascuno) ===")
    # Comitato corrente derivato dal roster VERO (review 15/07: basta copie hardcoded).
    # I nomi legacy vengono accodati SE presenti (regenerate_memo su memo pre-fusione):
    # mai perdere un report esistente, mai [NO REPORT] su specialisti che non esistono piu'.
    try:
        from bellomberg.agents.specialists import ALL_SPECIALISTS as _ALL_SP
        _committee = [s.name for s in _ALL_SP]
    except Exception:
        _committee = ["macro", "options", "eventdesk", "fundamentals", "crypto", "quant"]
    _legacy = sorted(n for n in specialist_reports
                     if n not in _committee and not str(n).startswith("_"))
    for sp_name in _committee + _legacy:
        r = specialist_reports.get(sp_name)
        if r:
            user_msg_parts.append("\n--- " + sp_name.upper() + " (Round " + str(r["round"]) + ") ---")
            txt = r["report"]
            if len(txt) > 30000:
                txt = txt[:30000] + "...[truncated]"
            user_msg_parts.append(txt)
        else:
            # memo PRE-fusione rigenerato: news+politics legacy coprono il dominio
            # eventi — un [NO REPORT] su eventdesk sarebbe FALSO e farebbe scattare
            # la regola dei domini scoperti (confidence cappata) a vuoto.
            if sp_name == "eventdesk" and any(k in specialist_reports for k in ("news", "politics")):
                continue
            user_msg_parts.append("\n--- " + sp_name.upper() + " --- [NO REPORT]")

    user_msg = "\n".join(user_msg_parts)
    print("[CAPO] Context: " + str(len(user_msg)) + " chars")

    client = OpenRouterClient(timeout=900.0, max_retries=2)  # 30c-bis + 26/06: retry del client; 05/09: OpenRouter
    import time as _time
    # Guardia anti-collasso (12/08): il giro esterno riprova UNA volta col nudge
    # se il testo esce sotto soglia senza refusal (l'eco del memo #50).
    _messages = [{"role": "user", "content": user_msg}]
    _collasso_ritentato = False
    from bellomberg.core.llm_client import somma_usage
    total_usage = None
    api_calls = 0
    while True:
        response = None
        last_err = None
        # 26/06: il Capo e' un single point of failure (1 sola chiamata); un blip di rete azzerava
        # tutto il memo (gli specialisti restavano persi). Ritenta su errori transitori (Connection error).
        for _attempt in range(3):
            try:
                # Voce 1 (01/08, decisione PM): STREAMING + tetto 64k. Una non-streaming
                # da 64k l'SDK la rifiuta (stima >10 min); get_final_message() restituisce
                # lo stesso oggetto Message, quindi refusal/stop_reason/usage sotto
                # non cambiano di una virgola.
                api_calls += 1
                with client.messages.stream(
                    model=CAPO_MODEL,
                    max_tokens=CAPO_MAX_TOKENS,
                    thinking={"type": "adaptive"},
                    system=_system,
                    messages=_messages,
                ) as _stream:
                    response = _stream.get_final_message()
                total_usage = somma_usage(total_usage, getattr(response, "usage", None))
                break
            except Exception as e:
                last_err = e
                print("[CAPO] tentativo " + str(_attempt + 1) + "/3 fallito: " + str(e)[:160])
                if _attempt < 2:
                    _time.sleep(5 * (_attempt + 1))
        if response is None:
            print("[CAPO] API error definitivo dopo 3 tentativi: " + str(last_err))
            # I due zeri qui NON sono un consumo reale: sono un Capo morto. Senza "error"
            # sparirebbe dai costi come se fosse gratis (fallback silenzioso): chi registra
            # l'usage legge questa chiave e marca la riga api_error.
            failed_usage = total_usage or somma_usage(None, None)
            failed_usage.update(model=CAPO_MODEL, input_tokens=failed_usage["in"],
                                output_tokens=failed_usage["out"], api_calls=api_calls, error=str(last_err))
            return "[CAPO ERROR]: " + str(last_err), failed_usage

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                text += block.text  # 25/06: concatena TUTTI i blocchi text (non solo il primo)
        # 26/07 (Opus 5, pre-V6): un rifiuto dei safeguard e' un HTTP 200 con
        # stop_reason="refusal" -> NON solleva, i 3 retry sopra non scattano e la
        # concatenazione qui produce stringa vuota. Senza questa riga il memo di una
        # run da ~10 EUR diceva "[CAPO] No output", indistinguibile da un modello muto:
        # la causa si DICHIARA in testa al memo (regola PM 14/07).
        _rif = _refusal_reason(response, "CAPO")
        if _rif:
            print("[CAPO] " + _rif[:220])
            text = (_rif + "\n\n" + text) if text.strip() else _rif
        # Guardia anti-collasso (12/08): sotto soglia senza refusal = eco/annuncio,
        # non un memo. UN retry col nudge (assistant+user in coda, ruoli alternati).
        _collassato = (not _rif) and len(text.strip()) < SOGLIA_COLLASSO_MEMO
        if _collassato and not _collasso_ritentato:
            _collasso_ritentato = True
            print("[CAPO] memo COLLASSATO (" + str(len(text.strip())) + " char < soglia "
                  + str(SOGLIA_COLLASSO_MEMO) + "): eco/annuncio invece del memo — UN retry col nudge")
            _messages = _messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": NUDGE_COLLASSO_MEMO},
            ]
            continue
        break
    if _collassato and _collasso_ritentato:
        _marc = ("[MEMO COLLASSATO: " + str(len(text.strip())) + " char anche dopo il retry — "
                 "quanto segue e' un'eco/annuncio, NON il memo. Rilanciare con regenerate_memo.py "
                 "(i report degli specialisti sono salvi in DB).]")
        print("[CAPO] " + _marc)
        text = _marc + "\n\n" + text
    if not text:
        text = "[CAPO] No output"
    if getattr(response, "stop_reason", None) == "max_tokens":
        print("[CAPO] WARNING: output a max_tokens (" + str(CAPO_MAX_TOKENS) + "): possibile troncamento del memo")
        text += ("\n\n> *[NOTA AUTOMATICA: la sintesi ha raggiunto il limite di output e potrebbe essere "
                 "troncata in coda. Aumentare CAPO_MAX_TOKENS o ridurre il budget di thinking.]*")

    rimpiazzi = {
        "₹": "INR ", "₽": "RUB ", "₩": "KRW ", "¥": "JPY ",
        "—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
    }
    for old, new in rimpiazzi.items():
        text = text.replace(old, new)

    data = datetime.now().strftime("%d/%m/%Y")
    marker_mandato = ("[MANDATO PM: impronta " + _mandato_pm.impronta(_mandato)
                      + "; origine " + str(_mandato.get("origine"))
                      + "; dichiarato_il " + str(_mandato.get("dichiarato_il")) + "]")
    final = ("# Bellomberg Weekly Research Note - " + data + " (Multi-Agent v3 + memory)\n\n"
             + marker_mandato + "\n\n" + text)

    usage = total_usage or somma_usage(None, None)
    usage.update(model=CAPO_MODEL, input_tokens=usage["in"], output_tokens=usage["out"], api_calls=api_calls)
    print("[CAPO] Done. Tokens: in=" + str(usage["input_tokens"]) + " out=" + str(usage["output_tokens"]))
    return final, usage
