from django.conf import settings
from django.db import models

from api.models import AIModel


class Workspace(models.Model):
    """
    Espace de travail autonome.

    Un workspace appartient directement à un utilisateur.
    Il ne dépend pas d'une Conversation du chat classique.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspaces",
    )

    title = models.CharField(
        max_length=200,
        default="Nouvel espace de travail",
    )
    active_model = models.ForeignKey(
        AIModel, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="workspaces"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.user})"


class WorkFile(models.Model):
    """
    Fichier présent dans un workspace.
    """

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="files",
    )

    file = models.FileField(
        upload_to="workspaces/%Y/%m/%d/",
    )

    original_name = models.CharField(
        max_length=255,
    )

    content_type = models.CharField(
        max_length=100,
        blank=True,
    )

    size_bytes = models.PositiveIntegerField(
        default=0,
    )

    extracted_text = models.TextField(
        blank=True,
    )

    uploaded_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.original_name


class WorkMessage(models.Model):
    """
    Message appartenant au workspace.
    """

    ROLE_CHOICES = [
        ("user", "Utilisateur"),
        ("assistant", "Assistant"),
        ("tool", "Outil"),
    ]

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
    )

    content = models.TextField()



    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"


class ToolExecution(models.Model):
    """
    Historique d'exécution d'un outil.
    """

    STATUS_CHOICES = [
        ("running", "En cours"),
        ("success", "Réussi"),
        ("error", "Erreur"),
    ]

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="tool_executions",
    )

    tool_name = models.CharField(
        max_length=100,
    )

    arguments = models.JSONField(
        default=dict,
    )

    result = models.JSONField(
        default=dict,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="running",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.tool_name} - {self.status}"