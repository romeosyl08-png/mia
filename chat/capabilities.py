"""Conservative, local capability checks. Never call a model to judge a request."""
import json
from pathlib import Path
import re
import unicodedata

from django.conf import settings


RULES = json.loads(Path(__file__).with_name('capability_rules.json').read_text(encoding='utf-8'))


def normalize_request(content):
    return ''.join(c for c in unicodedata.normalize('NFD', content.strip().lower()) if not unicodedata.combining(c)).replace('’', "'")


def filename_matches(filename, reference):
    return filename == reference or filename.endswith('/' + reference) or filename.endswith(' ' + reference)


def request_steps(content):
    text = normalize_request(content)[:4000]
    # Only inspect leading instructions. A quoted document, pasted code or the
    # body following a colon is data, even when it contains imperative verbs.
    for line in text.split('\n')[:16]:
        line = re.sub(RULES['listPrefix'], '', line.strip())
        line = re.sub(RULES['prefix'], '', line, count=1).strip()
        if not line:
            continue
        if re.search(RULES['dataStart'], line):
            break
        for clause in re.split(RULES['clauseSeparator'], line):
            clause = re.sub(RULES['prefix'], '', clause.strip(), count=1)
            found = {name for name, pattern in RULES['rules'].items() if re.search(pattern, clause)}
            if re.search(RULES['inlineData'], clause) and ('\n' in text or clause.split(':', 1)[-1].strip()):
                found = {name for name in found if not name.startswith('file_')}
            yield clause, found
        if ':' in line or not found:
            break


def request_requirements(content):
    return {name for _, names in request_steps(content) for name in names}


def generation_kind(content, mode='chat'):
    if mode in {'image', 'video', 'audio'}:
        return mode
    requirements = request_requirements(content)
    if len(requirements) == 1:
        return next(iter(requirements & {'image', 'video', 'audio'}), None)
    return None


def requires_tools(content, mode='chat'):
    # Reading and web search can be prepared by 2AI without model tool calls.
    return bool(request_requirements(content) - {'file_read', 'web'})


def supports_tools(model):
    if 'anthropic' in f'{model.provider.slug} {model.provider.name}'.lower():
        return False  # This site's direct Anthropic adapter does not expose tools.
    parameters = model.supported_parameters
    return 'tools' in parameters if isinstance(parameters, list) else None


def supports_images(model):
    if 'anthropic' in f'{model.provider.slug} {model.provider.name}'.lower():
        return False
    modalities = model.input_modalities
    return 'image' in modalities if isinstance(modalities, list) and modalities else None


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
    needs_tools = requires_tools(content, mode)
    if team and (workspace or needs_tools or has_images):
        return issue('team')
    if not auto:
        if has_images and any(supports_images(model) is False for model in models):
            return issue('vision')
        if has_images and any(supports_images(model) is None for model in models):
            return issue('vision_unknown')
        if needs_tools and any(supports_tools(model) is False for model in models):
            return issue('tools')
    return None
