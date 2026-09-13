"""
Modulo per inviare il briefing via email Gmail.

Usa smtplib (libreria standard Python) + Gmail SMTP server.
Richiede una Gmail App Password (NON la password normale dell'account).
Vedi SETUP_SCHEDULAZIONE.md per come crearne una.
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from bellomberg.reporting.i18n import label as _t, localized

from bellomberg.core.config import EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO


# Gmail SMTP settings (standard, non cambiare)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465  # SSL port


def email_configurata():
    """Verifica se le credenziali email sono nel .env."""
    return bool(EMAIL_FROM and EMAIL_PASSWORD and EMAIL_TO)


def _markdown_to_html(testo_md):
    """
    Conversione semplice markdown -> HTML per email.
    Non e' un parser completo, gestisce solo i pattern che usiamo nel briefing:
    # heading, ## subheading, **bold**, paragrafi.
    """
    righe = testo_md.split("\n")
    html_parti = []
    in_paragrafo = False

    for riga in righe:
        riga = riga.rstrip()
        if not riga:
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            continue

        # Heading
        if riga.startswith("# "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h1>" + riga[2:] + "</h1>")
        elif riga.startswith("## "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h2>" + riga[3:] + "</h2>")
        elif riga.startswith("### "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h3>" + riga[4:] + "</h3>")
        else:
            # Trasforma **testo** in <strong>
            import re
            riga = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', riga)
            if not in_paragrafo:
                html_parti.append("<p>")
                in_paragrafo = True
            html_parti.append(riga + " ")

    if in_paragrafo:
        html_parti.append("</p>")

    # Wrap con stile email-friendly
    css = """
    <style>
        body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
               max-width: 720px; margin: 20px auto; padding: 20px; color: #1a1a1a;
               line-height: 1.6; }
        h1 { color: #0a3d62; border-bottom: 2px solid #0a3d62; padding-bottom: 8px; }
        h2 { color: #0a3d62; margin-top: 28px; border-bottom: 1px solid #ccc;
             padding-bottom: 4px; }
        h3 { color: #444; margin-top: 20px; }
        p { margin: 12px 0; }
        strong { color: #0a3d62; }
    </style>
    """
    body = "\n".join(html_parti)
    return "<html><head>" + css + "</head><body>" + body + "</body></html>"


@localized
def invia_briefing_email(testo_briefing, oggetto=None):
    """
    Invia il briefing markdown come email HTML al destinatario configurato.
    Restituisce True se OK, False se errore.
    """
    if not email_configurata():
        print("  [!] Credenziali email non configurate, skip invio.")
        print("      Configura EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO nel .env")
        return False

    if not oggetto:
        oggetto = _t("Daily Portfolio Briefing - ") + datetime.now().strftime("%d/%m/%Y")

    # Costruisci messaggio multipart (HTML + fallback plain text)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Parte plain text (fallback per client che non leggono HTML)
    parte_text = MIMEText(testo_briefing, "plain", "utf-8")
    parte_html = MIMEText(_markdown_to_html(testo_briefing), "html", "utf-8")
    msg.attach(parte_text)
    msg.attach(parte_html)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"  [OK] Email inviata a {EMAIL_TO}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  [!] Autenticazione Gmail fallita.")
        print("      Verifica EMAIL_PASSWORD: deve essere una APP PASSWORD, non la password normale.")
        print("      Vedi SETUP_SCHEDULAZIONE.md per istruzioni.")
        return False
    except Exception as e:
        print(f"  [!] Errore invio email: {e}")
        return False


@localized
def invia_briefing_email_con_allegato(testo_briefing, oggetto=None, allegato_path=None):
    """Come invia_briefing_email ma con file allegato (PDF report)."""
    import os as _os
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        print("  [!] Credenziali email non configurate, skip invio.")
        return False

    if not oggetto:
        oggetto = _t("Daily Portfolio Briefing - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Corpo (alternative: plain + html)
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(testo_briefing, "plain", "utf-8"))
    body.attach(MIMEText(_markdown_to_html(testo_briefing), "html", "utf-8"))
    msg.attach(body)

    # Allegato PDF
    attach_ok = False
    if allegato_path and _os.path.exists(allegato_path):
        try:
            with open(allegato_path, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = _os.path.basename(allegato_path)
            part.add_header("Content-Disposition", "attachment; filename=" + filename)
            msg.attach(part)
            attach_ok = True
            print("  [Allegato] " + filename + " (" + str(_os.path.getsize(allegato_path)) + " bytes)")
        except Exception as e:
            print("  [!] Errore allegando PDF: " + str(e))
    elif allegato_path:
        print("  [!] Allegato non trovato: " + str(allegato_path))
    if allegato_path and not attach_ok:
        # audit/11 §5: prima l'email partiva SENZA report e la run risultava consegnata
        # — ora il PM lo vede dall'oggetto
        try:
            msg.replace_header("Subject", (msg["Subject"] or "") + _t(" [ALLEGATO MANCANTE]"))
        except Exception:
            pass

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print("  [OK] Email con allegato inviata a " + EMAIL_TO)
        return True
    except smtplib.SMTPAuthenticationError:
        print("  [!] Autenticazione Gmail fallita.")
        return False
    except Exception as e:
        print("  [!] Errore invio email: " + str(e))
        return False


@localized
def invia_pdf_only_email(pdf_path, oggetto=None):
    """Invia email con SOLO il PDF allegato. Corpo minimale, niente markdown."""
    import os as _os
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        return False
    if not pdf_path or not _os.path.exists(pdf_path):
        print("  [!] PDF non esiste: " + str(pdf_path))
        return False

    if not oggetto:
        oggetto = _t("Weekly Research Note - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Corpo minimale (1 riga, in caso il client mostri qualcosa)
    body_html = '<html><body><p>' + _t("email.pdf_attached") + '</p><p>' + _t("email.committee") + '</p></body></html>'
    msg.attach(MIMEText(_t("email.pdf_attached"), "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # PDF allegato
    try:
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = _os.path.basename(pdf_path)
        part.add_header("Content-Disposition", "attachment; filename=" + filename)
        msg.attach(part)
    except Exception as e:
        print("  [!] PDF attach error: " + str(e))
        return False

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print("  [!] SMTP error: " + str(e))
        return False


_CHIAVI_FV = ("fair_value_final", "fair_value_weighted", "fair_value_blend",
              "fair_value_base", "fair_value_nav")


@localized
def corpo_valutazioni(valuation_results, allegati):
    """Blocco HTML per il corpo dell'email: l'esito di OGNI valutazione chiesta dal comitato
    e i modelli Excel davvero allegati.

    Audit run 10/09 (Fable 5.1, memo #54): era stato chiesto un fair value, il desk ha
    chiamato get_valuation, il motore ha risposto FV n.d. (11 campi documentati mancanti) e
    l'email e' partita con due PDF e un piede fisso che prometteva "DCF Excel models for
    GREEN-validated candidates". Chi legge l'email non poteva distinguere un modello mai
    generato da uno perso per strada. Qui si dichiara: niente Excel = detto; FV n.d. = detto
    col motivo e i campi mancanti; nessuna valutazione chiesta = detto."""
    import os as _os
    import html as _html
    xlsx = [p for p in (allegati or []) if str(p).lower().endswith((".xlsx", ".xls"))]
    righe = []
    if xlsx:
        righe.append(_t("email.models", count=len(xlsx),
                        files=", ".join(_html.escape(_os.path.basename(str(p))) for p in xlsx)))
    else:
        righe.append(_t("<p><b>Nessun modello Excel allegato.</b></p>"))
    if not valuation_results:
        righe.append(_t("<p>Nessuna valutazione (get_valuation) richiesta dai desk in questa run.</p>"))
        return "\n".join(righe)
    righe.append(_t("<p><b>Valutazioni richieste dal comitato:</b></p>\n<ul>"))
    for tk, res in sorted((valuation_results or {}).items()):
        res = res if isinstance(res, dict) else {}
        us = res.get("valuation_usability") if isinstance(res.get("valuation_usability"), dict) else {}
        fv = next((res[k] for k in _CHIAVI_FV if res.get(k) is not None), None)
        if us.get("usable") and fv is not None:
            testo = "FV %s %s" % (fv, res.get("currency") or "")
        else:
            sanity = res.get("sanity") if isinstance(res.get("sanity"), dict) else {}
            motivo = sanity.get("headline") or res.get("error") or _t("motivo n.d.")
            mancanti = [m for m in (us.get("missing_fields") or []) if m != "fair_value"]
            testo = _t("FV n.d. - %s") % str(motivo)[:160]
            if mancanti:
                testo += _t("; campi mancanti (%d): %s") % (
                    len(mancanti), ", ".join(str(m) for m in mancanti[:12]))
        snap = res.get("snapshot_id")
        righe.append("<li><b>%s</b>: %s%s</li>" % (
            _html.escape(str(tk)), _html.escape(testo.strip()),
            (" [snapshot %s]" % _html.escape(str(snap)[:12])) if snap else ""))
    righe.append("</ul>")
    return "\n".join(righe)


@localized
def invia_email_multi_allegati(pdf_paths, oggetto=None, body_extra=""):
    """Invia email con N allegati (PDF memo + PDF appendice + N Excel DCF).

    pdf_paths: list of file paths (PDF + XLSX mixed). I file vengono inferiti via estensione.
    body_extra: HTML gia' pronto (es. corpo_valutazioni) inserito nel corpo.
    """
    import os as _os
    import re as _re
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        return False
    pdf_paths = [p for p in pdf_paths if p and _os.path.exists(p)]
    if not pdf_paths:
        print("  [!] Nessun allegato valido")
        return False

    if not oggetto:
        oggetto = _t("Weekly Research Note - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Lista allegati nel body
    n_files = len(pdf_paths)
    n_xlsx = sum(1 for p in pdf_paths if str(p).lower().endswith((".xlsx", ".xls")))
    files_list_html = "<ul>" + "".join(
        "<li><b>" + _os.path.basename(p) + "</b></li>" for p in pdf_paths
    ) + "</ul>"
    # Audit 11/09 (Fable 5.1): il piede nominava un modello cablato ("Capo on Opus 4.8",
    # superato dal .env dal 05/09) e prometteva modelli Excel anche con zero .xlsx allegati.
    piede = _t("email.committee") + "<br>"
    if n_xlsx:
        piede += _t("email.excel_footer", count=n_xlsx)
    else:
        piede += _t("email.no_excel_footer")
    body_html = """<html><body style="font-family: -apple-system, Segoe UI, sans-serif; color: #333; max-width: 700px;">
<h2 style="color: #1a1a2e;">{title}</h2>
<p>{attached}:</p>
{files_list}
{extra}
<hr>
<p style="color:#888;font-size:12px;">{piede}</p>
</body></html>""".format(
        title=_t("Weekly Research Note"), attached=_t("email.attached_count", count=n_files),
        files_list=files_list_html,
        extra=body_extra or "",
        piede=piede,
    )
    testo_extra = _re.sub(r"<[^>]+>", " ", body_extra or "")
    testo_extra = _re.sub(r"[ \t]+", " ", testo_extra).strip()
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(_t("Weekly Research Note - ") + _t("email.attached_count", count=n_files) + "."
                         + ("\n\n" + testo_extra if testo_extra else ""), "plain", "utf-8"))
    body.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(body)

    # Allegati
    for path in pdf_paths:
        try:
            ext = _os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                part = MIMEBase("application", "pdf")
            elif ext in (".xlsx", ".xls"):
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                part = MIMEBase("application", "octet-stream")
            with open(path, "rb") as f:
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = _os.path.basename(path)
            part.add_header("Content-Disposition", "attachment; filename=" + filename)
            msg.attach(part)
            print("  [Attach] " + filename + " (" + str(_os.path.getsize(path)) + " bytes)")
        except Exception as e:
            print("  [!] Attach fail " + path + ": " + str(e))
            print("  [!] Email NON inviata: il pacchetto allegati sarebbe incompleto")
            return False

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        print("  [OK] Email con " + str(n_files) + " allegati inviata a " + EMAIL_TO)
        return True
    except Exception as e:
        print("  [!] SMTP error: " + str(e))
        return False
