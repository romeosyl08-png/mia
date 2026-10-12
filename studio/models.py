from django.conf import settings
from django.db import models
from chat.storage import PrivateGeneratedMediaStorage

class Project(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    persona = models.ForeignKey('chat.Persona', null=True, blank=True, on_delete=models.SET_NULL)
    workspace = models.ForeignKey('bara.Workspace', null=True, blank=True, on_delete=models.SET_NULL)
    current_version = models.ForeignKey('Version', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-updated_at']

class Version(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='versions')
    number = models.PositiveIntegerField()
    parent = models.ForeignKey('self', null=True, on_delete=models.SET_NULL)
    scene = models.JSONField(default=dict)
    label = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['project','number'], name='studio_project_version_number')]

class Job(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='jobs')
    base_version = models.ForeignKey(Version, on_delete=models.PROTECT, related_name='+')
    result_version = models.ForeignKey(Version, null=True, on_delete=models.SET_NULL, related_name='+')
    operations = models.JSONField(default=list)
    style_prompt = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='queued', db_index=True)
    progress = models.CharField(max_length=200, default='En attente du worker')
    error = models.TextField(blank=True)
    warning = models.TextField(blank=True)
    credit_limit = models.PositiveIntegerField(default=20000)
    reserved_credits = models.PositiveIntegerField(default=0)
    spent_credits = models.PositiveIntegerField(default=0)
    settled_at = models.DateTimeField(null=True)
    output_ids = models.JSONField(default=list)
    lease_token = models.CharField(max_length=32, blank=True)
    lease_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']

class Asset(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    job = models.ForeignKey(Job, null=True, on_delete=models.SET_NULL, related_name='assets')
    kind = models.CharField(max_length=10, choices=[('image','Image'),('video','Vidéo'),('audio','Audio')])
    role = models.CharField(max_length=16, default='reference')
    name = models.CharField(max_length=120)
    file = models.FileField(storage=PrivateGeneratedMediaStorage(), upload_to='studio/')
    content_type = models.CharField(max_length=80)
    metadata = models.JSONField(default=dict)
    size_bytes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
