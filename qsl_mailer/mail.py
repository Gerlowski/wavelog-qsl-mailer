"""The e-mail: HTML with the cards inline, a plain-text alternative, and SMTP."""

import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from . import config as conf
from .callbook import lookup_variants
from .card import format_freq, format_grid
from .wavelog import MAIL_OPTIONS

# Language picked from the contacted station's prefix with language = auto
PREFIX_LANG = {
    'pl': ('SP', 'SQ', 'SO', 'SN', 'SR', '3Z', 'HF'),
    'de': ('DA', 'DB', 'DC', 'DD', 'DF', 'DG', 'DH', 'DJ', 'DK', 'DL', 'DM', 'DN', 'DO', 'DP', 'DQ', 'DR',
           'OE', 'HB9', 'HB3', 'HB0'),
}

TEXT = {
    'en': {
        'subject': '{mycall} – QSL card for our QSO, {callsign}',
        'hello': 'Hello {who},',
        'thanks': 'Thank you for the QSO! Here is my QSL card confirming our contact.',
        'thanks_many': 'Thank you for the QSOs! Here are my QSL cards confirming our contacts.',
        'from': 'Worked from',
        'cols': ('DATE', 'BAND', 'MODE', 'RST', 'MHz'),
        'optout': 'Prefer not to receive QSL cards by e-mail? Just reply to this message and I will stop.',
    },
    'pl': {
        'subject': '{mycall} – karta QSL za QSO z {callsign}',
        'hello': 'Cześć {who},',
        'thanks': 'Dziękuję za łączność! Poniżej moja karta QSL potwierdzająca QSO.',
        'thanks_many': 'Dziękuję za łączności! Poniżej moje karty QSL potwierdzające QSO.',
        'from': 'Nadawane z',
        'cols': ('DATA', 'PASMO', 'EMISJA', 'RST', 'MHz'),
        'optout': 'Nie chcesz dostawać kart QSL e-mailem? Odpisz na tę wiadomość — nie będę ich wysyłać.',
    },
    'de': {
        'subject': '{mycall} – QSL-Karte für unser QSO, {callsign}',
        'hello': 'Hallo {who},',
        'thanks': 'Vielen Dank für das QSO! Hier ist meine QSL-Karte als Bestätigung unserer Verbindung.',
        'thanks_many': 'Vielen Dank für die QSOs! Hier sind meine QSL-Karten als Bestätigung unserer Verbindungen.',
        'from': 'Gearbeitet von',
        'cols': ('DATUM', 'BAND', 'MODE', 'RST', 'MHz'),
        'optout': 'Keine QSL-Karten per E-Mail gewünscht? Einfach auf diese Nachricht antworten.',
    },
}


def language_for(cfg, callsign):
    language = conf.get(cfg, 'mail', 'language', 'auto').lower()
    if language in TEXT:
        return language
    base = lookup_variants(callsign)[-1]
    for lang, prefixes in PREFIX_LANG.items():
        if base.startswith(prefixes):
            return lang
    return 'en'


def qso_line(q):
    mode = q.get('submode') or q.get('mode') or ''
    return (f"  {q['time_on']:%Y-%m-%d %H:%M} UTC  {(q.get('band') or '').lower():6} {mode:6} "
            f"RST {q.get('rst_sent') or '-'}  {format_freq(q.get('freq'))} MHz")


def plain_text(t, who, mycall, opname, links, qsos, from_line):
    lines = [t['hello'].format(who=who), '', t['thanks_many'] if len(qsos) > 1 else t['thanks'], '']
    if from_line:
        lines += [f"{t['from']} {from_line}", '']
    lines += [qso_line(q) for q in qsos]
    lines += ['', '73!', f'{opname + ", " if opname else ""}{mycall}']
    lines += [url for _, url in links]
    lines += ['', t['optout'], '']
    return '\n'.join(lines)


def html_page(t, who, mycall, opname, location, links, qsos, cids, from_line, theme):
    e = html.escape
    th = 'padding:8px 10px 8px 0;font-size:11px;letter-spacing:1px;color:#7a3a63;text-align:left;font-weight:600;'
    td = ('padding:8px 10px 8px 0;font-size:14px;color:#16122b;font-weight:700;border-top:1px solid #ece6f0;'
          'white-space:nowrap;vertical-align:top;')
    small = 'display:block;font-size:12px;color:#6b6480;font-weight:600;'
    rows = ''.join(
        f'<tr><td style="{td}">{q["time_on"]:%Y-%m-%d}<span style="{small}">{q["time_on"]:%H:%M} UTC</span></td>'
        + ''.join(f'<td style="{td}">{e(v)}</td>' for v in (
            (q.get('band') or '').lower(), q.get('submode') or q.get('mode') or '', q.get('rst_sent') or '',
            format_freq(q.get('freq'))))
        + '</tr>' for q in qsos)
    cards = ''.join(
        f'<img src="cid:{cid}" width="552" alt="QSL {e(mycall)} – {e(q["callsign"])}" '
        f'style="display:block;width:100%;max-width:552px;height:auto;border-radius:10px;margin:0 0 14px;border:0;">'
        for q, cid in zip(qsos, cids))
    station = (f'<p style="margin:0 0 6px;font-size:13px;color:#7a3a63;font-weight:600;">'
               f'{e(t["from"])} {e(from_line)}</p>') if from_line else ''
    links_html = ' &nbsp;·&nbsp; '.join(
        f'<a href="{e(url)}" style="color:#ffcf6e;text-decoration:none;font-weight:600;">{e(label)}</a>'
        for label, url in links)
    top, mid, bottom = theme
    font = "Montserrat,'Segoe UI',Helvetica,Arial,sans-serif"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>QSL {e(mycall)}</title></head>
<body style="margin:0;padding:0;background:#0e1636;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#0e1636;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;font-family:{font};">
  <tr><td style="background:{top};background-image:linear-gradient(120deg,{top} 0%,{mid} 60%,{bottom} 100%);border-radius:14px 14px 0 0;padding:26px 24px;">
    <div style="font-size:12px;letter-spacing:3px;color:#ffcf6e;font-weight:700;">QSL</div>
    <div style="font-size:36px;line-height:1.1;color:#ffffff;font-weight:800;letter-spacing:1px;margin-top:4px;">{e(mycall)}</div>
    <div style="font-size:14px;color:#f3e2a8;margin-top:6px;">{e(location)}</div>
  </td></tr>
  <tr><td style="background:#ffffff;padding:26px 24px 8px;">
    <p style="margin:0 0 8px;font-size:22px;color:#16122b;font-weight:700;">{e(t['hello'].format(who=who))}</p>
    <p style="margin:0 0 20px;font-size:16px;line-height:1.5;color:#3a3450;">{e(t['thanks_many'] if len(qsos) > 1 else t['thanks'])}</p>
    {cards}
    {station}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 22px;">
      <tr>{''.join(f'<td style="{th}">{e(c)}</td>' for c in t['cols'])}</tr>
      {rows}
    </table>
    <p style="margin:0;font-size:24px;color:#e0704a;font-weight:800;">73!</p>
    <p style="margin:4px 0 24px;font-size:16px;color:#16122b;font-weight:600;">{e(opname + ', ' if opname else '')}{e(mycall)}</p>
  </td></tr>
  <tr><td style="background:#16122b;border-radius:0 0 14px 14px;padding:18px 24px;">
    <p style="margin:0 0 8px;font-size:14px;">{links_html}</p>
    <p style="margin:0;font-size:12px;line-height:1.5;color:#a59fbf;">{e(t['optout'])}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def links_for(cfg, mycall):
    links = []
    site = conf.get(cfg, 'mail', 'website')
    if site:
        links.append((site.split('://', 1)[-1].rstrip('/'), site))
    if conf.get_bool(cfg, 'mail', 'qrz_link', True):
        links.append(('QRZ.com', f'https://www.qrz.com/db/{lookup_variants(mycall)[-1]}'))
    return links


def operator_name(cfg, station, sender):
    """[mail] operator, else the Wavelog user's first name, else the sender name without the callsign."""
    name = conf.get(cfg, 'mail', 'operator') or (station.get('operator') or '').strip()
    if name:
        return name
    return ' '.join(w for w in parseaddr(sender)[0].split() if not any(ch.isdigit() for ch in w))


HEADER_THEMES = {
    'dusk': ('#2c2457', '#7a3a63', '#e0704a'),
    'ocean': ('#0c345c', '#146c94', '#3fb0b8'),
    'forest': ('#16382a', '#2e6840', '#a8923c'),
}


def build_message(cfg, card, qsos, email, sender):
    """Returns (message, [(filename, jpeg bytes)])."""
    first = qsos[0]
    station = card.qso_station(first)
    mycall = station['callsign'] or conf.get(cfg, 'mail', 'my_callsign', '')
    lang = language_for(cfg, first['callsign'])
    t = TEXT[lang]
    name = (first.get('name') or '').split(' ')[0].title()
    who = name or first['callsign']
    opname = operator_name(cfg, station, sender)
    links = links_for(cfg, mycall)
    grid = format_grid(station['grid'])
    location = conf.get(cfg, 'mail', 'location') or ' · '.join(
        p for p in (station.get('city'), grid, station.get('country')) if p)
    if station['ref']:
        location = f"{location} · {station['ref']}" if location else station['ref']
    from_line = card.station_line(first)
    from_line = from_line[len('FROM '):] if from_line.startswith('FROM ') else from_line

    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = email
    for header, key in (('Reply-To', 'reply_to'), ('Bcc', 'bcc')):
        if conf.get(cfg, 'mail', key):
            msg[header] = conf.get(cfg, 'mail', key)
    subject = conf.get(cfg, 'mail', f'subject_{lang}') or t['subject']
    msg['Subject'] = subject.format(mycall=mycall, callsign=first['callsign'])
    msg['Date'] = formatdate(localtime=True)
    domain = parseaddr(sender)[1].rsplit('@', 1)[-1] or None
    msg['Message-ID'] = make_msgid(domain=domain)
    msg.set_content(plain_text(t, who, mycall, opname, links, qsos, from_line))

    cards = []
    for q in qsos:
        fname = (f"{mycall.replace('/', '-')}_{q['callsign'].replace('/', '-')}_"
                 f"{q['time_on']:%Y%m%d_%H%M}_{(q.get('band') or '').lower()}.jpg")
        cards.append((fname, card.render(q)))
    if conf.get_bool(cfg, 'mail', 'html', True):
        cids = [make_msgid(domain=domain)[1:-1] for _ in cards]
        theme = HEADER_THEMES.get(conf.get(cfg, 'card', 'theme', 'dusk'), HEADER_THEMES['dusk'])
        msg.add_alternative(html_page(t, who, mycall, opname, location, links, qsos, cids, from_line, theme),
                            subtype='html')
        html_part = msg.get_payload()[-1]
        for (fname, data), cid in zip(cards, cids):
            html_part.add_related(data, maintype='image', subtype='jpeg', cid=f'<{cid}>',
                                  filename=fname, disposition='inline')
    else:
        for fname, data in cards:
            msg.add_attachment(data, maintype='image', subtype='jpeg', filename=fname)
    return msg, cards


def preview_html(msg, cards):
    """The HTML part with cid: links pointing at the card files saved next to it."""
    page = msg.get_body(('html',))
    if page is None:
        return None
    text = page.get_content()
    related = [p for p in msg.walk() if p.get('Content-ID')]
    for part, (fname, _) in zip(related, cards):
        text = text.replace('cid:' + part['Content-ID'].strip('<>'), fname)
    return text


def settings(cfg, wl):
    """SMTP server and sender: [smtp]/[mail] in config.ini, otherwise Wavelog's e-mail settings."""
    o = wl.options(MAIL_OPTIONS)
    host = conf.get(cfg, 'smtp', 'host')
    if host:
        security = conf.get(cfg, 'smtp', 'security', 'starttls').lower()
        port = int(conf.get(cfg, 'smtp', 'port', 465 if security == 'ssl' else 587))
        user, password = conf.get(cfg, 'smtp', 'user'), conf.get(cfg, 'smtp', 'password')
    else:
        host = o.get('smtpHost')
        security = {'tls': 'starttls', 'ssl': 'ssl'}.get(o.get('smtpEncryption', '').lower(), 'none')
        port = int(o.get('smtpPort') or (465 if security == 'ssl' else 587))
        user, password = o.get('smtpUsername'), o.get('smtpPassword')
    sender = conf.get(cfg, 'mail', 'from') or (
        formataddr((o.get('emailSenderName', ''), o['emailAddress'])) if o.get('emailAddress') else '')
    return {'host': host, 'port': port, 'security': security, 'user': user, 'password': password, 'sender': sender}


class Mailer:
    def __init__(self, s):
        self.s, self.smtp = s, None

    def send(self, msg):
        s = self.s
        if self.smtp is None:
            ctx = ssl.create_default_context()
            if s['security'] == 'ssl':
                self.smtp = smtplib.SMTP_SSL(s['host'], s['port'], context=ctx, timeout=30)
            else:
                self.smtp = smtplib.SMTP(s['host'], s['port'], timeout=30)
                if s['security'] == 'starttls':
                    self.smtp.starttls(context=ctx)
            if s['user']:
                self.smtp.login(s['user'], s['password'])
        self.smtp.send_message(msg)

    def close(self):
        if self.smtp is not None:
            try:
                self.smtp.quit()
            except smtplib.SMTPException:
                pass
