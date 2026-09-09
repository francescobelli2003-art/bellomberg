"""
sector_taxonomy.py - Tassonomia GRANULARE per sotto-settore (#165/#182).

Principio (Damodaran): non si valutano ne' si confrontano societa' su basi
imparagonabili. Un produttore di pannelli solari (capital-intensive, margini 40%)
e un SaaS (asset-light, margini 80%) NON condividono assumptions ne' peer set.

Ogni profilo definisce, per il SOTTO-settore (yfinance 'industry', piu' fine di 'sector'):
  - engine: operating | bank | insurance | reit | etf_passive (no DCF) | commodity_producer
  - growth/gross_margin/ebitda_margin/capex/reinvestment tipici (PRIOR, poi blendati col reale)
  - beta_unlevered (bottom-up Damodaran-style)
  - peers: ticker dei comparabili VERI di quel sotto-settore (per Comps omogenei)
  - multiple_basis: quale multiplo ha senso (EV/EBITDA, EV/Sales, P/TBV, P/AUM, EV/EBITDAX...)
  - notes: cautele di valutazione specifiche

Matching: prima per 'industry' esatto, poi per keyword, infine fallback al macro-settore.
Robusto: ritorna sempre un profilo (mai eccezioni).
"""
from typing import Dict, Any

# 05/09 (classificazione lotto 2): il contratto delle etichette e il negozio dei veicoli.
# Solo stdlib dentro: l'import non legge file ne' apre rete.
import bellomberg.storage.classificazione as cl

# Profili per SOTTO-settore. Chiave = pattern (substring, lower) sull'industry yfinance.
SUBSECTORS: Dict[str, Dict[str, Any]] = {
    # ---- SOFTWARE / TECH (distinti per economics) ----
    "software - infrastructure": {"engine":"operating","growth":0.15,"gm":0.78,"ebitda_m":0.33,
        "capex":0.03,"beta_u":1.05,"multiple_basis":"EV/Sales + EV/EBITDA",
        "dam_industry":"Software (System & Application)",
        "growth_floor":0.0,"growth_cap":0.35,
        "peers":["MSFT","ORCL","PANW","CRWD","NOW"],
        "notes":"SaaS: alta retention, Rule of 40, capex basso, valuta su EV/Sales finche' non matura."},
    "software - application": {"engine":"operating","growth":0.14,"gm":0.75,"ebitda_m":0.28,
        "capex":0.03,"beta_u":1.10,"multiple_basis":"EV/Sales + EV/EBITDA",
        "dam_industry":"Software (System & Application)",
        "growth_floor":0.0,"growth_cap":0.30,
        "peers":["CRM","ADBE","INTU","WDAY","TEAM"],
        "notes":"Application software: occhio a SBC e net retention."},
    "semiconductors": {"engine":"operating","growth":0.10,"gm":0.50,"ebitda_m":0.35,
        "capex":0.12,"beta_u":1.30,"multiple_basis":"EV/EBITDA + P/E ciclico",
        "dam_industry":"Semiconductor",
        "cyclical":True,"growth_floor":-0.10,"growth_cap":0.30,
        "peers":["NVDA","AMD","TSM","ASML","STM"],
        "notes":"Ciclico, capex elevato (fabs). Normalizza sul ciclo, non sul picco."},
    "solar": {"engine":"operating","growth":0.12,"gm":0.40,"ebitda_m":0.30,
        "capex":0.10,"beta_u":1.25,"multiple_basis":"EV/EBITDA + P/E",
        # ok PM 16/07 (audit/13 §6 n.11): ancora Damodaran = Semiconductor Equip
        # (manifattura), NON 'Green & Renewable Energy' (utility a leva, beta 0.47)
        "dam_industry":"Semiconductor Equip",
        # decisione PM 16/07 (post-collaudo V2): solar NON ciclico — il mid-cycle
        # decennale includerebbe l'era pre-IRA (business strutturalmente diverso);
        # resta manifatturiero growth con clamp di profilo. E&P resta ciclico.
        "growth_floor":-0.10,"growth_cap":0.30,
        "peers":["FSLR","ENPH","SEDG","RUN","NXT"],
        "notes":"Manifattura cleantech: capital-intensive, esposto a policy/tariffe e prezzo polisilicio. NON paragonare a SaaS. Ancora Damodaran: Semiconductor Equip (scelta PM, dichiarata)."},
    # ---- FINANZIARI (distinti: banca vs fintech vs asset mgmt vs assicurazioni) ----
    # V1.6 (audit/13, ok PM): clamp/default del beta bancario PER PROFILO (prior
    # manuali del profilo, dichiarati) — prima erano unici per tutte le banche.
    "banks - regional": {"engine":"bank","beta_u":1.15,"multiple_basis":"P/TBV + residual income",
        "dam_industry":"Banks (Regional)",
        "bank_beta_floor":0.90,"bank_beta_cap":1.60,"bank_beta_default":1.10,
        # audit/13 V2.5 (ok PM): floor/cap del ROE di partenza, cap franchise sul ROE
        # terminale (banche tradizionali +4pp su Ke; sopra SOLO con variant_view),
        # bound payout terminale. Tutti DICHIARATI nel foglio quando mordono.
        "roe_floor":0.05,"roe_cap":0.20,"franchise_cap":0.04,
        "payout_floor":0.30,"payout_cap":0.80,
        # V4 (§9-novies n.1): fade dell'excess ROE per profilo — banca matura = 5 anni
        # (la concorrenza comprime in fretta); l'analista puo' sempre sovrascrivere.
        "fade_years_default":5,
        # 17/07 §9-sexies n.1: banda di plausibilita' del P/B dei peer (misurate 25
        # banche sane 0,68-2,58; HSBC ADR 7,9 = dato rotto da rapporto ADS/ordinarie)
        "peer_pb_band":(0.15,3.5),
        "peers":["JPM","BAC","ISP.MI","UCG.MI"],
        "notes":"Banca commerciale: residual income su ROTE vs Ke, P/TBV giustificato. CET1 come vincolo capitale."},
    "banks - diversified": {"engine":"bank","beta_u":1.20,"multiple_basis":"P/TBV + residual income",
        "dam_industry":"Bank (Money Center)",
        "bank_beta_floor":0.90,"bank_beta_cap":1.50,"bank_beta_default":1.10,
        "roe_floor":0.05,"roe_cap":0.18,"franchise_cap":0.04,
        "payout_floor":0.30,"payout_cap":0.80,
        "fade_years_default":5,  # V4: banca universale matura, fade 5 anni
        "peer_pb_band":(0.15,3.5),
        # 17/07 §9-sexies n.1: HSBC (ADR) RIMOSSO dai seed — priceToBook Yahoo 7,9
        # FALSO (HSBA.L locale: 1,95); il canale banca ora sceglie comunque per
        # geografia (BANK_PEERS_BY_COUNTRY), questa lista e' solo il fallback.
        "peers":["JPM","C","BNP.PA","ISP.MI"],
        "notes":"Banca universale: somma delle parti (retail/IB/AM) se segmenti eterogenei."},
    "credit services": {"engine":"bank","beta_u":1.30,"multiple_basis":"P/E growth + P/TBV",
        # ancora dichiarata: Damodaran non ha una industry fintech/credito digitale —
        # 'Financial Svcs. (Non-bank & Insurance)' e' il proxy meno sbagliato
        "dam_industry":"Financial Svcs. (Non-bank & Insurance)",
        "bank_beta_floor":1.00,"bank_beta_cap":1.90,"bank_beta_default":1.30,
        # I compounder fintech hanno franchise cap +8pp; sopra il cap serve una
        # variant_view argomentata.
        "roe_floor":0.08,"roe_cap":0.35,"franchise_cap":0.08,
        "payout_floor":0.05,"payout_cap":0.60,
        # V4: il compounder fintech trattiene l'excess ROE piu' a lungo, coerente con
        # il franchise_cap +8pp: fade 8 anni.
        "fade_years_default":8,
        "peers":["SOFI","AFRM","V","MA"],
        "notes":"Fintech/credito digitale: growth story, ROE in salita. P/TBV insufficiente se cresce il book fast; usa anche P/E forward e FCFE."},
    "insurance": {"engine":"insurance","beta_u":0.90,"multiple_basis":"P/Book + embedded value",
        "dam_industry":"Insurance (General)",
        "bank_beta_floor":0.80,"bank_beta_cap":1.40,"bank_beta_default":1.00,
        "roe_floor":0.05,"roe_cap":0.18,"franchise_cap":0.04,
        "payout_floor":0.30,"payout_cap":0.85,
        "fade_years_default":5,  # V4: assicurazione matura, fade 5 anni
        "peers":["ALV.DE","G.MI","AXA.PA","ZURN.SW"],
        "notes":"Assicurazioni: P/Book e embedded value (vita) o combined ratio (danni). NO DCF unlevered."},
    "asset management": {"engine":"operating","growth":0.06,"gm":0.60,"ebitda_m":0.40,
        "capex":0.02,"beta_u":1.15,"multiple_basis":"P/AUM + P/E",
        "dam_industry":"Investments & Asset Management",
        "growth_floor":-0.05,"growth_cap":0.15,
        "peers":["BLK","BX","AMUN.PA"],
        "notes":"Asset manager / holding: valuta su % AUM e P/E; per le holding conta il NAV/discount."},
    # ---- DIFESA / HEALTHCARE: profili espliciti per evitare Comps vuoti ----
    "aerospace": {"engine":"operating","growth":0.08,"gm":0.21,"ebitda_m":0.13,
        "capex":0.04,"beta_u":0.95,"multiple_basis":"EV/EBITDA + P/E",
        "dam_industry":"Aerospace/Defense",
        "growth_floor":-0.05,"growth_cap":0.15,
        "peers":["LMT","RTX","NOC","GD","RHM.DE","HO.PA","BA.L"],
        "notes":"Difesa: backlog pluriennale e budget statali; margini medi, visibilita' alta. Ciclo politico > ciclo economico."},
    "drug manufacturers": {"engine":"operating","growth":0.05,"gm":0.70,"ebitda_m":0.32,
        "capex":0.05,"beta_u":0.70,"multiple_basis":"P/E + EV/EBITDA",
        "dam_industry":"Drugs (Pharmaceutical)",
        "growth_floor":-0.05,"growth_cap":0.15,
        "peers":["JNJ","MRK","PFE","NVO","AZN","SNY"],
        "notes":"Pharma: pipeline e patent cliff dominano il terminal value; difensivo, FX rilevante per gli EU."},
    "biotechnology": {"engine":"operating","growth":0.18,"gm":0.85,"ebitda_m":0.20,
        "capex":0.03,"beta_u":1.30,"multiple_basis":"EV/Sales + pipeline (rNPV)",
        "dam_industry":"Drugs (Biotechnology)",
        "growth_floor":-0.10,"growth_cap":0.35,
        "peers":["AMGN","GILD","VRTX","REGN","BIIB","ALNY"],
        "notes":"Biotech: NON e' big pharma (regola PM 16/07) — binaria sulla pipeline, spesso pre-utili; EV/Sales e rNPV, il DCF classico regge solo sui profittevoli."},
    "medical devices": {"engine":"operating","growth":0.08,"gm":0.68,"ebitda_m":0.28,
        "capex":0.04,"beta_u":0.85,"multiple_basis":"EV/EBITDA + P/E",
        "dam_industry":"Healthcare Products",
        "growth_floor":-0.05,"growth_cap":0.18,
        "peers":["MDT","ABT","SYK","BSX","EW","ZBH"],
        "notes":"Medtech: ricavi ricorrenti da procedure, R&D continua; multipli premium giustificati dalla durabilita'."},
    "healthcare plans": {"engine":"operating","growth":0.07,"gm":0.18,"ebitda_m":0.06,
        "capex":0.01,"beta_u":0.75,"multiple_basis":"P/E + MCR",
        "dam_industry":"Healthcare Support Services",
        "growth_floor":-0.03,"growth_cap":0.15,
        "peers":["UNH","ELV","CI","HUM","CNC","MOH"],
        "notes":"Assicurazione sanitaria: il driver e' il Medical Cost Ratio, non la crescita ricavi; margini sottili, P/E il multiplo giusto."},
    # ---- ENERGY (upstream vs services vs midstream) ----
    "oil & gas equipment & services": {"engine":"operating","growth":0.05,"gm":0.25,"ebitda_m":0.18,
        "capex":0.06,"beta_u":1.20,"multiple_basis":"EV/EBITDA",
        "dam_industry":"Oilfield Svcs/Equip.",
        "cyclical":True,"growth_floor":-0.08,"growth_cap":0.15,
        "peers":["SLB","HAL","BKR","SUBC.OL"],
        "notes":"Servizi oil: ciclico su capex E&P. NON paragonare a E&P puri."},
    "oil & gas e&p": {"engine":"operating","growth":0.03,"gm":0.45,"ebitda_m":0.55,
        "capex":0.25,"beta_u":1.15,"multiple_basis":"EV/EBITDAX + NAV",
        "dam_industry":"Oil/Gas (Production and Exploration)",
        "cyclical":True,"growth_floor":-0.08,"growth_cap":0.12,
        "peers":["XOM","CVX","ENI.MI","SHEL"],
        "notes":"E&P: EV/EBITDAX e NAV su riserve. Capex enorme."},
    # ---- MATERIALS / MINERS ----
    "steel": {"engine":"operating","growth":0.03,"gm":0.15,"ebitda_m":0.12,"capex":0.06,
        "beta_u":1.30,"multiple_basis":"EV/EBITDA ciclico",
        "dam_industry":"Steel",
        "cyclical":True,"growth_floor":-0.08,"growth_cap":0.15,
        "peers":["X","NUE","TKA.DE"],
        "notes":"Acciaio: ciclico, margini sottili, normalizza sul ciclo."},
    # ---- HARDWARE: profilo esplicito per evitare il default senza peer ----
    "computer hardware": {"engine":"operating","growth":0.10,"gm":0.28,"ebitda_m":0.12,
        "capex":0.03,"beta_u":1.10,"multiple_basis":"EV/EBITDA + P/E",
        "dam_industry":"Computers/Peripherals",
        "growth_floor":0.0,"growth_cap":0.30,
        "peers":["LOGI","HPQ","DELL","CRSR"],
        "notes":"Hardware consumer/embedded: margini sottili da manifattura, growth da "
                "adozione; il fit dei seed va verificato sul caso analizzato."},
    # ---- UTILITIES (V7 Lotto 1, design audit/15 ok PM 21/07: G1+G2 audit/14) ----
    "utilities - regulated": {"engine":"rab","beta_u":0.40,
        # beta_u = prior dai beta asset ARERA 2025-2027 (0,370-0,410 per T&D
        # elettrico/gas, delibera 513/2024/R/COM All. A) — fonte vera, non a occhio
        "multiple_basis":"EV/RAB premium + DDM regolato",
        "dam_industry":"Utility (General)",
        # banda di plausibilita' del premio EV/RAB (reti europee quotate ~1,0-1,4x;
        # fuori banda = crescita RAB straordinaria o dato rotto: si DICHIARA)
        "ev_rab_band":(0.7, 1.6),"rab_premium_default":1.15,
        "peer_pe_band":(5.0, 30.0),   # plausibilita' P/E peer (fuori banda = fuori mediana, riga dichiarata)
        "payout_floor":0.50,"payout_cap":0.95,  # le reti sono dividend stock
        "net_beta_floor":0.30,"net_beta_cap":1.00,"net_beta_default":0.65,
        "fade_years_default":5,
        "peers":["TRN.MI","SRG.MI","IG.MI","NG.L","REE.MC","ENG.MC"],
        "notes":"Rete regolata pura (Terna/Snam/Italgas/NG): la revenue e' la FORMULA del regolatore (WACC ammesso x RAB + pass-through), non un CAGR. Si valuta col motore RAB (engine 'rab'): EV/RAB premium + DDM regolato; il DCF operating qui e' strutturalmente sbagliato."},
    "utilities - diversified": {"engine":"operating","growth":0.04,"gm":0.30,"ebitda_m":0.22,
        "capex":0.10,"beta_u":0.50,"multiple_basis":"EV/EBITDA + SOTP per segmenti",
        "dam_industry":"Utility (General)",
        "growth_floor":-0.05,"growth_cap":0.12,
        "peers":["ENEL.MI","IBE.MC","SSE.L","ED","NEE"],
        "notes":"Utility integrata (reti+generazione+retail): consolidato single-line = headline; con i segments dell'analista uscira' il SOTP affiancato col delta dichiarato (V7 Lotto 3). Reti in pancia -> beta basso, leva alta."},
    "utilities - renewable": {"engine":"operating","growth":0.08,"gm":0.45,"ebitda_m":0.35,
        "capex":0.18,"beta_u":0.60,"multiple_basis":"EV/EBITDA + EV/MW",
        "dam_industry":"Green & Renewable Energy",
        "growth_floor":-0.05,"growth_cap":0.20,
        "peers":["ORSTED.CO","EDPR.LS","ERG.MI","NEP"],
        "notes":"Sviluppatore rinnovabili: il sanity vero e' per unita' fisica (capex/MW, load factor, IRR incrementale vs WACC — hook G9, fuori V7 Lotto 1). Contratti (merchant/CfD/PPA) decidono la qualita' dei ricavi."},
    # NB: 'Utilities - Independent Power Producers' (merchant) NON mappata di
    # proposito: economics ciclica da generatore, ne' rete ne' developer -> default
    # DICHIARATO finche' un nome vero non impone un profilo suo.
    # ---- ETF / PANIERI (NON si valutano con DCF) ----
    "etf": {"engine":"etf_passive","multiple_basis":"NAV / esposizione fattoriale",
        "peers":[],
        "notes":"ETF/paniere: NON valutabile con DCF. Si analizza come ESPOSIZIONE (fattori, tema, geografia, holdings sottostanti, costo TER), non come societa'."},
    # ---- VEICOLI A NAV (05/09, classificazione lotto 2): fondo chiuso, tesoreria
    # digitale, holding — lo dichiara il negozio dei veicoli. Nessun numero operativo
    # (growth/gm/beta_u a None: un DCF su questo profilo non puo' partire in silenzio),
    # engine mnav = NAV e sconto/premio sul NAV. Ieri il fondo chiuso con quoteType
    # EQUITY usciva «asset management», engine operating.
    "veicolo_nav": {"engine":"mnav","growth":None,"gm":None,"ebitda_m":None,"capex":None,
        "beta_u":None,"multiple_basis":"NAV / mNAV","peers":[],
        "notes":"Veicolo a NAV (fondo chiuso, tesoreria digitale, holding): NON valutabile con DCF. Si valuta a NAV e sconto/premio sul NAV (motore mnav) o come esposizione, mai come societa' operativa."},
}

# Mappa keyword -> chiave profilo (per industry non esatti)
KEYWORDS = [
    # I quattro sub-settori aggiunti matchavano solo per chiave ESATTA: per esempio
    # "Aerospace & Defense" cadeva nel default
    # senza peer. Le keyword vanno PRIMA delle generiche (ordine = precedenza).
    (["aerospace","defense","difesa"], "aerospace"),
    (["biotech"], "biotechnology"),
    (["drug manufacturer","pharmaceutical"], "drug manufacturers"),
    (["medical device","medical instrument","medical equipment"], "medical devices"),
    (["healthcare plan","managed care","health insurance"], "healthcare plans"),
    (["solar","photovoltaic"], "solar"),
    (["semiconductor","chip"], "semiconductors"),
    (["software - infra","infrastructure software"], "software - infrastructure"),
    (["software","saas","application"], "software - application"),
    (["bank"], "banks - regional"),
    (["credit services","fintech","payment"], "credit services"),
    (["insurance","assicur"], "insurance"),
    (["asset management","capital markets","holding"], "asset management"),
    (["oil & gas equipment","oilfield","drilling","subsea"], "oil & gas equipment & services"),
    (["oil & gas e&p","exploration","upstream"], "oil & gas e&p"),
    (["steel","iron"], "steel"),
    # V7: le industry Yahoo sono "Utilities - Regulated Electric/Gas/Water" ->
    # il match esatto non scatta, servono le keyword (stessa lezione aerospace 16/07)
    (["utilities - regulated","regulated electric","regulated gas","regulated water"], "utilities - regulated"),
    (["utilities - diversified"], "utilities - diversified"),
    (["utilities - renewable"], "utilities - renewable"),
    (["etf","exchange traded","fund","index"], "etf"),
]

DEFAULT_PROFILE = {"engine":"operating","growth":0.06,"gm":0.40,"ebitda_m":0.18,
    "capex":0.04,"beta_u":1.00,"multiple_basis":"EV/EBITDA","peers":[],
    "growth_floor":-0.10,"growth_cap":0.25,
    "notes":"Profilo generico (sotto-settore non mappato): assumptions prudenti, verificare peer set a mano."}

# 05/09 (classificazione lotto 2): il ramo «nessun dato» NON e' piu' il profilo operativo
# generico. Senza industry, sector ne' quoteType nessuna fonte dice che strumento sia:
# engine 'sconosciuto' e numeri a None, cosi' nessun motore li usa senza accorgersene
# (dcf_engine lo rifiuta PRIMA delle cinture). Diverso dal ramo 8a (industry presente ma
# non mappata), che resta DEFAULT_PROFILE con l'etichetta che dichiara il buco.
PROFILO_SCONOSCIUTO = {**DEFAULT_PROFILE, "engine":"sconosciuto", "growth":None, "gm":None,
    "ebitda_m":None, "capex":None, "beta_u":None,
    "notes":"Profilo SCONOSCIUTO: industry, sector e quoteType vuoti, nessuna fonte dice che strumento sia. Non e' un ripiego: e' un buco dichiarato."}

# la NATURA (dal negozio dei veicoli) decide il profilo SOLO per i veicoli: paniere -> profilo
# etf, veicolo a NAV -> veicolo_nav. bank/operating/sconosciuto NON decidono (cade alla
# tassonomia) ma viaggiano comunque in `_natura`.
NATURE_PANIERE = frozenset({"etf", "etn", "commodity", "crypto"})
NATURE_NAV = frozenset({"cef", "dat", "holding"})

# Compatibilita' per eventuali import legacy: deve restare vuoto. Gli override di
# profilo provengono esclusivamente dal campo `profilo_valutazione` del negozio.
TICKER_PROFILE_OVERRIDES = {}

# Una misura del P/B ha mostrato che gli ADR possono incorporare il rapporto con
# l'ordinaria e diventare comparabili distorti:
# i comparabili di una banca COMMERCIALE sono le banche della SUA geografia (stesso
# regolatore, stessa curva tassi, stesso costo del funding), non le megacap globali
# della vecchia lista unica di profilo. Selezione per 'country' Yahoo del ticker.
# Ticker PROBATI col foglio di misura 17/07 (P/B campo Yahoo 0,68-2,58, tutti sani);
# per la UK SOLO i .L locali: il campo priceToBook e' giusto sui .L e rotto sugli
# ADR (HSBC 7,9 = rapporto ADS/ordinarie 5:1 dentro il dato).
BANK_PEERS_BY_COUNTRY = {
    "japan": (["SMFG", "MFG"], "megabanche giapponesi (SMFG, Mizuho — ADR)"),
    "italy": (["ISP.MI", "UCG.MI", "BAMI.MI", "BPE.MI", "SAN.MC", "BNP.PA"], "banche IT/EU"),
    "united states": (["JPM", "BAC", "C", "WFC", "USB", "PNC"], "banche USA"),
    "united kingdom": (["HSBA.L", "BARC.L", "LLOY.L", "NWG.L"], "banche UK (.L locali, MAI ADR)"),
    "france": (["BNP.PA", "GLE.PA", "ACA.PA", "SAN.MC", "ISP.MI", "DBK.DE"], "banche FR/EU"),
    "germany": (["DBK.DE", "CBK.DE", "BNP.PA", "ISP.MI", "UCG.MI", "SAN.MC"], "banche DE/EU"),
    "spain": (["SAN.MC", "BBVA.MC", "CABK.MC", "ISP.MI", "UCG.MI", "BNP.PA"], "banche ES/EU"),
}
# Solo banche COMMERCIALI: fintech e assicurazioni mantengono peer del proprio
# business model.
BANK_GEO_PROFILES = {"banks - regional", "banks - diversified"}


def bank_peers_for(country, profile_key, fallback_peers):
    """(lista, nota) — peer bancari per GEOGRAFIA del ticker (17/07 §9-sexies n.1).
    Country fuori mappa o profilo non commerciale: fallback DICHIARATO alla lista
    seed del profilo (mai un buco silenzioso, regola 14/07)."""
    c = (country or "").lower().strip()
    if profile_key in BANK_GEO_PROFILES and c in BANK_PEERS_BY_COUNTRY:
        lst, label = BANK_PEERS_BY_COUNTRY[c]
        return list(lst), "peer per GEOGRAFIA: %s [country Yahoo: %s]" % (label, country)
    return list(fallback_peers or []), (
        "lista seed del profilo %s (country '%s' fuori mappa geo o profilo non "
        "banca commerciale): fit da verificare a mano" % (profile_key, country or "n.d."))


def _con_avvisi(evidenza: str, avvisi) -> str:
    """Un avviso raccolto lungo la strada (es. profilo dichiarato inesistente) viaggia
    nell'evidenza dell'etichetta che vince: mai perso in silenzio."""
    return evidenza + ((" | " + "; ".join(avvisi)) if avvisi else "")


def classify(industry: str, sector: str = "", ticker: str = "", quote_type: str = "",
             negozio=None) -> Dict[str, Any]:
    """Ritorna il profilo di valutazione del SOTTO-settore. Sempre un dict valido.

    05/09 (classificazione lotto 2): ogni return porta `_etichetta` (dominio
    profilo_valutazione: COME si valuta, con fonte ed evidenza) e `_natura` (COSA e', da
    classificazione.natura_risolta: il negozio dei veicoli vince sul dato Yahoo). `_matched`
    e `_profile_key` NON cambiano forma per i rami di ieri (tools/migrations/migra_profile_key.py li
    parsa in SQL). Ordine: (1) quoteType da paniere; (2) natura etf/etn/commodity/crypto
    dal negozio -> profilo etf; (3) natura cef/dat/holding -> veicolo_nav; (4)
    profilo_valutazione dichiarato nel negozio; (5) industry esatta; (6) keyword (si
    dichiara quale e su quale campo); (7a) industry/sector presenti ma non mappati ->
    DEFAULT_PROFILE con etichetta SCONOSCIUTA; (7b) nessun dato ->
    PROFILO_SCONOSCIUTO. `negozio=None` rilegge il negozio corrente a ogni chiamata.
    """
    ind = (industry or "").lower().strip()
    sec = (sector or "").lower().strip()
    tk = (ticker or "").upper().strip()
    qt = (quote_type or "").upper().strip()
    n = negozio if negozio is not None else cl.carica_veicoli()
    natura = cl.natura_risolta(tk, quote_type=qt, industry=industry or "", sector=sector or "",
                               negozio=n)
    v = cl.voce(tk, n)
    avvisi = []
    if n["origine"] == "assente":
        # clone senza negozio: la natura viene dal solo dato Yahoo; l'etichetta del profilo lo
        # dice, cosi' profile_source nel payload porta l'assenza (review 05/09)
        avvisi.append("negozio dei veicoli ASSENTE: la natura viene dal solo dato Yahoo, un "
                      "veicolo non dichiarato puo' finire al DCF")

    def _esito(profilo, matched, key, etichetta):
        # audit/13 V1.7a: oltre a _matched (traccia legacy in forme miste) si ritorna
        # SEMPRE anche _profile_key = chiave PULITA di SUBSECTORS ('default' se nessun
        # match): e' la chiave stabile per profili WACC/growth e per il DB.
        return {**profilo, "_matched": matched, "_profile_key": key, "industry": industry,
                "_etichetta": etichetta, "_natura": natura}

    # (1) fix 13/07: quoteType yfinance = la via AFFIDABILE per i panieri. Gli ETF hanno
    # spesso industry/sector VUOTI: senza questo check cadevano nel fallback
    # 'default' (operating) -> DCF nonsense su ETF/fondi (feedback PM 13/07).
    if qt in cl.QUOTE_TYPES_PANIERE:
        return _esito(SUBSECTORS["etf"], f"quoteType={qt}", "etf",
                      cl.etichetta("profilo_valutazione", "etf", "quote_type",
                                   _con_avvisi("quoteType=%s" % qt, avvisi)))
    # (2) il negozio dei veicoli dichiara un paniere (etf/etn/commodity/crypto): stessa
    # forma di `_matched` del vecchio set inline di ticker del PM, che il negozio sostituisce
    # (seconda linea di difesa: Yahoo risponde 404 sul quoteSummary di certi ETN).
    if natura.valore in NATURE_PANIERE:
        return _esito(SUBSECTORS["etf"], "etf (override ticker)", "etf",
                      cl.etichetta("profilo_valutazione", "etf", natura.fonte,
                                   "voce %s del negozio dei veicoli: tipo %s" % (tk, natura.valore),
                                   verificato_il=natura.verificato_il))
    # (3) veicolo a NAV (fondo chiuso, tesoreria digitale, holding): MAI il profilo
    # operativo, anche se Yahoo dice EQUITY + 'Asset Management'.
    if natura.valore in NATURE_NAV:
        return _esito(SUBSECTORS["veicolo_nav"], "veicolo (negozio)", "veicolo_nav",
                      cl.etichetta("profilo_valutazione", "veicolo_nav", natura.fonte,
                                   "voce %s del negozio dei veicoli: tipo %s" % (tk, natura.valore),
                                   verificato_il=natura.verificato_il))
    # (4) profilo dichiarato nella voce del negozio (stessa forma di _matched
    # degli override per ticker). Chiave inesistente = IGNORATA e dichiarata nell'etichetta.
    if v is not None and v["profilo_valutazione"]:
        _k = v["profilo_valutazione"]
        if _k in SUBSECTORS:
            return _esito(SUBSECTORS[_k], f"override ticker->{_k}", _k,
                          cl.etichetta("profilo_valutazione", _k, "registro_pm",
                                       "voce %s del negozio dei veicoli: profilo_valutazione "
                                       "dichiarato %r" % (tk, _k), verificato_il=v["verificato_il"]))
        avvisi.append("profilo_valutazione %r della voce %s del negozio NON esiste nella "
                      "tassonomia: IGNORATO (dichiarato), decide la tassonomia" % (_k, tk))
    # (5) match esatto industry
    if ind in SUBSECTORS:
        return _esito(SUBSECTORS[ind], ind, ind,
                      cl.etichetta("profilo_valutazione", ind, "tassonomia_esatta",
                                   _con_avvisi("industry Yahoo %r = chiave esatta della "
                                               "tassonomia" % industry, avvisi)))
    # (6) match keyword (ordine = precedenza): si dichiara QUALE keyword e su QUALE campo
    for kws, key in KEYWORDS:
        campo = kw = None
        for k in kws:
            if k in ind:
                campo, kw = "INDUSTRY", k
                break
        if campo is None:
            for k in kws:
                if k in sec:
                    campo, kw = "SECTOR", k
                    break
        if campo is not None:
            return _esito(SUBSECTORS[key], f"keyword->{key}", key,
                          cl.etichetta("profilo_valutazione", key, "tassonomia_keyword",
                                       _con_avvisi("keyword %r su %s %r" % (
                                           kw, campo, industry if campo == "INDUSTRY" else sector),
                                           avvisi)))
    # (7a) industry o sector presenti ma NON mappati: profilo generico INVARIATO (engine
    # operating, prior prudenti: le operative vere con industry fuori dalle chiavi restano
    # valutabili) ma l'etichetta dice che il profilo e' SCONOSCIUTO, non dedotto.
    # L'evidenza porta la parola SCONOSCIUTO perche' cio' che il modello legge e'
    # str(etichetta) = «fonte — evidenza» (profile_source), non la dichiarazione.
    if ind or sec:
        return _esito(DEFAULT_PROFILE, "default", "default",
                      cl.sconosciuto("profilo_valutazione",
                                     _con_avvisi("profilo SCONOSCIUTO: industry %r / sector %r "
                                                 "non mappati dalla tassonomia, profilo generico "
                                                 "applicato" % (industry or "", sector or ""),
                                                 avvisi)))
    # (7b) nessun dato: nessuna fonte dice che strumento sia. Ieri era il profilo operativo
    # generico (beta 1,0 e growth 6% su un veicolo); ora engine sconosciuto e numeri a None.
    # L'evidenza dice il vero sul quoteType (review 05/09: diceva «vuoto» anche con EQUITY).
    return _esito(PROFILO_SCONOSCIUTO, "default", "default",
                  cl.sconosciuto("profilo_valutazione",
                                 _con_avvisi("profilo SCONOSCIUTO: industry e sector vuoti, "
                                             "quoteType %s%s"
                                             % ("%r (dice solo che quota, non cosa sia)" % qt
                                                if qt else "vuoto",
                                                " per %s" % tk if tk else ""), avvisi)))


if __name__ == "__main__":
    tests = [
        ("Solar","Technology","SOLAR.X"),
        ("Software - Application","Technology","SOFTWARE.X"),
        ("Semiconductors","Technology","CHIP.X"),
        ("Banks - Regional","Financial Services","BANK.X"),
        ("Credit Services","Financial Services","CREDIT.X"),
        ("Oil & Gas Equipment & Services","Energy","OIL.X"),
        ("Steel","Basic Materials","STEEL.X"),
        ("","","UNKNOWN.X"),
    ]
    for ind,sec,tk in tests:
        p = classify(ind, sec, tk)
        print(f"{tk:10s} [{ind or 'ETF'}] -> engine={p['engine']:16s} match={p['_matched']:24s} mult={p.get('multiple_basis','')}")
        print(f"            peers: {p.get('peers',[])}")
