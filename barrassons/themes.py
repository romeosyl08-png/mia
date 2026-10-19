"""Big Persona appearance in the terminal; no conversation or memory is fetched."""
ACCENTS = {
    'violet': '#6C6FCB', 'ambre': '#E8A33D', 'sarcelle': '#3F8E8A',
    'brique': '#B4483A', 'vert': '#4E9F3D',
}
THEMES = {'clair': 'light', 'sombre': 'dark', 'light': 'light', 'dark': 'dark'}


def apply_theme(config, request):
    from .terminal import terminal
    theme = config.get('theme', 'persona')
    accent = config.get('accent', '#6C6FCB')
    synced = True
    if theme == 'persona':
        try:
            appearance = request(config, '/appearance', timeout=5)
            theme = appearance.get('theme', 'light')
            accent = appearance.get('accent', '#6C6FCB')
        except RuntimeError:
            theme, accent, synced = 'light', '#6C6FCB', False
    terminal.set_theme(theme, accent)
    return synced


def configure_theme(config, choice, request, save):
    from .terminal import terminal
    choice = choice.strip().casefold()
    if not choice:
        current = 'Big Persona' if config.get('theme', 'persona') == 'persona' else (
            'clair' if terminal.theme == 'light' else 'sombre')
        terminal.panel('Thème · ' + current, [
            '1. Big Persona · reprendre son apparence', '2. Clair', '3. Sombre',
            '', 'Couleurs : violet · ambre · sarcelle · brique · vert',
            'Choisis un thème ou une couleur. Entrée pour garder.',
        ], wrap=True)
        choice = terminal.prompt('Thème ❯ ').strip().casefold()
        if not choice:
            return
    choice = {'1': 'persona', '2': 'clair', '3': 'sombre', 'big persona': 'persona'}.get(choice, choice)
    updated = dict(config)
    if choice == 'persona':
        updated['theme'] = 'persona'
    elif choice in THEMES:
        updated.update(theme=THEMES[choice], accent=terminal.accent)
    elif choice in ACCENTS:
        updated.update(theme=terminal.theme, accent=ACCENTS[choice])
    else:
        raise ValueError('Choisis persona, clair, sombre ou une couleur : ' + ', '.join(ACCENTS) + '.')
    synced = apply_theme(updated, request)
    save(updated)
    config.update(updated)
    terminal.line('Thème enregistré.', 'green')
    if not synced:
        terminal.line('Apparence Big Persona indisponible ; thème clair violet utilisé. Réessaie avec /theme persona.', 'muted')
