from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from .models import Message


@login_required
def recovery(request,token):
    message = get_object_or_404(Message,recovery_token=token,interrupted=True,conversation__user=request.user)
    user_message = message.conversation.messages.filter(role='user',pk__lt=message.pk).order_by('-pk').first()
    return JsonResponse({'conversation_id':message.conversation_id,'title':message.conversation.title,
        'assistant_message_id':message.pk,'user_message_id':user_message.pk if user_message else None,
        'reply':message.content,'interrupted':True,'sources':message.sources,'tool_events':message.tool_events})
