"""Bound history sent to providers while keeping the full saved conversation."""

from django.conf import settings


def bounded_history(messages):
    max_messages = max(1, settings.CHAT_HISTORY_MAX_MESSAGES)
    remaining = max(1, settings.CHAT_HISTORY_MAX_CHARS)
    selected = []
    for message in reversed(list(messages)[-max_messages:]):
        content = message['content']
        if len(content) > remaining:
            if selected:
                break
            content = content[-remaining:]
        selected.append({'role': message['role'], 'content': content})
        remaining -= len(content)
        if remaining <= 0:
            break
    selected.reverse()
    # Retain a user/assistant boundary rather than an orphaned answer.
    if selected and selected[0]['role'] == 'assistant':
        selected.pop(0)
    return selected


def conversation_history(messages):
    recent = list(messages.filter(role__in={'user', 'assistant'})
                  .order_by('-created_at', '-id')
                  .values('role', 'content')[:max(1, settings.CHAT_HISTORY_MAX_MESSAGES)])
    result = bounded_history(reversed(recent))
    # Very small contexts preserve their existing bound without a summary.
    if result and settings.CHAT_HISTORY_MAX_CHARS >= 2000 and settings.CHAT_HISTORY_MAX_MESSAGES >= 4:
        summary = _extract_summary(messages)
        if summary:
            # Keep summaries inside the same provider budget, with recent turns.
            result = bounded_history([{'role':'user', 'content':'Contexte ancien extrait de l’historique (incomplet, à vérifier) :\n' + summary},
                                      *result[-(settings.CHAT_HISTORY_MAX_MESSAGES - 1):]])
    return result


def _extract_summary(messages):
    recent_ids = list(messages.order_by('-created_at', '-id').values_list('id', flat=True)[:settings.CHAT_HISTORY_MAX_MESSAGES])
    if not recent_ids:
        return ''
    cutoff = min(recent_ids)
    first = messages.order_by('id').first()
    if not first or not hasattr(first, 'conversation') or not first.conversation:
        return ''
    conversation = first.conversation
    if conversation.summary_through_id >= cutoff:
        return ''  # An edited branch must never receive a summary of future turns.
    old = list(messages.filter(id__gt=conversation.summary_through_id, id__lt=cutoff).order_by('id')[:50])
    summary = conversation.summary
    if old:
        extracted = '\n'.join(f'{m.role}: {m.content[:400]}' for m in old)
        combined = summary + '\n' + extracted
        summary = combined if len(combined) <= 5000 else combined[:2000] + '\n[…]\n' + combined[-2900:]
        type(conversation).objects.filter(pk=conversation.pk, summary_through_id=conversation.summary_through_id).update(
            summary=summary, summary_through_id=old[-1].pk)
    return summary.strip()
