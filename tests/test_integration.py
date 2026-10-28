"""End-to-end run against a real MariaDB/MySQL with Wavelog's tables and a local SMTP server.

Runs when MYSQL_HOST is set (CI provides a MariaDB service), e.g.:
  MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=root pytest tests/test_integration.py
"""

import email
import email.policy
import os
import socket

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


@pytest.fixture
def wavelog(tmp_path, smtp, monkeypatch):
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
        f"[wavelog]\nconfig_dir = {cfgdir}\n[qso]\nstation_ids = 1,2\nsince = 2026-01-01\n"
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
