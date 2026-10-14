from django.conf import settings as django_settings
from django.db import models

from .storage import PrivateGeneratedMediaStorage


class Persona(models.Model):
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personas")
    name = models.CharField(max_length=100)
    appearance_name = models.CharField(max_length=40, blank=True)
    appearance_avatar = models.CharField(max_length=16, blank=True)
    appearance_accent = models.CharField(max_length=7, blank=True)
    appearance_theme = models.CharField(max_length=10, blank=True, choices=[("light", "Clair"), ("dark", "Sombre")])
    prompt = models.TextField(blank=True)
    memory = models.TextField(blank=True)
    tone = models.CharField(max_length=50, blank=True)
    temperature = models.FloatField(default=0.7)
    length = models.CharField(max_length=20, default="medium")
    is_default = models.BooleanField(default=False)
    is_big_persona = models.BooleanField(default=False)
    share_with_personas = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user'], condition=models.Q(is_big_persona=True), name='one_big_persona_per_user'),
            models.CheckConstraint(condition=models.Q(is_big_persona=False) | models.Q(share_with_personas=False), name='big_persona_not_shared_source'),
        ]

    def __str__(self):
        return f"{self.name} ({self.user})"


class ChatPreferences(models.Model):
    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_preferences",
    )
    default_model_id = models.CharField(max_length=150, blank=True)
    favorite_model_ids = models.JSONField(default=list, blank=True)
    top_p = models.FloatField(default=1.0)
    max_tokens = models.PositiveIntegerField(default=500)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Chat preferences ({self.user})"


class RoutingObservation(models.Model):
    SURFACE_CHOICES = [
        ("chat", "Chat"),
        ("bara", "Bara"),
        ("api", "API"),
    ]
    OUTCOME_CHOICES = [
        ("success", "Succès fournisseur"),
        ("error", "Échec fournisseur"),
        ("interrupted", "Interrompu"),
    ]

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="routing_observations",
    )
    assistant_message = models.OneToOneField(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routing_observation",
    )
    workspace = models.ForeignKey(
        "bara.Workspace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routing_observations",
    )
    surface = models.CharField(max_length=12, choices=SURFACE_CHOICES, db_index=True)
    task_category = models.CharField(max_length=40, default="general", db_index=True)
    strategy = models.CharField(max_length=40, default="single")
    model_id = models.CharField(max_length=150)
    provider_slug = models.CharField(max_length=100)
    recommended_model_id = models.CharField(max_length=150, blank=True)
    recommended_provider_slug = models.CharField(max_length=100, blank=True)
    recommendation_confidence = models.FloatField(default=0)
    recommendation_score = models.FloatField(default=0)
    recommended_estimated_credits = models.PositiveIntegerField(default=0)
    recommendation_basis = models.CharField(max_length=24, default="prior")
    outcome = models.CharField(max_length=12, choices=OUTCOME_CHOICES, db_index=True)
    estimated_credits = models.PositiveIntegerField(default=0)
    charged_credits = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    models_used = models.JSONField(default=list, blank=True)
    tools_used = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task_category", "model_id", "-created_at"]),
            models.Index(fields=["user", "task_category", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.surface}:{self.task_category} — {self.model_id} ({self.outcome})"


class RoutingFeedback(models.Model):
    RATING_CHOICES = [
        ("positive", "Utile"),
        ("negative", "À améliorer"),
    ]

    observation = models.OneToOneField(
        RoutingObservation,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="routing_feedback",
    )
    rating = models.CharField(max_length=10, choices=RATING_CHOICES)
    correction_needed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.rating} — {self.observation}"


class UserMemory(models.Model):
    user = models.OneToOneField(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assistant_memory')
    content = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class Conversation(models.Model):
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    persona = models.ForeignKey(Persona, on_delete=models.SET_NULL, null=True, blank=True)
    workspace = models.ForeignKey("bara.Workspace", on_delete=models.SET_NULL, null=True, blank=True, related_name="chat_conversations")
    title = models.CharField(max_length=200, default="Nouvelle conversation")
    folder = models.CharField(max_length=80, blank=True)
    is_archived = models.BooleanField(default=False)
    summary = models.TextField(blank=True)
    summary_through_id = models.PositiveBigIntegerField(default=0)
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
    sources = models.JSONField(default=list, blank=True)
    image_file_ids = models.JSONField(default=list, blank=True)
    interrupted = models.BooleanField(default=False)
    recovery_token = models.CharField(max_length=32,unique=True,null=True,blank=True)
    tool_events = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class GeneratedMedia(models.Model):
    KIND_CHOICES = [("image", "Image"), ("video", "Vidéo"), ("audio", "Audio")]

    owner = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generated_media",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="generated_media",
    )
    message = models.OneToOneField(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_media",
    )
    image = models.FileField(
        storage=PrivateGeneratedMediaStorage(),
        upload_to="generated/media/%Y/%m/",
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="image", db_index=True)
    prompt = models.TextField()
    content_type = models.CharField(max_length=40, default="image/png")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Generated {self.get_kind_display().lower()} {self.pk} ({self.owner})"





