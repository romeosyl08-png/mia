from django.conf import settings as django_settings
from django.db import models


class Persona(models.Model):
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personas")
    name = models.CharField(max_length=100)
    prompt = models.TextField(blank=True)
    tone = models.CharField(max_length=50, blank=True)
    temperature = models.FloatField(default=0.7)
    length = models.CharField(max_length=20, default="medium")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.user})"


class Conversation(models.Model):
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    persona = models.ForeignKey(Persona, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200, default="Nouvelle conversation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.user})"


class Message(models.Model):
    ROLE_CHOICES = [("user", "user"), ("assistant", "assistant"), ("system", "system")]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


from api.models import AIModel, APIKey, APIProvider



