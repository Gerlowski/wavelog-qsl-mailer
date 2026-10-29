"""Command line: fill e-mails, send cards, preview, resend, loop."""

import argparse
import datetime as dt
import fcntl
import smtplib
import sys
import time
from pathlib import Path

from . import __version__, callbook, config as conf, mail
from .card import Card
from .state import State
from .wavelog import Wavelog

CARDS_PER_MAIL = 10


def log(msg):
    print(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}', flush=True)


def fill(cfg, files, wl, state, dry_run):
    books = callbook.configured(cfg, files)
    if not books:
        log('fill: no QRZ.com/HamQTH login (Wavelog config.php or [callbook]), skipping')
        return
    retry_days = int(conf.get(cfg, 'callbook', 'retry_days', 30))
    skip = {c.upper() for c in conf.csv_list(conf.get(cfg, 'qso', 'skip_calls'))}
    calls = [c for c in wl.calls_without_email(state) if c not in skip]
    log(f'fill: {len(calls)} callsigns without e-mail, callbooks: {", ".join(b.name for b in books)}')
    found = 0
    for callsign in calls:
        email = state.cached(callsign, retry_days)
        if email is None:
            email, source, failed = '', '', False
            for variant in callbook.lookup_variants(callsign):
                for book in books:
                    try:
                        email = callbook.clean_email(book.email(variant))
                    except Exception as e:  # one callbook down: try the next one
                        log(f'fill: {book.name} {variant}: {e}')
                        failed = True
                        continue
                    if email:
                        source = book.name
                        break
                if email:
                    break
                time.sleep(0.5)
            if not dry_run and (email or not failed):  # a miss counts only if every callbook answered
                state.remember(callsign, email, source)
        if email:
            found += 1
            if dry_run:
                log(f'fill: {callsign} -> {email} (dry run, not saved)')
            else:
                log(f'fill: {callsign} -> {email} ({wl.set_email(callsign, email)} QSO)')
    log(f'fill: found e-mails for {found} callsigns')


def send(cfg, wl, state, dry_run, preview_dir, resend=None):
    skip_calls = {c.upper() for c in conf.csv_list(conf.get(cfg, 'qso', 'skip_calls'))}
    skip_emails = {e.lower() for e in conf.csv_list(conf.get(cfg, 'qso', 'skip_emails'))}
    mark = conf.get_bool(cfg, 'qso', 'mark_qsl_sent', False)
    limit = int(conf.get(cfg, 'mail', 'max_per_run', 20))
    delay = float(conf.get(cfg, 'mail', 'delay_seconds', 5))

    # one mail per contacted station, e-mail and own callsign, with all its new QSOs
    groups = {}
    for qso in (wl.qsos_for_call(resend) if resend else [q for q in wl.new_qsos(state) if q['email']]):
        email = callbook.clean_email(qso['email'])
        if not email or (not resend and (email in skip_emails or qso['callsign'] in skip_calls
                                         or state.is_sent(qso['key']))):
            continue
        groups.setdefault((qso['callsign'], email, qso['station_callsign']), []).append(qso)
    if not groups:
        log('send: nothing new' if not resend else f'send: no QSO with an e-mail for {resend}')
        return

    s = mail.settings(cfg, wl)
    log(f'send: {sum(len(q) for q in groups.values())} QSO to confirm ({len(groups)} mails), '
        f'SMTP {s["host"] or "?"}:{s["port"]} ({s["security"]}), from {s["sender"] or "?"}')
    if not s['sender']:
        raise RuntimeError('no sender address: set it in Wavelog (Admin -> Global Options -> Email) or [mail] from')
    if not dry_run and not s['host']:
        raise RuntimeError('no SMTP server: choose SMTP in Wavelog (Admin -> Global Options -> Email) or fill in [smtp]')

    card = Card(cfg, wl.stations)
    mailer = None if dry_run else mail.Mailer(s)
    sent = 0
    try:
        for (callsign, email, _), qsos in groups.items():
            if sent >= limit:
                log(f'send: limit of {limit} mails per run reached, the rest goes in the next run')
                break
            qsos = qsos[:CARDS_PER_MAIL]
            msg, cards = mail.build_message(cfg, card, qsos, email, s['sender'])
            if dry_run:
                preview_dir.mkdir(parents=True, exist_ok=True)
                for fname, data in cards:
                    (preview_dir / fname).write_bytes(data)
                page = mail.preview_html(msg, cards)
                if page:
                    (preview_dir / (cards[0][0][:-4] + '.html')).write_text(page, encoding='utf-8')
                log(f'send (dry run): {callsign} <{email}> {len(qsos)} QSO -> {preview_dir}')
                sent += 1
                continue
            try:
                mailer.send(msg)
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
                log(f'send: {callsign} <{email}> refused: {e}')
                continue
            state.mark_sent(qsos, email)
            if mark:
                for qso in qsos:
                    wl.mark_qsl_sent(qso)
            sent += 1
            log(f'send: {callsign} <{email}> {len(qsos)} QSO')
            time.sleep(delay)
    finally:
        if mailer:
            mailer.close()
    log(f'send: {"prepared (dry run, nothing sent)" if dry_run else "sent"} {sent} mails')


def run(args):
    cfg = conf.load(args.config)
    state_path = conf.resolve(cfg, conf.get(cfg, 'state', 'path', 'state.sqlite'))
    preview = Path(args.preview) if args.preview else conf.resolve(cfg, 'preview')
    with open(state_path.with_suffix('.lock'), 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('previous run still in progress, skipping')
            return
        files = conf.wavelog_files(cfg)
        wl, state = Wavelog(cfg, files), State(state_path)
        try:
            migrated = state.migrate(wl.qso_keys)
            if migrated:
                log(f'state: {migrated} sent QSOs carried over from version 1.1')
            log(f'run{" (dry run)" if args.dry_run else ""}: API {wl.api_url}, database {wl.host}, '
                f'stations {",".join(map(str, wl.station_ids))}, QSOs since {wl.since:%Y-%m-%d}')
            if args.command == 'resend':
                send(cfg, wl, state, args.dry_run, preview, resend=args.callsign)
            if args.command in ('run', 'fill'):
                fill(cfg, files, wl, state, args.dry_run)
            if args.command in ('run', 'send'):
                send(cfg, wl, state, args.dry_run, preview)
            if args.command == 'card':
                card_preview(cfg, wl, preview, args.callsign)
        finally:
            wl.close()


def card_preview(cfg, wl, preview, callsign):
    """A sample card per configured Station Location, without touching any QSO."""
    card = Card(cfg, wl.stations)
    preview.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    for sid, st in wl.stations.items():
        qso = {'callsign': (callsign or 'N0CALL').upper(), 'time_on': now, 'band': '20m', 'mode': 'SSB',
               'submode': 'USB', 'freq': 14205000, 'rst_sent': '59', 'station_id': sid,
               'station_callsign': st['callsign'], 'my_grid': st['grid'], 'my_pota': st.get('pota')}
        path = preview / f"sample_{st['callsign'].replace('/', '-')}.jpg"
        path.write_bytes(card.render(qso))
        log(f'card: {path}')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='qsl-mailer', description='QSL cards by e-mail for Wavelog')
    ap.add_argument('command', nargs='?', default='run', choices=['run', 'fill', 'send', 'resend', 'card'],
                    help='run = fill + send (default); resend CALL = send all cards for CALL again; '
                         'card [CALL] = sample cards to the preview folder')
    ap.add_argument('callsign', nargs='?', help='callsign for resend / card')
    ap.add_argument('-c', '--config', default=str(conf.DEFAULT_CONFIG))
    ap.add_argument('--dry-run', action='store_true', help='no database writes, no mail; cards and mails to --preview')
    ap.add_argument('--preview', help='preview folder (default: preview/ next to config.ini)')
    ap.add_argument('--loop', type=int, metavar='SECONDS', help='repeat every SECONDS (container / service mode)')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = ap.parse_args(argv)
    if args.command == 'resend' and not args.callsign:
        ap.error('resend needs a callsign, e.g. resend SP9ABC')

    if not args.loop:
        run(args)
        return
    log(f'qsl-mailer {__version__}: running every {args.loop} s')
    while True:
        try:
            run(args)  # config.ini and Wavelog settings are re-read on every run
        except SystemExit as e:  # configuration problem: show it, keep the container alive
            log(f'config: {e}')
        except Exception as e:  # e.g. database briefly down while Wavelog restarts
            log(f'error: {type(e).__name__}: {e}')
        time.sleep(args.loop)


if __name__ == '__main__':
    sys.exit(main())
