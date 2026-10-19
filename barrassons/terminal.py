"""Compact terminal presentation, with a plain output mode for logs and pipes."""
import itertools
import json
import os
import re
import shutil
import sys
import textwrap
import threading
from contextlib import contextmanager
try:
    import readline
except ImportError:
    readline = None


ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def clean(value):
    return CONTROL.sub('', ANSI.sub('', str(value)))


class Terminal:
    def __init__(self):
        self.set_theme('light', '#6C6FCB')

    def set_theme(self, theme, accent):
        from .themes import ACCENTS
        self.theme = theme if theme in {'light', 'dark'} else 'light'
        self.accent = accent if accent in ACCENTS.values() else '#6C6FCB'
        dark = self.theme == 'dark'
        self.background = '#191c28' if dark else '#ffffff'
        self.colors = {
            'text': '#eeeff7' if dark else '#202332',
            'muted': '#a5abc0' if dark else '#656b7e',
            'accent': self.accent, 'green': '#79c96b' if dark else '#33782b',
            'red': '#f08b7c' if dark else '#B4483A',
            'code': '#79ceca' if dark else '#31736f',
        }

    @staticmethod
    def rgb(hex_color):
        return ';'.join(str(int(hex_color[index:index + 2], 16)) for index in (1, 3, 5))

    @property
    def colored(self):
        return sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ

    @property
    def width(self):
        return max(24, min(96, shutil.get_terminal_size((88, 24)).columns))

    def style(self, value, color):
        value = clean(value)
        if not self.colored:
            return value
        foreground = self.colors.get(color, self.colors['text'])
        bold = '1;' if color == 'bold' else ''
        return f'\033[{bold}38;2;{self.rgb(foreground)};48;2;{self.rgb(self.background)}m{value}\033[0m'

    def line(self, value='', color=None):
        print(self.style(value, color or 'text'))

    def clip(self, value, width=None):
        value = clean(value).replace('\n', ' ').replace('\t', ' ')
        width = max(1, width or self.width)
        return value if len(value) <= width else value[:max(0, width - 1)] + '…'

    def panel(self, title, rows, wrap=False):
        width = self.width - 2
        label = self.clip(title, width - 5)
        self.line('╭─ ' + label + ' ' + '─' * max(0, width - len(label) - 3) + '╮', 'accent')
        for row in rows:
            lines = textwrap.wrap(clean(row), width - 2, replace_whitespace=False, drop_whitespace=False) if wrap else [self.clip(row, width - 2)]
            for text in lines or ['']:
                self.line('│ ' + text.ljust(width - 2) + ' │', 'muted')
        self.line('╰' + '─' * width + '╯', 'accent')

    def welcome(self, root, model, branch, endpoint):
        self.line()
        self.line('✦ Barrassons', 'accent')
        self.line(self.clip(f'{root} · {model}'), 'muted')
        self.line('Décris ce que tu veux faire. /help pour les commandes.', 'muted')
        self.line()

    def setup_input(self):
        if readline is None or not sys.stdin.isatty():
            return
        commands = ['/help', '/model', '/theme', '/status', '/verbose', '/diff', '/undo', '/verify', '/review',
                    '/memory', '/remember', '/clear', '/exit']
        def complete(text, state):
            options = [value for value in commands if value.startswith(text)]
            return options[state] if state < len(options) else None
        readline.set_completer(complete)
        readline.parse_and_bind('tab: complete')
        readline.set_auto_history(False)

    def prompt(self, label='❯ ', continuation=False):
        prompt = self.style(label, 'muted' if continuation else 'accent')
        if self.colored:
            # GNU readline needs the zero-width escape sequences marked explicitly.
            prompt = re.sub(r'(\x1b\[[0-9;]*m)', '\x01' + r'\1' + '\x02', prompt)
        return input(prompt)

    def request(self):
        self.line('─' * self.width, 'muted')
        lines = [self.prompt()]
        while lines[-1].endswith('\\'):
            lines[-1] = lines[-1][:-1]
            lines.append(self.prompt('  … ', continuation=True))
        request = '\n'.join(lines).strip()
        if request and readline is not None and sys.stdin.isatty():
            readline.add_history(request)
        return request

    def status(self, model, tokens=0, credits=0, balance=None):
        parts = [model]
        if tokens:
            parts.append(f'{tokens:,} jetons de texte'.replace(',', ' '))
        if credits:
            parts.append(f'{credits:,} jetons utilisés'.replace(',', ' '))
        if balance is not None:
            parts.append(f'{balance:,} jetons disponibles'.replace(',', ' '))
        self.line('  ' + self.clip(' · '.join(parts), self.width - 2), 'muted')

    @contextmanager
    def busy(self, label):
        if not self.colored:
            self.line(f'✦ {label}', 'muted')
            yield
            return
        stopped = threading.Event()
        def animate():
            for symbol in itertools.cycle(['✦', '✧', '✶', '✧']):
                sys.stdout.write('\r' + self.style(f'{symbol} {label}', 'accent'))
                sys.stdout.flush()
                if stopped.wait(.18):
                    break
        worker = threading.Thread(target=animate, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set(); worker.join(timeout=1)
            sys.stdout.write('\r\033[2K'); sys.stdout.flush()

    def assistant(self, value):
        self.line('\n● Barrassons', 'accent')
        code = False
        for line in clean(value).splitlines():
            if line.startswith('```'):
                code = not code
                self.line('  ' + ('┌ ' + line[3:] if code else '└'), 'muted')
            elif code:
                self.line('  │ ' + line, 'code')
            else:
                heading = line.startswith('#')
                text = line.lstrip('#').strip() if heading else line
                for wrapped in textwrap.wrap(text, max(16, self.width - 4), replace_whitespace=False) or ['']:
                    self.line('  ' + wrapped, 'bold' if heading else None)
        self.line()

    def diff(self, value):
        for line in clean(value).splitlines():
            color = 'green' if line.startswith('+') else 'red' if line.startswith('-') else 'muted' if line.startswith('@@') else None
            self.line(line, color)

    def tool(self, name, args):
        title = {'list_files':'Lister les fichiers', 'read_file':'Lire un fichier', 'search_code':'Rechercher',
                 'write_file':'Créer un fichier', 'edit_file':'Modifier un fichier', 'run_command':'Exécuter une commande'}.get(name, name)
        target = args.get('path') or args.get('query') or args.get('command') or ''
        self.line('\n● ' + title + (' · ' + self.clip(target, self.width - len(title) - 7) if target else ''), 'accent')

    def result(self, value, verbose=False):
        if verbose:
            self.line(json.dumps(value, ensure_ascii=False, indent=2))
            return
        if value.get('error'):
            self.line('  ⎿ ' + value['error'], 'red')
        elif 'exit_code' in value:
            self.line(f'  ⎿ Code de sortie : {value["exit_code"]}', 'green' if value['exit_code'] == 0 else 'red')
            for key in ['stdout', 'stderr']:
                lines = clean(value.get(key, '')).splitlines()
                for line in lines[-12:]:
                    self.line('    ' + line, 'muted')
                if len(lines) > 12:
                    self.line(f'    … {len(lines) - 12} lignes précédentes dans la session', 'muted')
        else:
            if 'files' in value:
                text = f'{len(value["files"])} fichier(s)'
            elif 'matches' in value:
                text = f'{len(value["matches"])} résultat(s)'
            elif 'content' in value:
                text = f'{len(value["content"].splitlines())} ligne(s) lues'
            else:
                text = {'written':'Fichier créé', 'updated':'Fichier modifié', 'refused':'Action refusée',
                        'reverted':'Modification annulée'}.get(value.get('status'), 'Terminé')
            self.line('  ⎿ ' + text, 'muted')


terminal = Terminal()
