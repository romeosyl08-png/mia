"""Read public ChatGPT shares without executing scripts or using account cookies."""
import json
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from bs4 import BeautifulSoup

SHARE_HOSTS = {'chatgpt.com', 'www.chatgpt.com', 'chat.openai.com'}
MAX_NODES = 40000
MAX_MESSAGES = 500


class SharedLinkError(ValueError):
    pass


def canonical_share_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise SharedLinkError('Lien ChatGPT invalide.')
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme != 'https' or parsed.hostname not in SHARE_HOSTS or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            raise ValueError
        match = re.fullmatch(r'/share/([0-9a-fA-F-]{36})/?', parsed.path)
        if not match:
            raise ValueError
        share_id = str(UUID(match.group(1)))
    except (ValueError, AttributeError) as error:
        raise SharedLinkError('Utilise un lien public https://chatgpt.com/share/… valide.') from error
    return urlunsplit(('https', 'chatgpt.com', '/share/' + share_id, '', ''))


def shared_urls(content):
    urls = []
    lines, fenced = [], False
    for line in content.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced and not line.lstrip().startswith('>'):
            lines.append(line)
    for match in re.finditer(r'https://(?:www\.)?(?:chatgpt\.com|chat\.openai\.com)/share/[^\s<>"\]\)]+', '\n'.join(lines), re.I):
        try:
            url = canonical_share_url(match.group().rstrip('.,;!?'))
        except SharedLinkError:
            continue
        if url not in urls:
            urls.append(url)
    if len(urls) > 2:
        raise SharedLinkError('Deux liens ChatGPT maximum par message.')
    return urls


def _unflatten(table):
    """React Router/turbo-stream reference tables; never eval downloaded JavaScript."""
    if not isinstance(table, list) or not table or len(table) > MAX_NODES:
        return None
    memo, visiting = {}, set()

    def resolve(index, depth=0):
        if type(index) is not int or not 0 <= index < len(table) or depth > 80 or index in visiting:
            return None
        if index in memo:
            return memo[index]
        visiting.add(index)
        value = table[index]
        if isinstance(value, dict):
            result = {}
            for key, ref in value.items():
                name = resolve(int(key[1:]), depth + 1) if re.fullmatch(r'_\d+', key) else key
                if isinstance(name, str):
                    result[name] = resolve(ref, depth + 1)
        elif isinstance(value, list):
            result = [resolve(ref, depth + 1) for ref in value]
        else:
            result = value
        visiting.remove(index)
        memo[index] = result
        return result

    return resolve(0)


def _payloads(soup):
    decoder = json.JSONDecoder()
    for script in soup.find_all('script'):
        text = script.get_text()
        if script.get('id') == '__NEXT_DATA__' or script.get('type') == 'application/json':
            try:
                yield json.loads(text)
            except (ValueError, RecursionError):
                pass
        for match in re.finditer(r'streamController\.enqueue\(\s*', text):
            try:
                chunk, _ = decoder.raw_decode(text[match.end():])
                if isinstance(chunk, str) and chunk.lstrip().startswith('['):
                    yield _unflatten(json.loads(chunk))
            except (ValueError, RecursionError):
                pass


def _conversations(value):
    pending, seen = [value], set()
    while pending and len(seen) < MAX_NODES:
        node = pending.pop()
        if not isinstance(node, (dict, list)) or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            if isinstance(node.get('mapping'), dict) or isinstance(node.get('linear_conversation'), list):
                yield node
            pending.extend(v for v in node.values() if isinstance(v, (dict, list)))
        else:
            pending.extend(v for v in node if isinstance(v, (dict, list)))


def _messages(conversation):
    mapping = conversation.get('mapping')
    current = conversation.get('current_node')
    if isinstance(mapping, dict) and isinstance(current, str) and current in mapping:
        ordered, visited = [], set()
        while current in mapping and current not in visited and len(visited) < MAX_MESSAGES:
            visited.add(current)
            node = mapping[current]
            if not isinstance(node, dict):
                break
            ordered.append(node)
            current = node.get('parent')
        ordered.reverse()
    else:
        ordered = conversation.get('linear_conversation') or (list(mapping.values()) if isinstance(mapping, dict) else [])
    result = []
    for node in ordered[:MAX_MESSAGES]:
        if not isinstance(node, dict):
            continue
        message = node.get('message') or node
        if not isinstance(message, dict):
            continue
        author = message.get('author')
        if not isinstance(author, dict):
            continue
        role = author.get('role')
        metadata = message.get('metadata') or {}
        if (not isinstance(metadata, dict) or role not in {'user', 'assistant'} or message.get('channel') not in (None, 'final')
                or metadata.get('is_visually_hidden_from_conversation') or message.get('recipient') not in (None, 'all')):
            continue
        content = message.get('content') or {}
        if isinstance(content, dict):
            if content.get('content_type') not in (None, 'text', 'multimodal_text'):
                continue
            parts = content.get('parts', [])
            text = '\n'.join(part for part in parts if isinstance(part, str)) if isinstance(parts, list) else ''
        else:
            text = content if isinstance(content, str) else ''
        if text.strip():
            result.append({'role': role, 'content': text.strip()})
    return result


def parse_shared_conversation(body, max_chars=20000):
    soup = BeautifulSoup(body, 'html.parser')
    title = soup.title.get_text(strip=True) if soup.title else 'Conversation ChatGPT partagée'
    messages = []
    for payload in _payloads(soup):
        for conversation in _conversations(payload):
            extracted = _messages(conversation)
            if len(extracted) > len(messages):
                messages = extracted
                if isinstance(conversation.get('title'), str):
                    title = conversation['title']
    if not messages:
        for node in soup.select('[data-message-author-role]')[:MAX_MESSAGES]:
            role = node.get('data-message-author-role')
            if role in {'user', 'assistant'} and node.get_text(strip=True):
                messages.append({'role': role, 'content': node.get_text('\n', strip=True)})
    if not messages:
        raise SharedLinkError('La conversation partagée est inaccessible, supprimée ou dans un format non reconnu. Ouvre le lien puis colle le texte avec « Lire un lien » ; aucune connexion à ton compte ChatGPT n’est utilisée.')
    # Keep the most recent context when a share exceeds the bounded token budget.
    text = '\n\n'.join(('Utilisateur' if m['role'] == 'user' else 'Assistant') + ' :\n' + m['content'] for m in messages)
    truncated = len(text) > max_chars
    if truncated:
        prefix = '[Conversation longue : extrait de la fin uniquement]\n'
        budget = max(0, max_chars - len(prefix))
        text = (prefix[:max_chars] + (text[-budget:] if budget else ''))[:max_chars]
    return {'title': title[:300], 'content': text, 'truncated': truncated, 'message_count': len(messages),
            'source_type': 'chatgpt_shared_conversation'}
