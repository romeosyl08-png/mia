from django.conf import settings
from django.db import models

from api.models import AIModel
from api.crypto import decrypt_value, encrypt_value
from chat.storage import PrivateMediaStorage


class AgentTask(models.Model):
    workspace = models.ForeignKey('Workspace', on_delete=models.CASCADE, related_name='agent_tasks')
    conversation = models.ForeignKey('WorkConversation', on_delete=models.SET_NULL, null=True, blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT)
    objective = models.TextField()
    status = models.CharField(max_length=20, default='queued', db_index=True)
    state = models.JSONField(default=dict)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)
    max_steps = models.PositiveIntegerField(default=12)
    credit_limit = models.PositiveIntegerField(default=1000)
    spent_credits = models.PositiveIntegerField(default=0)
    steps = models.PositiveIntegerField(default=0)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_token = models.CharField(max_length=32, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class TaskEvent(models.Model):
    task = models.ForeignKey(AgentTask, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=30)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class EvaluationRun(models.Model):
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT)
    report = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class WorkspaceMember(models.Model):
    workspace = models.ForeignKey('Workspace', on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=[('viewer','Lecture'),('editor','Modification')])
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['workspace','user'],name='unique_workspace_member')]


class WorkspaceAudit(models.Model):
    workspace = models.ForeignKey('Workspace', on_delete=models.CASCADE, related_name='audit_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)


class TaskSchedule(models.Model):
    workspace = models.ForeignKey('Workspace', on_delete=models.CASCADE, related_name='schedules')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    objective = models.TextField()
    next_run = models.DateTimeField(db_index=True)
    interval_minutes = models.PositiveIntegerField(default=0)
    credit_limit = models.PositiveIntegerField(default=1000)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


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
    memory = models.TextField(blank=True)
    memory_enabled = models.BooleanField(default=True)
    memory_in_chat = models.BooleanField(default=True)
    memory_in_barrassons = models.BooleanField(default=False)
    semantic_search = models.BooleanField(default=False)
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
    summary = models.TextField(blank=True)
    summary_through_id = models.PositiveBigIntegerField(default=0)
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

    # Stockage privé : jamais servi par /media/. Le téléchargement passe
    # par des vues qui vérifient l'appartenance à l'espace de travail.
    file = models.FileField(
        storage=PrivateMediaStorage(),
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
    retrieval_index = models.JSONField(default=dict, blank=True)

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
        indexes = [models.Index(fields=["conversation", "created_at"], name="bara_msg_conv_created")]

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
    binary_file = models.FileField(storage=PrivateMediaStorage(), upload_to='versions/%Y/%m/%d/', blank=True)
    specification = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class DocumentArtifact(models.Model):
    file = models.OneToOneField(WorkFile, on_delete=models.CASCADE, related_name='artifact')
    kind = models.CharField(max_length=10)
    specification = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


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


class WorkspacePluginConnection(models.Model):
    PLUGIN_CHOICES = [
        ("google_mail", "Gmail"),
        ("microsoft_mail", "Outlook"),
        ("blender", "Blender"),
        ('google_drive','Google Drive'),
        ('google_calendar','Google Calendar'),
        ('github','GitHub'),
    ]

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="plugin_connections",
    )
    plugin = models.CharField(max_length=20, choices=PLUGIN_CHOICES)
    account_email = models.EmailField(blank=True)
    encrypted_access_token = models.TextField(blank=True)
    encrypted_refresh_token = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    blender_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    is_enabled = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plugin"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "plugin"],
                name="bara_unique_workspace_plugin",
            ),
        ]

    def set_oauth_tokens(self, access_token, refresh_token=None):
        self.encrypted_access_token = encrypt_value(access_token) if access_token else ""
        if refresh_token:
            self.encrypted_refresh_token = encrypt_value(refresh_token)

    def get_access_token(self):
        return decrypt_value(self.encrypted_access_token) if self.encrypted_access_token else ""

    def get_refresh_token(self):
        return decrypt_value(self.encrypted_refresh_token) if self.encrypted_refresh_token else ""

    def __str__(self):
        return f"{self.get_plugin_display()} ({self.workspace})"


class BlenderAutomationTask(models.Model):
    STATUS_CHOICES = [
        ("queued", "En attente"),
        ("running", "En cours"),
        ("success", "Réussi"),
        ("error", "Erreur"),
    ]

    connection = models.ForeignKey(
        WorkspacePluginConnection,
        on_delete=models.CASCADE,
        related_name="blender_tasks",
    )
    action = models.CharField(max_length=40)
    arguments = models.JSONField(default=dict)
    result = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="queued")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
