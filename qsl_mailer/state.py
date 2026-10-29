"""The mailer's own SQLite state: which QSOs got a card, callbook results, API read position."""

import datetime as dt
import sqlite3

from .wavelog import utcnow


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sent_qso (
                qso_key TEXT PRIMARY KEY, callsign TEXT, email TEXT, sent_at TEXT);
            CREATE TABLE IF NOT EXISTS callbook (
                callsign TEXT PRIMARY KEY, email TEXT, source TEXT, checked_at TEXT);
            CREATE TABLE IF NOT EXISTS api_cursor (
                station_id INTEGER, since TEXT, last_id INTEGER, PRIMARY KEY (station_id, since));
        ''')

    def migrate(self, qso_keys):
        """Version 1.1 kept Wavelog QSO IDs in `sent`; qso_keys maps them to QSO keys."""
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sent'").fetchone():
            return 0
        rows = self.db.execute('SELECT qso_id, callsign, email, sent_at FROM sent').fetchall()
        keys = qso_keys([r[0] for r in rows]) if rows else {}
        self.db.executemany('INSERT OR IGNORE INTO sent_qso VALUES (?, ?, ?, ?)',
                            [(keys[r[0]], *r[1:]) for r in rows if r[0] in keys])
        self.db.execute('DROP TABLE sent')
        self.db.commit()
        return len(keys)

    def is_sent(self, qso_key):
        return self.db.execute('SELECT 1 FROM sent_qso WHERE qso_key = ?', (qso_key,)).fetchone() is not None

    def mark_sent(self, qsos, email):
        now = utcnow().isoformat(timespec='seconds')
        self.db.executemany('INSERT OR REPLACE INTO sent_qso VALUES (?, ?, ?, ?)',
                            [(q['key'], q['callsign'], email, now) for q in qsos])
        self.db.commit()

    def cursor(self, station_id, since):
        row = self.db.execute('SELECT last_id FROM api_cursor WHERE station_id = ? AND since = ?',
                              (station_id, since.isoformat())).fetchone()
        return row[0] if row else 0

    def set_cursor(self, station_id, since, last_id):
        self.db.execute('INSERT OR REPLACE INTO api_cursor VALUES (?, ?, ?)', (station_id, since.isoformat(), last_id))
        self.db.commit()

    def cached(self, callsign, retry_days):
        """Cached e-mail, '' for a recent miss, None when the callbook should be asked."""
        row = self.db.execute('SELECT email, checked_at FROM callbook WHERE callsign = ?', (callsign,)).fetchone()
        if row is None:
            return None
        email, checked = row
        if email or dt.datetime.fromisoformat(checked) > utcnow() - dt.timedelta(days=retry_days):
            return email or ''
        return None

    def remember(self, callsign, email, source):
        self.db.execute('INSERT OR REPLACE INTO callbook VALUES (?, ?, ?, ?)',
                        (callsign, email or '', source, utcnow().isoformat(timespec='seconds')))
        self.db.commit()
