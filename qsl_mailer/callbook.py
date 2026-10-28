"""E-mail lookups on QRZ.com and HamQTH, with the same credentials Wavelog uses."""

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import config as conf

UA = 'wavelog-qsl-mailer/1.0'
EMAIL_RE = re.compile(r'^[^@\s,;<>"]+@[^@\s,;<>"]+\.[A-Za-z]{2,}$')
# Parts after "/" that are not a callsign (SP9ABC/P, SP9ABC/QRP)
SUFFIXES = {'P', 'M', 'MM', 'AM', 'QRP', 'A', 'R', 'B', 'T'}


def http_xml(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return ET.fromstring(r.read())


def xml_text(root, path):
    el = root.find(path)
    return (el.text or '').strip() if el is not None else ''


class _Session:
    """Lazy login, one retry when the session key expires."""
    name = ''

    def __init__(self, user, password):
        self.user, self.password, self.key = user, password, None

    def email(self, callsign):
        for attempt in (1, 2):
            if not self.key:
                self._login()
            root = self._lookup(callsign)
            error = self._error(root)
            if error and 'session' in error.lower() and attempt == 1:
                self.key = None
                continue
            return self._email(root)
        return ''


class HamQTH(_Session):
    name = 'hamqth'

    def _login(self):
        root = http_xml('https://www.hamqth.com/xml.php?' + urllib.parse.urlencode({'u': self.user, 'p': self.password}))
        self.key = xml_text(root, './/{*}session_id')
        if not self.key:
            raise RuntimeError('HamQTH login failed: ' + xml_text(root, './/{*}error'))

    def _lookup(self, callsign):
        return http_xml('https://www.hamqth.com/xml.php?' + urllib.parse.urlencode(
            {'id': self.key, 'callsign': callsign, 'prg': 'wavelog-qsl-mailer'}))

    def _error(self, root):
        return xml_text(root, './/{*}error')

    def _email(self, root):
        return xml_text(root, './/{*}search/{*}email')


class QRZ(_Session):
    name = 'qrz'

    def _login(self):
        root = http_xml('https://xmldata.qrz.com/xml/current/?' + urllib.parse.urlencode(
            {'username': self.user, 'password': self.password, 'agent': UA}))
        self.key = xml_text(root, './/{*}Key')
        if not self.key:
            raise RuntimeError('QRZ.com login failed: ' + xml_text(root, './/{*}Error'))

    def _lookup(self, callsign):
        return http_xml('https://xmldata.qrz.com/xml/current/?' + urllib.parse.urlencode(
            {'s': self.key, 'callsign': callsign}))

    def _error(self, root):
        return xml_text(root, './/{*}Error')

    def _email(self, root):
        return xml_text(root, './/{*}Callsign/{*}email')


PROVIDERS = {'qrz': QRZ, 'hamqth': HamQTH}


def configured(cfg, files):
    """Callbooks in [callbook] order; credentials from config.ini or Wavelog's config.php."""
    wcfg = files['config']
    default_order = wcfg.get('callbook') or 'qrz,hamqth'
    if isinstance(default_order, list):
        default_order = ','.join(default_order)
    order = conf.csv_list(conf.get(cfg, 'callbook', 'order', default_order))
    for name in ('qrz', 'hamqth'):  # try the other one too if it has credentials
        if name not in order:
            order.append(name)
    out = []
    for name in order:
        if name not in PROVIDERS:
            continue
        user = conf.get(cfg, 'callbook', f'{name}_user', wcfg.get(f'{name}_username'))
        password = conf.get(cfg, 'callbook', f'{name}_password', wcfg.get(f'{name}_password'))
        if user and password:
            out.append(PROVIDERS[name](user, password))
    return out


def lookup_variants(callsign):
    """SP9ABC/P -> [SP9ABC/P, SP9ABC]; DL/SP9ABC -> [DL/SP9ABC, SP9ABC]."""
    parts = [p for p in callsign.split('/') if p and p not in SUFFIXES and not p.isdigit()]
    base = max(parts, key=len) if parts else callsign
    return [callsign] if base == callsign else [callsign, base]


def clean_email(value):
    value = (value or '').strip().strip('<>').lower()
    return value if EMAIL_RE.match(value) else ''
