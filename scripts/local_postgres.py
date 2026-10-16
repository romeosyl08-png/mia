"""Manage a user-owned PostgreSQL instance for local 2AI development."""

import argparse
from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from urllib.parse import quote


PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / '.local' / 'postgresql'
DATA = STATE / 'data'
SOCKET = STATE / 'socket'
PORT = 55432
DATABASE = 'ai_2ai'
ROLE = 'ai2ai'


def binaries():
    version_file = DATA / 'PG_VERSION'
    installed = list(Path('/usr/lib/postgresql').glob('*/bin'))
    if version_file.exists():
        version = version_file.read_text().strip()
        installed = [path for path in installed if path.parent.name == version]
    installed = sorted(installed, key=lambda path: int(path.parent.name), reverse=True)
    if not installed:
        raise SystemExit('Installe PostgreSQL localement avant de lancer cette commande.')
    return installed[0]


def run(command, **options):
    return subprocess.run([str(part) for part in command], check=True, **options)


def start(binary):
    if not (DATA / 'PG_VERSION').exists():
        raise SystemExit('Initialise la base avec : python scripts/local_postgres.py setup')
    if subprocess.run([str(binary / 'pg_ctl'), '-D', str(DATA), 'status'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        print('PostgreSQL local est déjà démarré.')
        return
    SOCKET.mkdir(mode=0o700, parents=True, exist_ok=True)
    run([binary / 'pg_ctl', '-D', DATA, '-l', STATE / 'postgresql.log', '-w', '-t', '30', 'start'])


def setup(binary):
    import psycopg
    from psycopg import sql

    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    SOCKET.mkdir(mode=0o700, exist_ok=True)
    if not (DATA / 'PG_VERSION').exists():
        with tempfile.NamedTemporaryFile(mode='w', dir=STATE, prefix='.init-password-') as password_file:
            password_file.write(secrets.token_urlsafe(32))
            password_file.flush()
            run([binary / 'initdb', '-D', DATA, '--encoding=UTF8', '--locale=C.UTF-8',
                 '--auth-local=peer', '--auth-host=scram-sha-256', '--pwfile', password_file.name])
        socket_path = str(SOCKET).replace("'", "''")
        with (DATA / 'postgresql.conf').open('a') as config:
            config.write(f"\n# Instance locale dédiée à 2AI\nlisten_addresses = '127.0.0.1'\n"
                         f"port = {PORT}\nunix_socket_directories = '{socket_path}'\n"
                         "password_encryption = 'scram-sha-256'\nssl = off\n")
    start(binary)
    password = secrets.token_urlsafe(32)
    with psycopg.connect(host=str(SOCKET), port=PORT, dbname='postgres', user=getpass.getuser(),
                        connect_timeout=5, autocommit=True) as connection:
        exists = connection.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (ROLE,)).fetchone()
        action = 'ALTER' if exists else 'CREATE'
        connection.execute(sql.SQL(action + ' ROLE {} WITH LOGIN NOSUPERUSER CREATEDB NOCREATEROLE PASSWORD {}')
                           .format(sql.Identifier(ROLE), sql.Literal(password)))
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname = %s', (DATABASE,)).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
                sql.Identifier(DATABASE), sql.Identifier(ROLE)))

    url = f'postgresql://{ROLE}:{quote(password, safe="")}@127.0.0.1:{PORT}/{DATABASE}?sslmode=disable'
    with psycopg.connect(url, connect_timeout=5) as connection:
        connection.execute('SELECT 1').fetchone()
    env_path = PROJECT / '.env'
    original = env_path.read_text() if env_path.exists() else ''
    if original:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        backup = PROJECT / f'.env.before-local-postgres-{stamp}'
        with open(backup, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(original)
    updated = original
    for key, value in {'DATABASE_URL': url, 'LOG_LEVEL': 'INFO'}.items():
        pattern = rf'^(?:export\s+)?{key}\s*=.*$'
        if re.search(pattern, updated, flags=re.MULTILINE):
            updated = re.sub(pattern, lambda match: f'{key}={value}', updated, flags=re.MULTILINE)
        else:
            updated = updated.rstrip('\n') + f'\n{key}={value}\n'
    # Atomic replacement keeps unrelated keys and never prints connection secrets.
    with tempfile.NamedTemporaryFile(mode='w', dir=PROJECT, prefix='.env.local-postgres-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(updated)
    try:
        temporary.replace(env_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Base {DATABASE} prête sur 127.0.0.1:{PORT}. Connexion enregistrée dans .env.')
    print('Configuration précédente sauvegardée ; LOG_LEVEL=INFO activé.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'start', 'stop', 'status'])
    args = parser.parse_args()
    binary = binaries()
    if args.action == 'setup':
        setup(binary)
    elif args.action == 'start':
        start(binary)
    elif args.action == 'stop':
        run([binary / 'pg_ctl', '-D', DATA, '-w', '-m', 'fast', 'stop'])
    else:
        raise SystemExit(subprocess.run([str(binary / 'pg_ctl'), '-D', str(DATA), 'status']).returncode)


if __name__ == '__main__':
    main()
