import configparser
import datetime as dt
import email
import email.policy
from pathlib import Path

from qsl_mailer import config as conf
from qsl_mailer.callbook import clean_email, lookup_variants
from qsl_mailer.card import Card, format_freq, format_grid
from qsl_mailer.mail import build_message, language_for, preview_html

STATIONS = {
    1: {'station_id': 1, 'callsign': 'SP4BL', 'grid': 'KO13AE', 'city': 'Łomża', 'country': 'Poland',
        'cq': 15, 'itu': 28, 'operator': 'Bartosz', 'pota': None},
    2: {'station_id': 2, 'callsign': 'SP4BL/P', 'grid': 'KO13BC', 'city': 'Łomża', 'country': 'Poland',
        'cq': 15, 'itu': 28, 'operator': 'Bartosz', 'pota': None},
}


def qso(**kw):
    q = {'id': 1, 'callsign': 'DL1XYZ', 'email': 'dl1xyz@example.de', 'time_on': dt.datetime(2026, 10, 28, 19, 52),
         'band': '20M', 'mode': 'SSB', 'submode': 'USB', 'freq': 14205000, 'rst_sent': '59', 'name': 'Hans Meier',
         'station_id': 1, 'station_callsign': 'SP4BL', 'my_grid': 'KO13AE'}
    q.update(kw)
    return q


def make_cfg(tmp_path, **sections):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict(sections)
    cfg.path = Path(tmp_path) / 'config.ini'
    return cfg


def test_php_config_parsing():
    text = """<?php
| $config['callbook'] = 'hamqth';
$config['callbook'] = ['qrz', 'hamqth'];
$config['qrz_username'] = 'SP4BL';
$config['qrz_password'] = 'it\\'s secret'; // comment
$config['table_name'] = "TABLE_HRD_CONTACTS_V01";
"""
    c = conf.parse_config_php(text)
    assert c['callbook'] == ['qrz', 'hamqth']
    assert c['qrz_password'] == "it's secret"
    assert c['table_name'] == 'TABLE_HRD_CONTACTS_V01'


def test_php_database_parsing():
    text = """<?php
|	['hostname'] The hostname of your database server.
$db['default'] = array(
	'dsn'	=> '',
	'hostname' => 'wavelog-db',
	'username' => 'wavelog',
	'password' => 'pa,ss',
	'database' => 'wavelog',
);"""
    d = conf.parse_database_php(text)
    assert d['hostname'] == 'wavelog-db' and d['username'] == 'wavelog' and d['database'] == 'wavelog'


def test_formatting():
    assert format_freq(14200000) == '14.200'
    assert format_freq(7074000) == '7.074'
    assert format_freq(14074500) == '14.0745'
    assert format_freq(None) == ''
    assert format_grid('ko13ae') == 'KO13ae'
    assert lookup_variants('SP9ABC/P') == ['SP9ABC/P', 'SP9ABC']
    assert lookup_variants('DL/SP9ABC') == ['DL/SP9ABC', 'SP9ABC']
    assert clean_email(' <Jan@Example.PL> ') == 'jan@example.pl'
    assert clean_email('not an address') == ''


def test_language(tmp_path):
    cfg = make_cfg(tmp_path)
    assert language_for(cfg, 'SQ4MK') == 'pl'
    assert language_for(cfg, 'DL1XYZ') == 'de'
    assert language_for(cfg, 'OE9MON') == 'de'
    assert language_for(cfg, 'JH1GEX') == 'en'
    assert language_for(make_cfg(tmp_path, mail={'language': 'en'}), 'SQ4MK') == 'en'


def test_generated_card(tmp_path):
    card = Card(make_cfg(tmp_path), STATIONS)
    assert card.mode == 'generated'
    jpg = card.render(qso())
    assert jpg[:2] == b'\xff\xd8' and len(jpg) > 50_000
    portable = card.qso_station(qso(station_id=2, station_callsign='SP4BL/P', my_grid='KO13BC', my_pota='PL-1234'))
    assert portable['callsign'] == 'SP4BL/P' and portable['grid'] == 'KO13BC' and portable['ref'] == 'POTA PL-1234'


def test_background_card(tmp_path):
    from PIL import Image
    Image.new('RGB', (800, 600), (40, 90, 140)).save(tmp_path / 'background.jpg')
    card = Card(make_cfg(tmp_path), STATIONS)
    assert card.mode == 'background'
    assert card.render(qso())[:2] == b'\xff\xd8'


def test_design_card_station_line(tmp_path):
    from PIL import Image
    Image.new('RGB', (1650, 1050), (20, 20, 40)).save(tmp_path / 'design.jpg')
    cfg = make_cfg(tmp_path, card={'image': 'design.jpg', 'printed_callsign': 'SP4BL', 'printed_grid': 'KO13ae'})
    card = Card(cfg, STATIONS)
    assert card.mode == 'design'
    assert card.station_line(qso()) == ''
    line = card.station_line(qso(station_id=2, station_callsign='SP4BL/P', my_grid='KO13BC', my_pota='PL-1234'))
    assert line == 'FROM SP4BL/P · KO13bc · POTA PL-1234'
    # without printed_* the first Station Location is what the design shows
    card = Card(make_cfg(tmp_path, card={'image': 'design.jpg'}), STATIONS)
    assert card.station_line(qso()) == ''
    assert card.station_line(qso(station_id=2, station_callsign='SP4BL/P', my_grid='KO13BC')) == 'FROM SP4BL/P · KO13bc'


def test_old_site_key(tmp_path):
    card = Card(make_cfg(tmp_path), STATIONS)
    cfg = make_cfg(tmp_path, mail={'site': 'https://sp4bl.pl'})
    msg, _ = build_message(cfg, card, [qso()], 'dl1xyz@example.de', 'SP4BL <qsl@sp4bl.pl>')
    assert 'https://sp4bl.pl' in msg.get_body(('plain',)).get_content()


def test_message_html_and_text(tmp_path):
    card = Card(make_cfg(tmp_path), STATIONS)
    cfg = make_cfg(tmp_path, mail={'website': 'https://sp4bl.pl'})
    qsos = [qso(), qso(id=2, band='40M', freq=7150000, time_on=dt.datetime(2026, 10, 28, 20, 30))]
    msg, cards = build_message(cfg, card, qsos, 'dl1xyz@example.de', 'Bartosz SP4BL <qsl@sp4bl.pl>')
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert parsed['Subject'] == 'SP4BL – QSL-Karte für unser QSO, DL1XYZ'
    types = [p.get_content_type() for p in parsed.walk()]
    assert types == ['multipart/alternative', 'text/plain', 'multipart/related', 'text/html', 'image/jpeg', 'image/jpeg']
    text = parsed.get_body(('plain',)).get_content()
    assert 'Hallo Hans' in text and '14.205 MHz' in text and 'https://sp4bl.pl' in text
    page = preview_html(msg, cards)
    assert 'cid:' not in page and cards[0][0] in page and 'Bartosz, SP4BL' in page and 'Łomża · KO13ae · Poland' in page


def test_plain_mail_with_attachments(tmp_path):
    card = Card(make_cfg(tmp_path), STATIONS)
    cfg = make_cfg(tmp_path, mail={'html': 'false', 'language': 'en'})
    msg, _ = build_message(cfg, card, [qso()], 'dl1xyz@example.de', 'SP4BL <qsl@sp4bl.pl>')
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ['SP4BL_DL1XYZ_20261028_1952_20m.jpg']


def test_fill_does_not_cache_a_miss_when_a_callbook_fails(tmp_path, monkeypatch):
    from qsl_mailer import callbook, cli
    from qsl_mailer.state import State

    class Down:
        name = 'qrz'

        def __init__(self, user, password):
            pass

        def email(self, call):
            raise OSError('timed out')

    class Wl:
        def calls_without_email(self):
            return ['SQ4MK']

    monkeypatch.setitem(callbook.PROVIDERS, 'qrz', Down)
    monkeypatch.setattr(cli.time, 'sleep', lambda s: None)
    cfg = make_cfg(tmp_path, callbook={'order': 'qrz', 'qrz_user': 'u', 'qrz_password': 'p'})
    state = State(tmp_path / 'state.sqlite')
    cli.fill(cfg, {'config': {}, 'db': {}}, Wl(), state, dry_run=False)
    assert state.cached('SQ4MK', 30) is None  # asked again next run, not skipped for 30 days
