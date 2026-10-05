from django.conf import settings
from django.db import models

from api.models import AIModel
from api.crypto import decrypt_value, encrypt_value


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
    auto_route = models.BooleanField(default=False)
    instructions = models.TextField(blank=True)
    disabled_tools = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.user})"





class WorkConversation(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=200, default="Nouvelle conversation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.workspace})"


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

    public_token = models.CharField(max_length=32, unique=True, null=True, blank=True)

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

    conversation = models.ForeignKey(
        "WorkConversation", on_delete=models.CASCADE, related_name="messages", null=True, blank=True
    )

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
        ("pending", "En attente de confirmation"),
        ("running", "En cours"),
        ("success", "Réussi"),
        ("error", "Erreur"),
        ("rejected", "Refusé"),
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


class FileVersion(models.Model):
    file = models.ForeignKey(WorkFile, on_delete=models.CASCADE, related_name="versions")
    content = models.TextField(blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class WorkspaceMCPServer(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="mcp_servers")
    name = models.CharField(max_length=100)
    endpoint = models.URLField(max_length=500)
    encrypted_bearer_token = models.TextField(blank=True)
    available_tools = models.JSONField(default=list, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name"], name="bara_unique_mcp_server_name"),
        ]

    def set_bearer_token(self, token):
        self.encrypted_bearer_token = encrypt_value(token) if token else ""

    def get_bearer_token(self):
        return decrypt_value(self.encrypted_bearer_token) if self.encrypted_bearer_token else ""

    def __str__(self):
        return f"{self.name} ({self.workspace})"