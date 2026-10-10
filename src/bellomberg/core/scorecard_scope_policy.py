"""Bounded Capo scorecard declarations; not a financial or free-prose certifier.

Only the new evidence-followup contract calls this module. The producer of
Reflection36, its requests, saved lessons and historical Capo requests are intact.
"""
from functools import lru_cache
from hashlib import sha256
import json
import re

from bellomberg.core.reflection_policy import descriptive_groups_for, MIN_OUTCOMES

POLICY = 'capo-scorecard-scope/2'
OPEN = '<scorecard_bindings>'
CLOSE = '</scorecard_bindings>'
USES = {'description', 'sizing', 'selection', 'ranking', 'action_rule', 'operational_lesson'}
INSTRUCTIONS = """
SCORECARD SCOPE capo-scorecard-scope/2 [src: scorekeeper]: queste regole prevalgono
sulle istruzioni generiche PESA LE FIRME SUL TRACK RECORD, pesa le voci, alza la
soglia dei BUY e ALTA deve battere MEDIA/BASSA. Usa i group_id esatti e il loro n.
Sotto 36 esiti attestati nel SINGOLO gruppo: SOLO descrizione; conserva le statistiche
ma nessuna lezione operativa o modifica a sizing, selezione, ranking o regole d'azione.
Overall non presta il campione a BUY, altri gruppi, titoli o sottoinsiemi. Confronti
operativi richiedono OGNI gruppo coinvolto operational_eligible=true. Un gruppo
UNVERIFIED non attesta neppure le statistiche; dichiara la lacuna. La soglia minima
non prova indipendenza, significativita', causalita', alpha o P&L del conto: conserva
method_note e i limiti degli orizzonti/valuta/firme euristiche dello scorekeeper.
Per OGNI uso del track record riporta TUTTI i group_ids coinvolti, anche quelli
oggetto della raccomandazione, in UN solo blocco finale:
<scorecard_bindings>{"rows":[{"group_ids":["action:BUY"],"use":"description",
"instruction":"estratto letterale della frase presente nel memo"}]}</scorecard_bindings>
Grammatica chiusa: sole chiavi rows/group_ids/use/instruction; 0..32 righe,
instruction 1..1600 caratteri, gruppi non vuoti e senza duplicati. use e' esattamente
description, sizing, selection, ranking, action_rule oppure operational_lesson.
Zero righe se nessun uso. Il controllo verifica i binding e UNA sola forma di prosa:
un verbo d'azione su size/peso/posizione giustificato, nella stessa proposizione (grazie
a, perche', dato, given, because, sulla base di, parentesi), da una CITAZIONE NUMERICA
dello scorecard: etichetta o group_id del gruppo accanto alla misura (35 esiti, n=35,
hit rate dei BUY 54%). Quella frase e' un uso del gruppo citato e del gruppo oggetto
della size, overall compreso, da dichiarare nei binding; sotto 36 la frase resta nel
memo con la nota di rifiuto. Ogni altra prosa, le parafrasi e il significato economico:
NOT_ASSESSED, mai autorizzati da questo controllo.
"""
# POLICY /2 (10/10, prosa legata v2, regola stretta v3): the system prompt changed, so a Capo checkpoint
# saved under /1 fails closed ("Capo checkpoint contract changed") instead of resuming
# with the old rules. Accepted by the PM: no weekly run was in flight.

# Closed prose forms, matched on the NORMALIZED line (markdown list/quote/heading
# markers, bold/italic/backticks, spaces and final punctuation removed).
PROSE_FORMS = {
    'un aumento del sizing.': 'sizing',
    'una modifica della selezione.': 'selection',
    'una modifica del ranking.': 'ranking',
    "una modifica delle regole d'azione.": 'action_rule',
    'una lezione operativa.': 'operational_lesson',
}
_CLOSED = {**{k.rstrip('.'): v for k, v in PROSE_FORMS.items()},
           'un aumento della size': 'sizing', 'un aumento del peso': 'sizing',
           'un aumento della posizione': 'sizing'}
_CLOSED_RE = re.compile(r'il track record di (\S+) giustifica (.+)', re.I)

# Operational prose (PM 09/10 "Riconosci anche la prosa"; v3 10/10 after two
# adversarial reviews: v1 24/64 and v2 13/67 false positives). PRECISION FIRST: a
# sentence is rejected ONLY when all three are written out in the same clause:
#   1. an action verb (1st plural anywhere; imperative, 1st singular or a nominal
#      "Aumento della size" only at the start; "raccomando/proponiamo di" +
#      infinitive; "we/let's" + verb; present/future passive "viene portata")
#      governs a size/weight/position/% object;
#   2. an EXPLICIT SCORECARD CITATION: a group label or id of the scope ADJACENT to
#      a measure (35 esiti, n=35, 35 outcomes, hit rate/track record + 54%, su 35),
#      in one chunk of glue words. A numeric citation without label ("il loro hit
#      rate (30 esiti)") counts only for the group that owns the size (size dei BUY)
#      and only with a full head and a named count (v3.1: never "del 15% annuo",
#      "su 35 commesse", "il campione su 35 pazienti");
#      generic words (esito, campione, track record without a number) never cite;
#   3. a justification link: grazie a, perche', dato/visto (che), sulla base, sulla
#      scorta, in forza, forte di, given, because, based on, thanks to, as the...,
#      owing/due to, in view of, on the strength of, sostenuti da; "X giustifica:" or a
#      "Dato ..." clause before the verb; a parenthesis of the action clause; a
#      colon followed directly by the citation.
# Everything else is NOT_ASSESSED: clauses after ";", ", e", ma/mentre, spaced
# dashes; reported speech before the verb (il Red Team sostiene/propone, argues,
# il PM ricorda) and reported pieces (lo scorecard mostra); sentences declaring the
# limit (sotto soglia, solo descrittivo, insufficiente, solo ALTA supera la soglia);
# negations, conditionals, questions, quotations, `>` lines; hedges (cautela,
# contesto, appendice); less weight given to the scorecard. Lexical, not semantic.
_MARKUP = re.compile(r'^\s*(?:#{1,6}\s+)?(?:(?:[-*+\u2022]|\d{1,3}[.)])\s+)?')
_EMPHASIS = re.compile(r'\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)|`')
_QUOTE_PAIRS = {'"': '"', '\u201c': '\u201d', '\u00ab': '\u00bb'}
_SENTENCE = re.compile(r'(?<=[.!?])\s+')
_HARD = re.compile(r'\s*;\s*|\s+[\u2014\u2013-]+\s+|\s*[\u2014\u2013]\s*|,\s+(?:e|ed|and)\s+'
                   r'|,?\s+(?:mentre|ma|tuttavia|whereas|while|but|however)\s+', re.I)
_SKIP = re.compile(r"\b(?:non|n\u00e9|mai|nessun[oa]?|senza|evit\w*|se|qualora|nel caso|quando|"
                   r"storicamente|potremmo|potrebbe|insufficient\w*|sotto soglia|not|no|never|without|"
                   r"avoid\w*|if|unless|would|could|might|when|whenever|historically|below threshold)\b"
                   r"|n't\b", re.I)
# A sentence that declares the limit of the scorecard is never an operational use.
_LIMIT = re.compile(r"\b(?:sotto\s+(?:la\s+)?soglia|sotto\s+(?:i\s+)?36|soglia|threshold|36\s+bar|"
                    r"insufficient\w*|troppo\s+(?:piccol|poch)\w*|pochi|too\s+(?:small|few)|fragil\w*|"
                    r"solo\s+(?:come\s+|una\s+)?(?:descri\w*|contesto)|descritt\w*|descriptive|description\s+only|"
                    r"non\s+operativ\w*|not\s+operational|solo\s+\w+\s+supera|only\s+\w+\s+clears|"
                    r"below\s+(?:the\s+)?36)\b", re.I)
_HEDGE = re.compile(r'\b(?:cautela|prudenza|con riserva|da leggere|per riferimento|solo (?:come )?contesto|'
                    r'come contesto|in appendice|in osservazione|riportat\w*|descritt\w*|descriptive|'
                    r'for reference|reference only|context only|as context|in the appendix)\b', re.I)
# Reported speech BEFORE the verb: the instruction belongs to somebody else. Only the
# verbs of "osservare" frame: the noun "osservazioni" is a scorecard count (v3.1).
_FRAME = re.compile(r"\b(?:red\s+team|sostien\w*|argu\w*|propone|propongono|propost\w*|proposes?|suggerisce|"
                    r"suggeriscono|suggests?|osserv(?:a|ano|iamo|ato|ata)|observes?|notes?|nota\s+che|secondo|"
                    r"according|ricord\w*|"
                    r"segnal\w*|dice|dicono|says?|afferm\w*|claims?|chied\w*|asks?|ritien\w*|believes?|"
                    r"thinks?|scrive|scrivono|writes?|warns?|avvert\w*)\b", re.I)
_REPORTED = re.compile(r'\b(?:red team|scorecard (?:mostra|riporta|indica)|scorecard shows|osserv(?:a|ano|iamo)|'
                       r'segnal\w*|ricord\w*|observes?|notes? that|reports?)\b', re.I)
_SIZE_WORD = (r'(?:size|sizing|pes[oi]|posizion[ei]|esposizion[ei]|allocazion[ei]|weights?|weighting|'
              r'positions?|exposure|allocation|stake)')
_SIZE = re.compile(r'\b' + _SIZE_WORD + r'\b|\d+(?:[.,]\d+)?\s*%', re.I)
# "Riduciamo il peso attribuito al campione BUY": less weight to the scorecard is compliant.
_LESS_SCORECARD = re.compile(r'\b(?:pes[oi]|weight)\s+(?:attribuit|dat|assegnat|given|assigned|attached)\w*'
                             r'\s+(?:a|al|allo|alla|ai|agli|alle|to)\b', re.I)
_MARKER = re.compile(
    r"(?<!\w)(?:grazie\s+(?:a|al|allo|alla|ai|agli|alle)(?!\w)|grazie\s+all['\u2019]|perch[e\u00e9\u00e8]|"
    r"poich[e\u00e9\u00e8]|(?<!\bil\s)(?<!\bun\s)(?:dat|vist)o\s+che|"
    r"(?<!\bil\s)(?<!\bun\s)(?:dat|vist|considerat)[oaie](?=\s+(?:il|lo|la|i|gli|le|un|una|"
    r"che|quest\w+|tal[ei])\b|\s+l['\u2019])|sulla\s+base\s+d\w*|alla\s+luce\s+d\w*|in\s+base\s+a\w*|"
    r"in\s+virt[u\u00f9]['\u2019]?\s+d\w*|fort[ei]\s+d\w*|sulla\s+scorta\s+d\w*|in\s+forza\s+d\w*|"
    r"(?:sostenut|supportat|giustificat|motivat)[oaie]\s+d\w*|"
    r"given|because|since(?=\s+(?:the|our|its|this|these)\b)|as(?=\s+(?:the|our|its|their)\b)|thanks\s+to|"
    r"considering(?=\s+(?:the|our|its|their)\b)|on\s+the\s+strength\s+of|owing\s+to|due\s+to|in\s+view\s+of|"
    r"based\s+on|in\s+light\s+of|on\s+the\s+back\s+of|(?:supported|backed|justified|driven)\s+by)"
    r"(?!(?<=\w)\w)", re.I)
_MARKER_START = re.compile(r'^\s*(?:(?:quindi|pertanto|dunque|allora|so|then)\s*,?\s+)?' + _MARKER.pattern, re.I)
_JUSTIFIES = re.compile(r'\b(?:giustific\w*|support\w*|sostien\w*|consent\w*|permett\w*|justif\w*|'
                        r'warrant\w*)\b', re.I)
_CONF_NOUN = r'(?:convinzione|confidenza|conviction|confidence|fascia)\s+'
_PREP = (r'(?:(?:di|dei|degli|delle|del|della|sui|sugli|sulle|sul|su|per|for|of|on|in|nei|nelle|negli)'
         r'\s+)?')
_FILLER = (r'(?:(?:the|our|le|i|gli|nostre|nostri|idee|ideas|operazioni|raccomandazioni|segnali|calls|'
           r'bucket|azione|action|a|con)\s+){0,3}')
# Citation tokens: a scorecard head (one token), group ids, n=, percentages, numbers.
_TOKEN = re.compile(r"(?i:track[\s-]?record|hit[\s-]?rate|win[\s-]?rate|tasso\s+di\s+successo|scorecard|"
                    r"campione|hit)(?!\w)|(?:action|confidence|specialist):[\w\-]+|(?i:n)\s*=\s*\d+"
                    r"|\d+(?:[.,]\d+)?\s*%|\d+(?:[.,]\d+)?|\w+['\u2019]|\w+|[^\w\s]")
_HEADS = {'track record', 'track-record', 'trackrecord', 'hit rate', 'hit-rate', 'hitrate', 'win rate',
          'win-rate', 'winrate', 'scorecard', 'campione', 'hit'}
_WEAK_HEADS = {'campione', 'hit'}  # heads only beside a group label: "il campione su 35 pazienti" is not one
_COUNT_NOUNS = {'esiti', 'outcomes', 'osservazioni', 'observations', 'operazioni'}
_LABEL_ONLY_COUNTS = {'operazioni'}  # "il loro track record su 35 operazioni di M&A" is not the scorecard
_CLOSED_TRADES = {('trade', 'chiusi'), ('closed', 'trades')}  # "35 trade chiusi": a two-word count noun
_PRONOUNS = {'loro', 'its', 'their'}
_GLUE = set("di del dello della dei degli delle dell' d' al allo alla ai agli alle all' a ad il lo la i gli le "
            "l' un una uno un' su sui sul sulla sugli sulle of the at on over is are was e' \u00e8 conta "
            "contano counts has have ha hanno with con pari soli only suo sua our nostro nostri nostre "
            "fascia bucket convinzione conviction confidenza confidence idee ideas azione action".split())
_GLUE |= _PRONOUNS | _COUNT_NOUNS
_COORD = {'e', 'ed', 'and', '&', '/'}
_IT_ANYWHERE = ('compriamo acquistiamo vendiamo cediamo aumentiamo alziamo raddoppiamo incrementiamo '
                'riduciamo tagliamo dimezziamo accumuliamo alleggeriamo sovrappesiamo sottopesiamo '
                'aggiungiamo rafforziamo portiamo riportiamo porteremo').split()
_IMPERATIVE = ('compra acquista vendi cedi aumenta alza raddoppia incrementa riduci taglia dimezza '
               'accumula alleggerisci sovrappesa sottopesa aggiungi rafforza porta riporta '
               'buy sell add trim increase reduce raise cut double halve overweight underweight '
               'accumulate scale').split()
_FIRST_SINGULAR = ('compro acquisto vendo cedo aumento alzo raddoppio incremento riduco taglio dimezzo '
                   'accumulo alleggerisco aggiungo rafforzo').split()
_NOMINAL = 'aumento incremento riduzione taglio rialzo raddoppio increase reduction'.split()
_EN_BASE = ('buy sell add trim increase reduce raise cut double halve overweight underweight accumulate '
            'size scale lift boost').split()
_INFINITIVES = ('comprare acquistare vendere aumentare alzare raddoppiare incrementare ridurre tagliare '
                'dimezzare accumulare alleggerire sovrappesare sottopesare portare riportare').split()


def _cased(words):  # lowercase or Capitalized, never ALL CAPS (BUY/ADD are labels)
    return '|'.join(sorted({w for word in words for w in (word, word.capitalize())}, key=len, reverse=True))


_VERB = re.compile(
    r'(?<![\w:\-])(?:' + _cased(_IT_ANYWHERE) + r')(?![\w:\-])'
    r"|\b(?:[Ww]e|[Ll]et['\u2019]s)(?:\s+(?:will|should|shall|must|now|also))?\s+(?:"
    + '|'.join(_EN_BASE) + r')\b'
    r"|\b[Ww]e(?:\s+are|['\u2019]re)\s+(?:increasing|cutting|trimming|raising|reducing|lifting|boosting|"
    r'doubling|halving)\b'
    r'|\b(?:[Rr]accomand(?:o|iamo)|[Ss]uggerisco|[Ss]uggeriamo|[Pp]ropongo|[Pp]roponiamo|[Cc]onsiglio|'
    r'[Cc]onsigliamo)\s+di\s+(?:' + '|'.join(_INFINITIVES) + r')\b')
_PASSIVE = re.compile(
    r'\b(?:viene|vengono|verr[a\u00e0]|verranno|sar[a\u00e0]|saranno)\s+(?:(?:ri)?portat|aumentat|alzat|'
    r'incrementat|ridott|tagliat|raddoppiat|dimezzat)[oaie]\b'
    r'|\b(?:is|are|will\s+be)\s+(?:raised|increased|cut|reduced|trimmed|doubled|halved|lifted|brought)\b')
_OPENING_VERB = re.compile(
    r'^(?:(?:[Qq]uindi|[Pp]ertanto|[Dd]unque|[Aa]llora|[Tt]hen|[Ss]o)\s*,?\s+)?(?:(?:'
    + _cased(_IMPERATIVE) + r')(?![\w:\-])|(?:' + _cased(_FIRST_SINGULAR)
    + r")(?=\s+(?:il|lo|la|i|gli|le|un|una|uno)\b|\s+l['\u2019]|\s+[A-Z]{2,}\b))")
_OPENING_NOMINAL = re.compile(r'^(?:' + _cased(_NOMINAL) + r')(?=\s+(?:della|del|dello|dei|delle|degli|di|in|of|the)\b)')


def _normalize(line):
    text = _EMPHASIS.sub('', _MARKUP.sub('', line))
    return ' '.join(_MARKUP.sub('', text).split()).rstrip(' .;:!,')


def _strip_quotes(text):
    """Blank quoted spans in ONE pass: an unclosed opener never rescans the line."""
    out, index, dead = [], 0, set()
    while index < len(text):
        close = _QUOTE_PAIRS.get(text[index])
        if close is not None and text[index] not in dead:
            end = text.find(close, index + 1)
            if end >= 0:
                out.append(' ')
                index = end + 1
                continue
            dead.add(text[index])
        out.append(text[index])
        index += 1
    return ''.join(out)


@lru_cache(maxsize=64)
def _patterns(keys):
    """Labels of the groups PRESENT in scope (never a fixed list) and the size-owner regex.

    Returns (labels, objects): `labels` maps a prose token (BUY, ALTA, overall) to
    its scope key; `objects` matches a label owning the size (size dei BUY, BUY
    sizing, BUY ideas, the conviction BASSA bucket, size delle idee a convinzione
    BASSA; confidence levels only with their noun), or None.
    """
    labels = {}
    for key in keys:
        prefix, _, name = key.partition(':')
        if prefix in ('action', 'confidence') and name.isupper() and len(name) > 2:
            labels[name] = key
    if 'overall' in keys:
        labels['overall'] = 'overall'
    edge = r'(?<![\w:\-])(?:{})(?![\w\-])'
    actions = sorted((n for n, k in labels.items() if k.startswith('action:')), key=len, reverse=True)
    levels = sorted((n for n, k in labels.items() if k.startswith('confidence:')), key=len, reverse=True)
    owners = ([edge.format('|'.join(map(re.escape, actions)))] if actions else []) + (
        [r'(?i:' + _CONF_NOUN + r')' + edge.format('|'.join(map(re.escape, levels)))] if levels else [])
    if not owners:
        return labels, None
    one = r'(?:' + '|'.join(owners) + r')'
    coord = r'(?:\s*(?:,|e|ed|and|/|&)\s*' + _PREP + r'(?:quell[oaie]\s+)?' + one + r')*'
    size = r'(?i:' + _SIZE_WORD + r')'
    objects = (r'(?<!\w)' + size + r'\s+(?i:' + _PREP + _FILLER + r')' + one + coord
               + r'|' + one + coord + r'\s+(?i:ideas|idee|bucket|calls)(?!\w)')
    if actions:
        action = edge.format('|'.join(map(re.escape, actions)))
        objects += (r'|' + action + r'\s+' + size + r'(?!\w)'
                    + r'|^\s*(?i:(?:i|gli|le|the|our)\s+)' + action + coord)
    return labels, re.compile(objects)


def _label_key(token, labels, groups):
    if ':' in token:
        prefix, _, name = token.partition(':')
        for key in (token, prefix.lower() + ':' + name.upper(), prefix.lower() + ':' + name.lower()):
            if key in groups:
                return key
        return None  # an id outside the scope is never a group of this check
    return labels.get('overall' if token.lower() == 'overall' else token)


def _citations(piece, groups, labels):
    """Explicit scorecard citations in a justification piece: (labelled keys, unlabelled?).

    Only the FIRST chunk counts: the citation must open the piece (after the link
    word, the parenthesis or the colon), so a later "con hit rate dei BUY ..." is
    not the justification. A chunk is a run of glue tokens (articles, prepositions,
    numbers, labels, the scorecard heads); any other word ends it. Commas join only numbers/labels (54%,
    35 esiti / ALTA 50, MEDIA 30), a colon only after a head (track record BASSA:
    28 esiti), "e/and" only before the measure (BUY e ADD (35 e 36 esiti)). The
    chunk cites its labels when it holds a count (35 esiti, n=35, su 35 after a
    head, never "su 35 anni") or a head with a percentage. Without labels it is a
    numeric citation ONLY with a full head (not campione/hit) and a named count
    beside its number (35 esiti/outcomes/osservazioni/operazioni/trade chiusi, n=35):
    never a bare percentage ("il loro track record del 15% annuo") nor "su 35
    commesse" (third review, 10/10).
    """
    tokens = [m.group(0) for m in _TOKEN.finditer(piece.lstrip(" '\u2019,")[:_WINDOW])]
    cited, unlabelled = [], False
    state = {'labels': [], 'head': False, 'strong': False, 'count': False, 'named': False, 'pct': False}

    def kind(tok):
        low = tok.lower().replace('\u2019', "'")
        if ' '.join(low.split()) in _HEADS or re.fullmatch(r'tasso\s+di\s+successo', low):
            return 'head'
        if _label_key(tok, labels, groups):
            return 'label'
        if re.fullmatch(r'n\s*=\s*\d+', low):
            return 'n'
        if low.endswith('%'):
            return 'pct'
        if low[:1].isdigit():
            return 'num'
        return low

    kinds = [kind(t) for t in tokens]
    depth = 0
    for i, (tok, k) in enumerate(zip(tokens, kinds)):
        prev = kinds[i - 1] if i else None
        nxt = kinds[i + 1] if i + 1 < len(kinds) else None
        if k == '(':
            depth += 1
        elif k == ')':
            depth = max(0, depth - 1)
        elif k == ',':
            if not (prev in ('num', 'pct', 'label', 'n') or prev in _COUNT_NOUNS) or nxt not in ('num', 'pct', 'label', 'n'):
                break
        elif k == ':':
            if not state['head']:
                break
        elif k in _COORD:
            if depth == 0 and (state['count'] or (state['head'] and state['pct'])):
                break
        elif k == 'head':
            state['head'] = True
            state['strong'] = state['strong'] or tok.lower() not in _WEAK_HEADS
        elif k == 'label':
            state['labels'].append(_label_key(tok, labels, groups))
        elif k == 'n':
            state['count'] = state['named'] = True
        elif k == 'pct':
            state['pct'] = True
        elif k == 'num':
            j = i + 1
            while j < min(len(kinds), i + 4) and (kinds[j] in _COORD or kinds[j] == 'num'):
                j += 1
            after = kinds[j] if j < len(kinds) else None
            if after in _COUNT_NOUNS or (after, kinds[j + 1] if j + 1 < len(kinds) else None) in _CLOSED_TRADES:
                state['count'] = True
                state['named'] = state['named'] or after not in _LABEL_ONLY_COUNTS
            elif state['head'] and prev in ('su', 'over', 'on', 'soli', 'only') and (
                    after is None or not after[:1].isalpha() or after in _GLUE or after in _COORD):
                state['count'] = True  # "su 35" ends the measure; "su 35 commesse" counts something else
        elif k in _GLUE:
            pass
        else:
            break
    measured = state['count'] or (state['head'] and state['pct'])
    if state['labels'] and measured:
        cited = state['labels']
    elif not state['labels'] and state['strong'] and state['named']:
        unlabelled = True
    return list(dict.fromkeys(cited)), unlabelled


def _first_depth0(text, char):
    """Index of the first `char` outside parentheses, -1 if none (visits only ( ) and char)."""
    depth = 0
    for match in re.finditer('[()' + re.escape(char) + ']', text):
        c = match.group(0)
        if c == '(' and char != '(':
            depth += 1
        elif c == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            return match.start()
    return -1


_WINDOW = 400  # a citation opens its piece: only its first characters are tokenized
_MAX_VERBS = 6  # per clause: each verb rescans the clause tail, so the work stays linear


def _operational_verbs(clause, opening):
    spans = []
    for pattern, kind in ((_VERB, 'verb'), (_PASSIVE, 'passive')):
        for match in pattern.finditer(clause):
            if len(spans) == _MAX_VERBS:
                break
            spans.append((match.start(), match.end(), kind))
    if opening:
        for pattern, kind in ((_OPENING_VERB, 'verb'), (_OPENING_NOMINAL, 'nominal')):
            first = pattern.match(clause)
            if first and (first.start(), first.end(), 'verb') not in spans:
                spans = [(first.start(), first.end(), kind)] + spans[:_MAX_VERBS - 1]
                break
    return sorted(spans)


def _recognize(line, groups):
    """Operational uses of scorecard groups in one memo line: [(group_ids, use, form)]."""
    if line.lstrip().startswith('>'):
        return []  # a markdown quotation is somebody else's text, not the Capo's instruction
    text = _normalize(line)
    closed = _CLOSED_RE.fullmatch(text)
    if closed and closed.group(2).lower() in _CLOSED:
        return [([closed.group(1)], _CLOSED[closed.group(2).lower()], 'closed')]
    labels, owners = _patterns(tuple(sorted(groups)))
    found = []
    for sentence in _SENTENCE.split(_strip_quotes(text)):
        if not sentence or '?' in sentence or _SKIP.search(sentence) or _LIMIT.search(sentence):
            continue
        spoken = False  # reported speech in an earlier clause of the sentence
        for number, clause in enumerate(_HARD.split(sentence)):
            framed, spoken = spoken, spoken or bool(_FRAME.search(clause))
            if _HEDGE.search(clause):
                continue
            for start, end, kind in _operational_verbs(clause, number == 0):
                before, after = clause[:start], clause[end:]
                if framed or _FRAME.search(before):
                    continue  # il Red Team sostiene che / propone: / argues we ...
                colon = _first_depth0(after, ':')
                head = after if colon < 0 else after[:colon]
                marker = _MARKER.search(head)
                opened = _first_depth0(head, '(')
                cut = min([len(head)] + ([opened] if opened >= 0 else []) + ([marker.start()] if marker else []))
                target = (before + ' ' if kind == 'passive' else '') + head[:cut]
                if not _SIZE.search(target) or _LESS_SCORECARD.search(target):
                    continue
                pieces = []
                opening = _MARKER_START.match(before)
                if opening:
                    pieces.append(before[opening.end():])
                elif ':' in before and _JUSTIFIES.search(before.rsplit(':', 1)[0]):
                    pieces.append(before.rsplit(':', 1)[0])
                if marker:
                    pieces.append(after[marker.end():])
                plain = head[:marker.start()] if marker else head
                pieces += [m.group(0) for m in re.finditer(r'\([^()]*\)', plain)]
                if colon >= 0 and not marker and kind != 'nominal':
                    pieces.append(after[colon + 1:])  # links only if the citation opens it
                cited, unlabelled = [], False
                for piece in pieces:
                    if _REPORTED.search(piece):
                        continue
                    keys, numeric = _citations(piece, groups, labels)
                    cited += keys
                    unlabelled = unlabelled or numeric
                objects = []
                if owners is not None:
                    for match in owners.finditer(target):
                        objects += [k for k in (_label_key(m.group(0), labels, groups)
                                                for m in _TOKEN.finditer(match.group(0))) if k]
                if not cited and not (unlabelled and objects):
                    continue
                named = list(dict.fromkeys(objects + cited))
                if (named, 'sizing', 'operational') not in found:
                    found.append((named, 'sizing', 'operational'))
    return found


def project(scorecard):
    return {'policy': POLICY, 'minimum_outcomes': MIN_OUTCOMES,
            **descriptive_groups_for(scorecard), 'semantic_scope': 'NOT_ASSESSED'}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _binding_code(row, groups):
    if (not isinstance(row, dict) or set(row) != {'group_ids', 'use', 'instruction'}
            or not isinstance(row['use'], str) or row['use'] not in USES
            or not isinstance(row['instruction'], str) or not row['instruction'].strip()
            or len(row['instruction']) > 1600
            or not isinstance(row['group_ids'], list) or not row['group_ids']
            or not all(isinstance(k, str) and k for k in row['group_ids'])):
        return 'INVALID_BINDING'
    keys = row['group_ids']
    if len(keys) != len(set(keys)):
        return 'DUPLICATE_GROUP'
    if any(key not in groups for key in keys):
        return 'UNKNOWN_GROUP'
    if any(groups[key]['status'] != 'ATTESTED' for key in keys):
        return 'GROUP_UNVERIFIED'
    if row['use'] != 'description' and any(not groups[key]['operational_eligible'] for key in keys):
        return 'GROUP_NOT_OPERATIONAL'
    return None


def assess(raw, scope):
    """Reject individual declarations only; never rewrite raw or block the memo.

    An accepted binding attests group membership/threshold and a literal link to
    the memo. It does NOT establish that the model classified that prose honestly.
    """
    result = {'policy': POLICY, 'raw_sha256': sha256(raw.encode('utf-8')).hexdigest(),
              'status': 'NOT_ASSESSED', 'binding_status': 'ABSENT',
              'accepted_bindings': [], 'rejected_bindings': [], 'explicit_prose': [],
              'prose_scope': 'NOT_ASSESSED', 'semantic_scope': 'NOT_ASSESSED'}
    prose = numbered = raw  # numbered keeps the Capo's own line numbers for the notice
    bindings = []
    if OPEN in raw or CLOSE in raw:
        result['binding_status'] = 'INVALID'
        try:
            if raw.count(OPEN) != 1 or raw.count(CLOSE) != 1:
                raise ValueError('envelope count')
            start, finish = raw.index(OPEN), raw.index(CLOSE)
            if finish < start or finish - start > 64000:
                raise ValueError('envelope order/length')
            prose = raw[:start] + raw[finish + len(CLOSE):]
            numbered = (raw[:start] + '\n' * raw[start:finish + len(CLOSE)].count('\n')
                        + raw[finish + len(CLOSE):])
            payload = json.loads(raw[start + len(OPEN):finish], object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {'rows'}
                    or not isinstance(payload['rows'], list) or len(payload['rows']) > 32):
                raise ValueError('rows schema')
            result['binding_status'] = 'VALID'
            bindings = payload['rows']
        except (ValueError, TypeError, RecursionError):
            result['rejected_bindings'].append({'code': 'INVALID_BINDING_BLOCK'})
    for number, line in enumerate(numbered.splitlines(), 1):
        line = line.strip()
        for group_ids, use, form in _recognize(line, scope['groups']):
            row = {'group_ids': group_ids, 'use': use, 'instruction': line}
            code = _binding_code(row, scope['groups'])
            result['explicit_prose'].append({'line': number, **row, 'form': form,
                'status': 'REJECTED' if code else 'DECLARED_SCOPE_CHECKED', 'code': code})
    for index, row in enumerate(bindings):
        code = _binding_code(row, scope['groups'])
        if code is None and row['instruction'] not in prose:
            code = 'INSTRUCTION_NOT_IN_MEMO'
        if code is None:
            # The declared use/groups cannot launder the binding's own text: an
            # operational sentence labelled description, or one naming a small
            # group but declared as overall, is checked against the NAMED groups.
            for instruction_line in row['instruction'].splitlines():
                for group_ids, use, _form in _recognize(instruction_line, scope['groups']):
                    code = _binding_code({'group_ids': group_ids, 'use': use,
                                          'instruction': instruction_line}, scope['groups'])
                    if code is None and (row['use'] == 'description'
                                         or not set(group_ids).issubset(row['group_ids'])):
                        code = 'EXPLICIT_SCOPE_MISMATCH'
                    if code:
                        break
                if code:
                    break
        if code is None:
            # Reconcile only full-line prose already recognized above. A model's
            # JSON declaration cannot override the known group/use of that line.
            instruction_lines = {line.strip() for line in row['instruction'].splitlines()}
            for covered in result['explicit_prose']:
                if covered['instruction'] not in instruction_lines:
                    continue
                if covered['code']:
                    code = covered['code']
                elif (row['use'] != covered['use']
                      or not set(covered['group_ids']).issubset(row['group_ids'])):
                    code = 'EXPLICIT_SCOPE_MISMATCH'
                if code:
                    break
        if code:
            result['binding_status'] = 'INVALID'
            result['rejected_bindings'].append({'row': index, 'code': code})
        else:
            result['accepted_bindings'].append(row)
    if result['rejected_bindings'] or any(r['code'] for r in result['explicit_prose']):
        result['status'] = 'REJECTED_BINDINGS'
    elif result['binding_status'] == 'VALID' or result['explicit_prose']:
        result['status'] = 'BINDINGS_CHECKED'
    return result


def _excerpt(text, limit=120):
    # A cut excerpt never closes its guillemet: a closed one would pose as the whole sentence.
    return ('\u00ab' + text + '\u00bb' if len(text) <= limit
            else '\u00ab' + text[:limit].rstrip() + '\u2026')


def notice(result):
    state = ('binding operativo rifiutato; nessun uso autorizzato dal binding respinto'
             if result['status'] == 'REJECTED_BINDINGS' else
             'binding dichiarati controllati solo per gruppo e soglia' if result['status'] == 'BINDINGS_CHECKED'
             else 'binding assenti; uso del track record NOT_ASSESSED')
    codes = sorted({r['code'] for r in result['rejected_bindings'] + result['explicit_prose'] if r.get('code')})
    details = ''
    prose = [e for e in result['explicit_prose'] if e.get('code')]
    if prose:
        details += ('. Respinta la PROSA del memo (righe del testo del Capo): ' + '; '.join(
            'riga ' + str(e['line']) + ' ' + _excerpt(e['instruction']) + ' (' + e['code'] + ': '
            + ', '.join(e['group_ids']) + ')' for e in prose[:5])
            + ('; e altre ' + str(len(prose) - 5) if len(prose) > 5 else ''))
    if result['rejected_bindings']:
        details += ('. Respinto il BLOCCO scorecard_bindings: ' + ', '.join(
            ('riga JSON ' + str(r['row']) if 'row' in r else 'blocco intero') + ' (' + r['code'] + ')'
            for r in result['rejected_bindings']))
    return ('\n\n> [SCORECARD: ' + state + (('; cause: ' + ', '.join(codes)) if codes else '') + details
            + '. Gruppi con meno di 36 esiti non abilitano sizing, selezione, ranking, '
            'regole o lezioni operative'
            + '. Controllo limitato ai binding strutturati e alle frasi operative riconosciute '
            '(verbo d\'azione su size/peso/posizione giustificato da una citazione numerica '
            'dello scorecard del gruppo nella stessa proposizione: etichetta accanto a esiti, n o '
            'hit rate con numero); '
            'prosa restante e significato economico NOT_ASSESSED. Testo originale conservato.]')


def format_track_record_scope_for_capo(scope) -> str:
    """New-contract projection only: no refetch, ranking, truncation or instruction."""
    return ('--- SCORECARD: GRUPPI E LIMITI DI UTILIZZO [src: scorekeeper] ---\n'
            '<scorecard_scope>' + json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                              allow_nan=False) + '</scorecard_scope>')
