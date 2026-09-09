"""
BELLOMBERG - Personal AI Hedge Fund Terminal
Built for one PM (name from PM_NAME in .env)
Powered by 6 specialist Opus 4.8 agents + Capo (Event Desk = fusione News+Politics 15/07/2026)
"""

from bellomberg.core.config import PM_NAME

BRAND_NAME = "BELLOMBERG"
BRAND_TAGLINE = "Your Personal AI Hedge Fund Terminal"
BRAND_SUBTAGLINE = "6 specialists. 1 senior analyst. Your portfolio."
VERSION = "0.7.0"  # v0.7 — News Terminal Bloomberg-grade + bugfix wave (giu 2026)
CODENAME = "Genesis"  # internal codename phase 1

# Palette ufficiale Bellomberg (Citadel-meets-luxury)
BRAND_COLORS = {
    "primary_bg": "#0a0e1a",          # deep navy near-black
    "panel_bg": "#13182b",            # slightly lighter
    "accent_gold": "#d4af37",         # signature gold
    "accent_cyan": "#00d4ff",         # signature cyan
    "accent_emerald": "#00ff88",      # gains
    "accent_crimson": "#ff4757",      # losses
    "text_primary": "#e8e8f0",
    "text_muted": "#8a8a9e",
    "border": "#2a2f44",
    "highlight": "#f0d068",
}

# ASCII art logo (mostrato all'avvio del consigliere)
ASCII_LOGO = r"""
+==============================================================+
|                                                              |
|    ____  ______ __    __    ____   __  __ ____ ____ ____    |
|   |  _ \|  ____|  |  |  |  / __ \ |  \/  | __ ) ____|  _ \  |
|   | |_) | |__  | |  | |  | |  | || \  / | __ )|  _| | |_) | |
|   |  _ <|  __| | |  | |  | |  | || |\/| | __ )| |___|  _ <  |
|   | |_) | |____| |__| |__| |__| || |  | | __ )|_____| | \ \ |
|   |____/|______|______|______\__\_||_|  |_|____/      |_|  \_\
|                                                              |
|              Your Personal AI Hedge Fund Terminal            |
|                       v0.3 'Genesis'                         |
+==============================================================+
"""


def print_banner():
    """Splash banner all'avvio del consigliere."""
    print(ASCII_LOGO)


def get_email_subject_prefix():
    """Prefix per email da Bellomberg."""
    return "[BELLOMBERG] "


def get_email_signature_html():
    """Firma email HTML stile Bellomberg."""
    return """
    <div style="margin-top:24px;padding-top:12px;border-top:1px solid #2a2f44;">
        <p style="color:#d4af37;font-family:Inter,Segoe UI,sans-serif;font-size:11px;margin:0;
                  letter-spacing:1px;font-weight:600;">
            BELLOMBERG
        </p>
        <p style="color:#8a8a9e;font-size:10px;margin:2px 0 0 0;font-family:Inter,sans-serif;">
            Personal AI Hedge Fund Terminal - 6 specialists + Capo on Opus 4.8
        </p>
    </div>
    """


def get_pdf_header_footer():
    """Header/footer config per PDF generati."""
    return {
        "header_text": "BELLOMBERG",
        "header_color": BRAND_COLORS["accent_gold"],
        "footer_text": "Bellomberg Personal Terminal - Confidential to " + PM_NAME,
        "footer_color": BRAND_COLORS["text_muted"],
    }
