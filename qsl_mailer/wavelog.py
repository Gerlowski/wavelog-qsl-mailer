"""Wavelog access: QSOs and Station Locations through Wavelog's API.

The API has no way to change an existing QSO and does not expose the e-mail settings or the
user's name, so these few things still go to the database: the contact e-mail, QSL sent,
Admin -> Global Options -> Email and the operator's first name.
"""

import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import pymysql

from . import __version__, config as conf

# Asked from get_contacts_adif; Wavelog merges rows that are equal in all of them (true duplicates)
API_FIELDS = ['CALL', 'EMAIL', 'QSO_DATE', 'TIME_ON', 'BAND', 'MODE', 'SUBMODE', 'FREQ', 'RST_SENT', 'PROP_MODE',
              'SAT_NAME', 'NAME', 'STATION_CALLSIGN', 'MY_GRIDSQUARE', 'MY_POTA_REF', 'MY_WWFF_REF', 'MY_SOTA_REF']
API_PAGE = 1000

MAIL_OPTIONS = ['emailProtocol', 'smtpEncryption', 'smtpHost', 'smtpPort',
                'smtpUsername', 'smtpPassword', 'emailAddress', 'emailSenderName']


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # Wavelog stores naive UTC


def qso_key(station_id, callsign, time_on, band):
    """Identifies a QSO: the API does not return Wavelog's QSO ID."""
    return f'{station_id}|{(callsign or "").upper()}|{time_on:%Y-%m-%d %H:%M:%S}|{(band or "").lower()}'


def api_url(cfg, files):
    """[wavelog] api_url, else Wavelog's base_url from config.php (the "API URL" on Wavelog's API page)."""
    url = conf.get(cfg, 'wavelog', 'api_url')
    if not url and files['config'].get('base_url'):
        url = files['config']['base_url'].rstrip('/') + '/index.php'
    return (url or '').rstrip('/')


class Wavelog:
    def __init__(self, cfg, files):
        self.api_url = api_url(cfg, files)
        if not self.api_url:
            sys.exit('[wavelog] api_url: set the API URL shown in Wavelog (menu -> API Keys)')
        self.api_key = conf.get(cfg, 'wavelog', 'api_key')
        if not self.api_key:
            sys.exit('[wavelog] api_key: create a read-only API key in Wavelog (menu -> API Keys) and set it here')

        wdb = files['db']
        host = conf.get(cfg, 'database', 'host', wdb.get('hostname') or 'localhost')
        port = conf.get(cfg, 'database', 'port', wdb.get('port') or 3306)
        if ':' in host:  # CodeIgniter accepts "host:port"
            host, port = host.rsplit(':', 1)
        self.host = host
        self.table = conf.get(cfg, 'database', 'table', files['config'].get('table_name') or 'TABLE_HRD_CONTACTS_V01')
        if not re.fullmatch(r'\w+', self.table):
            sys.exit('[database] table: letters, digits and _ only')
        user = conf.get(cfg, 'database', 'user', wdb.get('username'))
        if not user:
            sys.exit('No database settings: set [wavelog] config_dir or fill in [database]')
        self.conn = pymysql.connect(
            host=host, port=int(port), user=user,
            password=conf.get(cfg, 'database', 'password', wdb.get('password', '')),
            database=conf.get(cfg, 'database', 'name', wdb.get('database')),
            unix_socket=conf.get(cfg, 'database', 'socket'),
            charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=True,
            connect_timeout=15, read_timeout=60, write_timeout=60)

        self.station_ids = [int(s) for s in conf.csv_list(conf.get(cfg, 'qso', 'station_ids'))]
        if not self.station_ids:
            listing = '\n'.join(f"  {s['station_id']:>4}  {s['callsign']:<12} {s['name']}" for s in self.all_stations())
            sys.exit('[qso] station_ids: set the IDs of your Station Locations (comma separated).\n'
                     'Station Locations of this API key:\n' + listing)
        since = conf.get(cfg, 'qso', 'since')
        if not since:
            sys.exit('[qso] since: set the first QSO date (UTC) to send cards for, e.g. 2026-01-01')
        self.since = dt.datetime.fromisoformat(since)
        self.min_age = dt.timedelta(minutes=int(conf.get(cfg, 'qso', 'min_age_minutes', 30)))
        self.stations = self.load_stations(self.station_ids)

    def close(self):
        self.conn.close()

    def query(self, sql, args=()):
        with self.conn.cursor() as c:
            c.execute(sql, args)
            return c.fetchall()

    def execute(self, sql, args=()):
        with self.conn.cursor() as c:
            return c.execute(sql, args)

    def api(self, endpoint, payload=None):
        """GET (no payload) or POST JSON (payload plus the key) to Wavelog's API."""
        data = None if payload is None else json.dumps({'key': self.api_key, **payload}).encode()
        req = urllib.request.Request(f'{self.api_url}/api/{endpoint}', data=data, headers={
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'User-Agent': f'wavelog-qsl-mailer/{__version__}'})
        name = endpoint.split('/')[0]
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            try:
                reason = json.loads(e.read()).get('reason') or ''
            except ValueError:
                reason = ''
            raise RuntimeError(f'Wavelog API {name}: HTTP {e.code} {reason}'.rstrip()) from None
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError(f'Wavelog API {name}: no JSON answer, check [wavelog] api_url ({self.api_url})') from None

    # --- stations ---

    def all_stations(self):
        rows = self.api('station_info/' + urllib.parse.quote(self.api_key, safe=''))
        return [{'station_id': int(r['station_id']), 'name': r.get('station_profile_name') or '',
                 'callsign': (r.get('station_callsign') or '').upper(), 'row': r} for r in rows]

    def load_stations(self, ids):
        """Station Location details used on the card and in the mail, keyed by station_id."""
        found = {s['station_id']: s['row'] for s in self.all_stations()}
        missing = set(ids) - set(found)
        if missing:
            sys.exit(f'[qso] station_ids: no Station Location with ID {", ".join(map(str, sorted(missing)))} '
                     f'for this API key')
        names = self.operator_names(ids)
        stations = {}  # keeps the configured order: the first one is the main station
        for i in ids:
            r = found[i]
            stations[i] = {'station_id': i, 'callsign': (r.get('station_callsign') or '').upper(),
                           'grid': r.get('station_gridsquare') or '', 'city': r.get('station_city') or '',
                           'cq': r.get('station_cq'), 'itu': r.get('station_itu'), 'pota': r.get('station_pota'),
                           'country': (r.get('station_country') or '').title(), 'operator': names.get(i)}
        return stations

    def operator_names(self, ids):
        """First name of the Wavelog user owning each Station Location (not in the API)."""
        marks = ','.join(['%s'] * len(ids))
        rows = self.query(f"SELECT s.station_id, u.user_firstname AS name FROM station_profile s "
                          f"JOIN users u ON u.user_id = s.user_id WHERE s.station_id IN ({marks})", ids)
        return {r['station_id']: r['name'] for r in rows}

    # --- QSOs ---

    @staticmethod
    def _qso(station_id, q):
        time_on = dt.datetime.strptime(q['QSO_DATE'] + q['TIME_ON'], '%Y%m%d%H%M%S')
        freq = q.get('FREQ')
        return {'key': qso_key(station_id, q.get('CALL'), time_on, q.get('BAND')),
                'callsign': (q.get('CALL') or '').upper(), 'email': q.get('EMAIL') or '', 'time_on': time_on,
                'band': q.get('BAND') or '', 'mode': q.get('MODE'), 'submode': q.get('SUBMODE'),
                'freq': round(float(freq) * 1e6) if freq else None, 'rst_sent': q.get('RST_SENT'),
                'prop_mode': q.get('PROP_MODE'), 'sat_name': q.get('SAT_NAME'), 'name': q.get('NAME'),
                'station_callsign': (q.get('STATION_CALLSIGN') or '').upper(), 'my_grid': q.get('MY_GRIDSQUARE'),
                'my_pota': q.get('MY_POTA_REF'), 'my_wwff': q.get('MY_WWFF_REF'), 'my_sota': q.get('MY_SOTA_REF'),
                'station_id': station_id}

    def fetch(self, state=None):
        """QSOs of the configured Station Locations, in log order. With state: only from the stored
        position on, which moves past pages of QSOs before `since` (they never need a card)."""
        out = []
        for sid in self.station_ids:
            last = state.cursor(sid, self.since) if state else 0
            moving = state is not None
            while True:
                r = self.api('get_contacts_adif', {'station_id': sid, 'fetchfromid': last, 'limit': API_PAGE,
                                                   'output_format': 'json', 'fields': API_FIELDS})
                page = [self._qso(sid, q) for q in r.get('qsos') or []]
                new_last = int(r.get('lastfetchedid') or last)
                if new_last <= last:
                    break
                moving = moving and all(q['time_on'] < self.since for q in page)
                if moving:
                    state.set_cursor(sid, self.since, new_last)
                out += page
                last = new_last
        return out

    def new_qsos(self, state):
        """QSOs on or after `since` and older than min_age."""
        until = utcnow() - self.min_age
        return [q for q in self.fetch(state) if self.since <= q['time_on'] <= until]

    def calls_without_email(self, state):
        return sorted({q['callsign'] for q in self.new_qsos(state) if q['callsign'] and not q['email']})

    def set_email(self, callsign, email):
        marks = ','.join(['%s'] * len(self.station_ids))
        return self.execute(f"UPDATE {self.table} SET COL_EMAIL = %s WHERE station_id IN ({marks}) "
                            f"AND COL_TIME_ON >= %s AND COL_TIME_ON <= %s "
                            f"AND UPPER(COL_CALL) = %s AND (COL_EMAIL IS NULL OR COL_EMAIL = '')",
                            [email, *self.station_ids, self.since, utcnow() - self.min_age, callsign])

    def qsos_for_call(self, callsign):
        """All QSOs with this callsign and an e-mail, ignoring the date window (manual resend)."""
        return [q for q in self.fetch() if q['callsign'] == callsign.upper() and q['email']]

    def mark_qsl_sent(self, qso):
        self.execute(f"UPDATE {self.table} SET COL_QSL_SENT = 'Y', COL_QSL_SENT_VIA = 'E', COL_QSLSDATE = %s "
                     f"WHERE station_id = %s AND UPPER(COL_CALL) = %s AND COL_TIME_ON = %s AND COL_BAND = %s "
                     f"AND (COL_QSL_SENT IS NULL OR COL_QSL_SENT <> 'Y')",
                     [utcnow().replace(microsecond=0), qso['station_id'], qso['callsign'], qso['time_on'],
                      qso['band']])

    def qso_keys(self, ids):
        """{QSO ID: qso_key} for state files written before the API was used."""
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ','.join(['%s'] * len(chunk))
            for r in self.query(f"SELECT COL_PRIMARY_KEY AS id, station_id, COL_CALL AS callsign, "
                                f"COL_TIME_ON AS time_on, COL_BAND AS band FROM {self.table} "
                                f"WHERE COL_PRIMARY_KEY IN ({marks})", chunk):
                if r['time_on']:
                    out[r['id']] = qso_key(r['station_id'], r['callsign'], r['time_on'], r['band'])
        return out

    def options(self, names):
        """Wavelog Admin -> Global Options values (e.g. the e-mail/SMTP settings)."""
        marks = ','.join(['%s'] * len(names))
        rows = self.query(f'SELECT option_name, option_value FROM options WHERE option_name IN ({marks})', names)
        return {r['option_name']: r['option_value'] or '' for r in rows}
