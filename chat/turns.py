"""Persist a complete chat turn and settle its reservation atomically."""

from django.db import transaction

from .billing import credits_for_usage, settle_reservation
from .models import Conversation, Message


@transaction.atomic
def save_interrupted_turn(user, conversation, persona, content, partial, workspace, sources, tool_events, image_file_ids, recovery_token):
    if conversation is None:
        conversation = Conversation.objects.create(user=user,title=content[:200],persona=persona,workspace=workspace)
    user_message = Message.objects.create(conversation=conversation,role='user',content=content,image_file_ids=image_file_ids or [])
    assistant = Message.objects.create(conversation=conversation,role='assistant',content=partial,
        sources=sources or [],tool_events=tool_events or [],interrupted=True,recovery_token=recovery_token)
    conversation.save(update_fields=['updated_at'])
    return {'conversation_id':conversation.pk,'title':conversation.title,'user_message_id':user_message.pk,
            'assistant_message_id':assistant.pk,'reply':partial,'interrupted':True,'sources':assistant.sources,'tool_events':assistant.tool_events}


@transaction.atomic
def complete_chat_turn(user, conversation, persona, content, reply, model, account, reserve, estimated_input, provider_input, provider_output, edit_message=None, workspace=None, temporary=False, sources=None, usage_records=None, tool_events=None, image_file_ids=None):
    prompt_tokens = provider_input or estimated_input
    completion_tokens = provider_output or round(len(reply) / 4)
    if usage_records:
        prompt_tokens = sum(record['prompt_tokens'] for record in usage_records)
        completion_tokens = sum(record['completion_tokens'] for record in usage_records)
    actual = sum(record['credits'] for record in usage_records) if usage_records else credits_for_usage(model, prompt_tokens, completion_tokens)
    settlement = settle_reservation(account, reserve, actual, model.model_id)
    result = {
        'conversation_id': None,
        'title': content[:200],
        'reply': reply,
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'remaining_balance': account.balance,
        'charged_credits': settlement.charged_credits,
        'billing_warning': (
            f"Solde insuffisant : {settlement.shortfall} jetons de coût supplémentaire n'ont pas pu être débités."
            if settlement.shortfall else None
        ),
        'user_message_id': None,
        'assistant_message_id': None,
        'sources': sources or [],
        'tool_events': tool_events or [],
    }
    if temporary:
        return result
    title_changed = False
    if edit_message:
        is_first_message = not conversation.messages.filter(id__lt=edit_message.id).exists()
        conversation.messages.filter(id__gte=edit_message.id).delete()
        conversation.summary = ''
        conversation.summary_through_id = 0
        if is_first_message:
            conversation.title = content[:200]
            title_changed = True
    if conversation is None:
        conversation = Conversation.objects.create(user=user, title=content[:200], persona=persona, workspace=workspace)
    user_message = Message.objects.create(conversation=conversation, role='user', content=content, prompt_tokens=prompt_tokens, image_file_ids=image_file_ids or [])
    assistant_message = Message.objects.create(conversation=conversation, role='assistant', content=reply, completion_tokens=completion_tokens, sources=sources or [], tool_events=tool_events or [])
    if not title_changed:
        conversation.refresh_from_db(fields=['title'])
    fields = ['title','updated_at'] if title_changed else ['updated_at']
    if edit_message:
        fields += ['summary','summary_through_id']
    conversation.save(update_fields=fields)
    result.update(conversation_id=conversation.id, title=conversation.title, user_message_id=user_message.id, assistant_message_id=assistant_message.id)
    return result
