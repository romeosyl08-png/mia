from django.contrib import admin

from .models import Conversation, Message, Persona


admin.site.register(Persona)
admin.site.register(Conversation)
admin.site.register(Message)