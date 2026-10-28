"""config.ini plus settings read from Wavelog's own config.php / database.php."""

import configparser
import os
import re
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(os.environ.get('QSL_MAILER_CONFIG', 'config.ini'))


def load(path):
    cfg = configparser.ConfigParser(interpolation=None)
    if not cfg.read(path, encoding='utf-8'):
        sys.exit(f'Config file not found: {path} (copy config.example.ini to config.ini)')
    cfg.path = Path(path).resolve()
    return cfg


def get(cfg, section, key, fallback=None):
    """Value from config.ini; empty or missing means fallback."""
    value = cfg.get(section, key, fallback='') if cfg.has_section(section) else ''
    return value if value != '' else fallback


def get_bool(cfg, section, key, fallback=False):
    value = get(cfg, section, key)
    return fallback if value is None else value.strip().lower() in ('1', 'true', 'yes', 'on')


def csv_list(value):
    return [v.strip() for v in (value or '').split(',') if v.strip()]


def resolve(cfg, path):
    """Relative paths in config.ini are relative to the config file's directory."""
    p = Path(path)
    return p if p.is_absolute() else cfg.path.parent / p


# --- Wavelog's PHP config files (parsed, never executed) ---

PHP_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")


def php_value(raw):
    raw = raw.strip()
    if raw.lower().startswith('array(') or raw.startswith('['):
        return [php_value(m.group(0)) for m in PHP_STRING.finditer(raw)]
    m = PHP_STRING.fullmatch(raw)
    if m:
        return re.sub(r'\\(.)', r'\1', m.group(1) if m.group(1) is not None else m.group(2))
    if raw.lower() in ('true', 'false'):
        return raw.lower() == 'true'
    return raw


def parse_config_php(text):
    """Simple `$config['key'] = value;` assignments; comment blocks are skipped."""
    out = {}
    for m in re.finditer(r"^\s*\$config\[['\"](\w+)['\"]\]\s*=\s*(.+?);\s*(?://.*)?$", text, re.M):
        out[m.group(1)] = php_value(m.group(2))
    return out


def parse_database_php(text):
    """Connection settings of the 'default' group, array or per-key style."""
    out = {}
    text = text[max(text.find("$db['default']"), 0):]
    for m in re.finditer(r"['\"](hostname|username|password|database|port)['\"]\s*(?:\]\s*=|=>)\s*([^,;\n]+)", text):
        out.setdefault(m.group(1), php_value(m.group(2)))
    return out


def wavelog_files(cfg):
    """{'config': {...}, 'db': {...}} from [wavelog] config_dir, or empty dicts."""
    out = {'config': {}, 'db': {}}
    config_dir = get(cfg, 'wavelog', 'config_dir')
    if not config_dir:
        return out
    d = resolve(cfg, config_dir)
    if not (d / 'config.php').exists() and (d / 'docker' / 'config.php').exists():
        d = d / 'docker'  # whole application/config mounted instead of the docker subfolder
    if not (d / 'config.php').exists():
        sys.exit(f'[wavelog] config_dir: {d / "config.php"} not found')
    out['config'] = parse_config_php((d / 'config.php').read_text(encoding='utf-8', errors='replace'))
    if (d / 'database.php').exists():
        out['db'] = parse_database_php((d / 'database.php').read_text(encoding='utf-8', errors='replace'))
    return out
