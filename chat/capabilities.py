"""Conservative, local capability checks. Never call a model to judge a request."""
import json
from pathlib import Path
import re
import unicodedata

from django.conf import settings


RULES = json.loads(Path(__file__).with_name('capability_rules.json').read_text(encoding='utf-8'))


def request_requirements(content):
    # Only classify a direct request on its first line; quoted documents and
    # requests for explanations, prompts or code must remain ordinary text.
    text = ''.join(c for c in unicodedata.normalize('NFD', content.strip().lower()) if not unicodedata.combining(c))
    text = text.replace('’', "'").split('\n', 1)[0][:600]
    text = re.sub(RULES['prefix'], '', text, count=1)
    requirements = {name for name, pattern in RULES['rules'].items() if re.search(pattern, text)}
    if '\n' in content.strip():
        requirements = {name for name in requirements if not name.startswith('file_')}
    return requirements


def supports_tools(model):
    if 'anthropic' in f'{model.provider.slug} {model.provider.name}'.lower():
        return False  # This site's direct Anthropic adapter does not expose tools.
    parameters = model.supported_parameters
    return 'tools' in parameters if isinstance(parameters, list) else None


def supports_images(model):
    return 'image' in model.input_modalities and 'anthropic' not in f'{model.provider.slug} {model.provider.name}'.lower()


def model_capabilities(model):
    return {'name': model.name, 'tools': supports_tools(model), 'images': supports_images(model)}


def issue(code, message=None):
    return {'code': code, 'message': (message or RULES['messages'][code]) + ' ' + RULES['messages']['no_charge']}


def capability_issue(content, mode, models, workspace=None, has_images=False, team=False, auto=False):
    requirements = request_requirements(content)
    for kind in ('image', 'video', 'audio'):
        if kind in requirements:
            return issue('media_mode_required', RULES['mediaMessages'][kind])
    disabled = set(workspace.disabled_tools or []) if workspace else set()
    web = mode == 'web' or 'web' in requirements
    if web and (not settings.SEARCH_API_KEY or 'web_search' in disabled):
        return issue('web')
    if 'execute' in requirements and (
        not workspace or not settings.BARA_CODE_EXECUTION_ENABLED
        or {'run_python', 'run_command'} <= disabled
    ):
        return issue('execute')
    file_requirements = {name for name in requirements if name.startswith('file_')}
    if file_requirements and not workspace:
        return issue('workspace')
    if any(set(RULES['toolRequirements'][name]) <= disabled for name in file_requirements):
        return issue('file_tool')
    needs_tools = bool(workspace) or 'execute' in requirements or ('web' in requirements and mode != 'web')
    if team and (needs_tools or has_images):
        return issue('team')
    if not auto:
        if has_images and any(not supports_images(model) for model in models):
            return issue('vision')
        if needs_tools and any(supports_tools(model) is False for model in models):
            return issue('tools')
    return None
