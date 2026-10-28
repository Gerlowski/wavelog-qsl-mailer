"""Read access to the Wavelog database (plus the two writes: contact e-mail and QSL sent)."""

import datetime as dt
import re
import sys

import pymysql

from . import config as conf

QSO_COLUMNS = (
    "COL_PRIMARY_KEY AS id, UPPER(COL_CALL) AS callsign, COL_EMAIL AS email, COL_TIME_ON AS time_on, "
    "COL_BAND AS band, COL_MODE AS mode, COL_SUBMODE AS submode, COL_FREQ AS freq, "
    "COL_RST_SENT AS rst_sent, COL_PROP_MODE AS prop_mode, COL_SAT_NAME AS sat_name, COL_NAME AS name, "
    "UPPER(COL_STATION_CALLSIGN) AS station_callsign, COL_MY_GRIDSQUARE AS my_grid, "
    "COL_MY_POTA_REF AS my_pota, COL_MY_WWFF_REF AS my_wwff, COL_MY_SOTA_REF AS my_sota, station_id"
)

MAIL_OPTIONS = ['emailProtocol', 'smtpEncryption', 'smtpHost', 'smtpPort',
                'smtpUsername', 'smtpPassword', 'emailAddress', 'emailSenderName']


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # Wavelog stores naive UTC


class Wavelog:
    def __init__(self, cfg, files):
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
                     'Station Locations in this Wavelog:\n' + listing)
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

    # --- stations ---

    def all_stations(self):
        return self.query("SELECT station_id, station_profile_name AS name, UPPER(station_callsign) AS callsign "
                          "FROM station_profile ORDER BY station_id")

    def load_stations(self, ids):
        """Station Location details used on the card and in the mail, keyed by station_id."""
        marks = ','.join(['%s'] * len(ids))
        rows = self.query(
            f"SELECT s.station_id, UPPER(s.station_callsign) AS callsign, s.station_gridsquare AS grid, "
            f"s.station_city AS city, s.station_cq AS cq, s.station_itu AS itu, s.station_pota AS pota, "
            f"d.name AS country, u.user_firstname AS operator "
            f"FROM station_profile s LEFT JOIN dxcc_entities d ON d.adif = s.station_dxcc "
            f"LEFT JOIN users u ON u.user_id = s.user_id WHERE s.station_id IN ({marks})", ids)
        stations = {r['station_id']: r for r in rows}
        missing = set(ids) - set(stations)
        if missing:
            sys.exit(f'[qso] station_ids: no Station Location with ID {", ".join(map(str, sorted(missing)))}')
        for s in stations.values():
            s['country'] = (s['country'] or '').title()
        return stations

    # --- QSOs ---

    def _where(self):
        marks = ','.join(['%s'] * len(self.station_ids))
        return (f'station_id IN ({marks}) AND COL_TIME_ON >= %s AND COL_TIME_ON <= %s',
                [*self.station_ids, self.since, utcnow() - self.min_age])

    def calls_without_email(self):
        where, args = self._where()
        rows = self.query(f"SELECT DISTINCT UPPER(COL_CALL) AS callsign FROM {self.table} "
                          f"WHERE {where} AND (COL_EMAIL IS NULL OR COL_EMAIL = '')", args)
        return [r['callsign'] for r in rows if r['callsign']]

    def set_email(self, callsign, email):
        where, args = self._where()
        return self.execute(f"UPDATE {self.table} SET COL_EMAIL = %s WHERE {where} "
                            f"AND UPPER(COL_CALL) = %s AND (COL_EMAIL IS NULL OR COL_EMAIL = '')",
                            [email, *args, callsign])

    def qsos_with_email(self):
        where, args = self._where()
        return self.query(f"SELECT {QSO_COLUMNS} FROM {self.table} WHERE {where} "
                          f"AND COL_EMAIL IS NOT NULL AND COL_EMAIL <> '' ORDER BY COL_TIME_ON", args)

    def qsos_for_call(self, callsign):
        """All QSOs with this callsign and an e-mail, ignoring the date window (manual resend)."""
        marks = ','.join(['%s'] * len(self.station_ids))
        return self.query(f"SELECT {QSO_COLUMNS} FROM {self.table} WHERE station_id IN ({marks}) "
                          f"AND UPPER(COL_CALL) = %s AND COL_EMAIL IS NOT NULL AND COL_EMAIL <> '' "
                          f"ORDER BY COL_TIME_ON", [*self.station_ids, callsign.upper()])

    def mark_qsl_sent(self, qso_id):
        self.execute(f"UPDATE {self.table} SET COL_QSL_SENT = 'Y', COL_QSL_SENT_VIA = 'E', COL_QSLSDATE = %s "
                     f"WHERE COL_PRIMARY_KEY = %s AND (COL_QSL_SENT IS NULL OR COL_QSL_SENT <> 'Y')",
                     [utcnow().replace(microsecond=0), qso_id])

    def options(self, names):
        """Wavelog Admin -> Global Options values (e.g. the e-mail/SMTP settings)."""
        marks = ','.join(['%s'] * len(names))
        rows = self.query(f'SELECT option_name, option_value FROM options WHERE option_name IN ({marks})', names)
        return {r['option_name']: r['option_value'] or '' for r in rows}
