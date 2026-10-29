"""End-to-end run against a real MariaDB/MySQL with Wavelog's tables, a stand-in for Wavelog's API
on top of it and a local SMTP server.

Runs when MYSQL_HOST is set (CI provides a MariaDB service), e.g.:
  MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=root pytest tests/test_integration.py
"""

import email
import email.policy
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymysql
import pytest

from qsl_mailer import callbook
from qsl_mailer.cli import main

pytestmark = pytest.mark.skipif(not os.environ.get('MYSQL_HOST'), reason='MYSQL_HOST not set')

SCHEMA = """
CREATE TABLE TABLE_HRD_CONTACTS_V01 (
  COL_PRIMARY_KEY int NOT NULL AUTO_INCREMENT PRIMARY KEY, COL_CALL varchar(64), COL_EMAIL varchar(255),
  COL_TIME_ON datetime, COL_BAND varchar(10), COL_MODE varchar(12), COL_SUBMODE varchar(64), COL_FREQ bigint,
  COL_RST_SENT varchar(64), COL_PROP_MODE varchar(8), COL_SAT_NAME varchar(64), COL_NAME varchar(128),
  COL_STATION_CALLSIGN varchar(64), COL_MY_GRIDSQUARE varchar(12), COL_MY_POTA_REF varchar(64),
  COL_MY_WWFF_REF varchar(64), COL_MY_SOTA_REF varchar(64), COL_QSL_SENT varchar(2), COL_QSL_SENT_VIA varchar(2),
  COL_QSLSDATE datetime, station_id int) DEFAULT CHARSET=utf8mb4;
CREATE TABLE station_profile (
  station_id int unsigned NOT NULL AUTO_INCREMENT PRIMARY KEY, station_profile_name varchar(200),
  station_gridsquare varchar(100), station_city varchar(100), station_callsign varchar(50), station_dxcc int,
  station_cq int, station_itu int, station_pota varchar(50), user_id int unsigned) DEFAULT CHARSET=utf8mb4;
CREATE TABLE users (user_id int NOT NULL PRIMARY KEY, user_firstname varchar(32)) DEFAULT CHARSET=utf8mb4;
CREATE TABLE dxcc_entities (adif smallint NOT NULL PRIMARY KEY, name varchar(150)) DEFAULT CHARSET=utf8mb4;
CREATE TABLE options (option_id int NOT NULL AUTO_INCREMENT PRIMARY KEY, option_name varchar(191),
  option_value longtext, autoload varchar(20)) DEFAULT CHARSET=utf8mb4;
INSERT INTO dxcc_entities VALUES (269, 'POLAND');
INSERT INTO users VALUES (1, 'Bartosz');
INSERT INTO station_profile VALUES (1, 'Home', 'KO13AE', 'Łomża', 'SP4BL', 269, 15, 28, NULL, 1),
                                   (2, 'Portable', 'KO13BC', 'Łomża', 'SP4BL/P', 269, 15, 28, NULL, 1);
INSERT INTO options (option_name, option_value) VALUES ('emailProtocol', 'smtp'), ('smtpEncryption', ''),
  ('smtpHost', '127.0.0.1'), ('smtpPort', '{smtp_port}'), ('smtpUsername', ''), ('smtpPassword', ''),
  ('emailAddress', 'qsl@example.org'), ('emailSenderName', 'Bartosz SP4BL');
INSERT INTO TABLE_HRD_CONTACTS_V01 (COL_CALL, COL_EMAIL, COL_TIME_ON, COL_BAND, COL_MODE, COL_FREQ, COL_RST_SENT,
  COL_STATION_CALLSIGN, COL_MY_GRIDSQUARE, station_id) VALUES
  ('DK1ABC', 'dk1abc@example.de', UTC_TIMESTAMP() - INTERVAL 3 HOUR, '20M', 'FT8', 14074000, '-08', 'SP4BL', 'KO13AE', 1),
  ('DK1ABC', 'dk1abc@example.de', UTC_TIMESTAMP() - INTERVAL 2 HOUR, '40M', 'SSB', 7150000, '59', 'SP4BL', 'KO13AE', 1),
  ('SQ4MK', NULL, UTC_TIMESTAMP() - INTERVAL 2 HOUR, '2M', 'FM', 145250000, '59', 'SP4BL/P', 'KO13BC', 2),
  ('K1NEW', 'k1@example.com', UTC_TIMESTAMP() - INTERVAL 5 MINUTE, '20M', 'FT8', 14074000, '-01', 'SP4BL', 'KO13AE', 1),
  ('OLD1', 'old@example.com', '2020-01-01 12:00:00', '20M', 'SSB', 14200000, '59', 'SP4BL', 'KO13AE', 1),
  ('OTHER', 'o@example.com', UTC_TIMESTAMP() - INTERVAL 2 HOUR, '20M', 'SSB', 14200000, '59', 'SP4XX', 'KO00AA', 3);
"""


@pytest.fixture
def smtp():
    from aiosmtpd.controller import Controller
    received = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append(email.message_from_bytes(envelope.content, policy=email.policy.default))
            return '250 OK'

    with socket.socket() as sock:  # a free port; aiosmtpd's self-check cannot use port 0
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    ctl = Controller(Handler(), hostname='127.0.0.1', port=port)
    ctl.start()
    yield ctl, received
    ctl.stop()


API_KEY = 'wlTESTKEY'


def db_connect(database):
    return pymysql.connect(host=os.environ['MYSQL_HOST'], port=int(os.environ.get('MYSQL_PORT', 3306)),
                           user=os.environ.get('MYSQL_USER', 'root'), password=os.environ.get('MYSQL_PASSWORD', ''),
                           database=database, charset='utf8mb4', autocommit=True,
                           cursorclass=pymysql.cursors.DictCursor)


class FakeApi(BaseHTTPRequestHandler):
    """station_info and get_contacts_adif (JSON) answering like Wavelog's API v1."""

    def log_message(self, *args):
        pass

    def answer(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != f'/index.php/api/station_info/{API_KEY}':
            return self.answer(401, {'status': 'failed', 'reason': 'missing or invalid api key'})
        with db_connect('qslmailer_test') as db, db.cursor() as c:
            c.execute("SELECT s.station_id, s.station_profile_name, s.station_gridsquare, s.station_callsign, "
                      "s.station_city, s.station_pota, s.station_cq, s.station_itu, d.name AS station_country "
                      "FROM station_profile s LEFT JOIN dxcc_entities d ON d.adif = s.station_dxcc")
            self.answer(200, [{k: (None if v is None else str(v)) for k, v in r.items()} for r in c.fetchall()])

    def do_POST(self):
        obj = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/index.php/api/get_contacts_adif' or obj.get('key') != API_KEY:
            return self.answer(401, {'status': 'failed', 'reason': 'missing api key'})
        assert obj['output_format'] == 'json' and 'EMAIL' in obj['fields']
        with db_connect('qslmailer_test') as db, db.cursor() as c:
            # like Wavelog: the MY_* values come from the Station Location
            c.execute("SELECT q.*, s.station_callsign, s.station_gridsquare, s.station_pota "
                      "FROM TABLE_HRD_CONTACTS_V01 q JOIN station_profile s ON s.station_id = q.station_id "
                      "WHERE q.station_id = %s AND q.COL_PRIMARY_KEY > %s ORDER BY q.COL_PRIMARY_KEY LIMIT %s",
                      (obj['station_id'], obj['fetchfromid'], obj['limit']))
            rows = c.fetchall()
        if not rows:
            return self.answer(200, {'status': 'successful', 'message': 'No new QSOs available.',
                                     'lastfetchedid': obj['fetchfromid'], 'exported_qsos': 0, 'adif': None})
        qsos = [{'CALL': r['COL_CALL'], 'EMAIL': r['COL_EMAIL'], 'QSO_DATE': f"{r['COL_TIME_ON']:%Y%m%d}",
                 'TIME_ON': f"{r['COL_TIME_ON']:%H%M%S}", 'BAND': r['COL_BAND'], 'MODE': r['COL_MODE'],
                 'SUBMODE': r['COL_SUBMODE'], 'FREQ': r['COL_FREQ'] / 1000000 if r['COL_FREQ'] else None,
                 'RST_SENT': r['COL_RST_SENT'], 'PROP_MODE': r['COL_PROP_MODE'], 'SAT_NAME': r['COL_SAT_NAME'],
                 'NAME': r['COL_NAME'], 'STATION_CALLSIGN': r['station_callsign'],
                 'MY_GRIDSQUARE': r['station_gridsquare'], 'MY_POTA_REF': r['station_pota'],
                 'MY_WWFF_REF': None, 'MY_SOTA_REF': None} for r in rows]
        self.answer(200, {'status': 'successful', 'message': 'Export successful',
                          'lastfetchedid': max(r['COL_PRIMARY_KEY'] for r in rows),
                          'exported_records': len(qsos), 'qsos': qsos})


@pytest.fixture
def api():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}/index.php'
    server.shutdown()


@pytest.fixture
def wavelog(tmp_path, smtp, api, monkeypatch):
    ctl, _ = smtp
    root = pymysql.connect(host=os.environ['MYSQL_HOST'], port=int(os.environ.get('MYSQL_PORT', 3306)),
                           user=os.environ.get('MYSQL_USER', 'root'), password=os.environ.get('MYSQL_PASSWORD', ''),
                           charset='utf8mb4', autocommit=True, client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
    with root.cursor() as c:
        c.execute('DROP DATABASE IF EXISTS qslmailer_test')
        c.execute('CREATE DATABASE qslmailer_test CHARACTER SET utf8mb4')
        c.execute('USE qslmailer_test')
        c.execute(SCHEMA.replace('{smtp_port}', str(ctl.port)))
    cfgdir = tmp_path / 'wavelog'
    cfgdir.mkdir()
    (cfgdir / 'config.php').write_text("<?php\n$config['qrz_username'] = 'SP4BL';\n$config['qrz_password'] = 'x';\n")
    (cfgdir / 'database.php').write_text(
        "<?php\n$db['default'] = array(\n"
        f"\t'hostname' => '{os.environ['MYSQL_HOST']}:{os.environ.get('MYSQL_PORT', 3306)}',\n"
        f"\t'username' => '{os.environ.get('MYSQL_USER', 'root')}',\n"
        f"\t'password' => '{os.environ.get('MYSQL_PASSWORD', '')}',\n\t'database' => 'qslmailer_test',\n);\n")
    (tmp_path / 'config.ini').write_text(
        f"[wavelog]\nconfig_dir = {cfgdir}\napi_url = {api}\napi_key = {API_KEY}\n[qso]\nstation_ids = 1,2\nsince = 2026-01-01\nmark_qsl_sent = true\n"
        "[mail]\ndelay_seconds = 0\n")

    class FakeQRZ:
        name = 'qrz'

        def __init__(self, user, password):
            pass

        def email(self, call):
            return {'SQ4MK': 'Mariusz@Example.PL'}.get(call, '')

    monkeypatch.setitem(callbook.PROVIDERS, 'qrz', FakeQRZ)
    with root.cursor() as c:
        c.execute('USE qslmailer_test')
    yield tmp_path, root
    with root.cursor() as c:
        c.execute('DROP DATABASE IF EXISTS qslmailer_test')
    root.close()


def rows(root, sql):
    with root.cursor() as c:
        c.execute(sql)
        return c.fetchall()


def test_full_run(wavelog, smtp):
    tmp_path, root = wavelog
    _, received = smtp
    config = str(tmp_path / 'config.ini')

    main(['-c', config, '--dry-run'])
    assert received == []
    assert rows(root, "SELECT COL_EMAIL FROM TABLE_HRD_CONTACTS_V01 WHERE COL_CALL = 'SQ4MK'") == ((None,),)
    assert sorted(p.name for p in (tmp_path / 'preview').iterdir() if p.suffix == '.html')

    main(['-c', config])
    subjects = sorted(m['Subject'] for m in received)
    assert subjects == ['SP4BL – QSL-Karte für unser QSO, DK1ABC', 'SP4BL/P – karta QSL za QSO z SQ4MK']
    dk = next(m for m in received if 'DK1ABC' in m['Subject'])
    assert [p.get_content_type() for p in dk.walk()].count('image/jpeg') == 2  # both bands in one mail
    assert rows(root, "SELECT COL_EMAIL FROM TABLE_HRD_CONTACTS_V01 WHERE COL_CALL = 'SQ4MK'") == (('mariusz@example.pl',),)
    sent = rows(root, "SELECT COL_CALL, COL_QSL_SENT, COL_QSL_SENT_VIA FROM TABLE_HRD_CONTACTS_V01 "
                      "WHERE COL_QSL_SENT = 'Y' ORDER BY COL_CALL")
    assert sent == (('DK1ABC', 'Y', 'E'), ('DK1ABC', 'Y', 'E'), ('SQ4MK', 'Y', 'E'))

    main(['-c', config])  # nothing new: no duplicates
    assert len(received) == 2

    main(['-c', config, 'resend', 'SQ4MK'])
    assert len(received) == 3 and 'SQ4MK' in received[-1]['Subject']
