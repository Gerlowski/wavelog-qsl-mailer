"""The mailer's own SQLite state: which QSOs got a card, and callbook results."""

import datetime as dt
import sqlite3

from .wavelog import utcnow


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sent (
                qso_id INTEGER PRIMARY KEY, callsign TEXT, email TEXT, sent_at TEXT);
            CREATE TABLE IF NOT EXISTS callbook (
                callsign TEXT PRIMARY KEY, email TEXT, source TEXT, checked_at TEXT);
        ''')

    def is_sent(self, qso_id):
        return self.db.execute('SELECT 1 FROM sent WHERE qso_id = ?', (qso_id,)).fetchone() is not None

    def mark_sent(self, qsos, email):
        now = utcnow().isoformat(timespec='seconds')
        self.db.executemany('INSERT OR REPLACE INTO sent VALUES (?, ?, ?, ?)',
                            [(q['id'], q['callsign'], email, now) for q in qsos])
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
