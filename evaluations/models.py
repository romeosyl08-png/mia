import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


CATEGORIES = [(value, label) for value, label in (
    ('general', 'Général'), ('coding', 'Code'), ('analysis', 'Analyse'),
    ('research', 'Recherche'), ('writing', 'Rédaction'), ('math', 'Mathématiques'),
)]


class ComparisonSession(models.Model):
    class Mode(models.TextChoices):
        MANUAL = 'manual', 'Manuel'
        AUTOMATIC = 'automatic', 'Surprise'

    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente'
        PROCESSING = 'processing', 'En cours'
        COMPLETED = 'completed', 'Terminée'
        FAILED = 'failed', 'Échec'
        CANCELLED = 'cancelled', 'Annulée'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comparisons')
    prompt = models.TextField()
    category = models.CharField(max_length=24, choices=CATEGORIES, default='general')
    selection_mode = models.CharField(max_length=12, choices=Mode.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    request_key = models.UUIDField(default=uuid.uuid4)
    request_digest = models.CharField(max_length=64)
    selection_metadata = models.JSONField(default=dict)
    analysis_consent = models.BooleanField(default=False)
    max_output_tokens = models.PositiveIntegerField()
    reserved_credits = models.PositiveIntegerField(default=0)
    settled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'request_key'], name='comparison_user_request_unique')]
        indexes = [models.Index(fields=['status', 'created_at']), models.Index(fields=['user', 'created_at'])]


class ComparisonResponse(models.Model):
    comparison = models.ForeignKey(ComparisonSession, on_delete=models.CASCADE, related_name='responses')
    model = models.ForeignKey('api.AIModel', on_delete=models.PROTECT, related_name='comparison_responses')
    slot = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B')])
    response_text = models.TextField(blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    provider_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    credits_charged = models.PositiveIntegerField(default=0)
    reserved_credits = models.PositiveIntegerField(default=0)
    estimated_input_tokens = models.PositiveIntegerField(default=0)
    input_price = models.DecimalField(max_digits=8, decimal_places=4)
    output_price = models.DecimalField(max_digits=8, decimal_places=4)
    usage_reliable = models.BooleanField(default=False)
    billing_shortfall = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=[
        ('pending', 'En attente'), ('processing', 'En cours'), ('completed', 'Réussie'), ('failed', 'Échec'),
    ], default='pending')
    error_message = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ['slot']
        constraints = [
            models.UniqueConstraint(fields=['comparison', 'slot'], name='comparison_slot_unique'),
            models.UniqueConstraint(fields=['comparison', 'model'], name='comparison_model_unique'),
            models.CheckConstraint(condition=Q(slot__in=['A', 'B']), name='comparison_slot_valid'),
        ]


class EvaluationVote(models.Model):
    comparison = models.OneToOneField(ComparisonSession, on_delete=models.CASCADE, related_name='vote')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    preferred_response = models.CharField(max_length=8, choices=[
        ('A', 'A'), ('B', 'B'), ('tie', 'Égalité'), ('both_bad', 'Les deux sont mauvais'),
    ])
    accuracy_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    relevance_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    clarity_score = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    justification = models.TextField(blank=True)
    justification_hash = models.CharField(max_length=64, blank=True, db_index=True)
    suspicious_reasons = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['comparison', 'user'], name='evaluation_user_comparison_unique'),
            models.CheckConstraint(condition=Q(preferred_response__in=['A', 'B', 'tie', 'both_bad']), name='evaluation_preference_valid'),
            *[models.CheckConstraint(condition=Q(**{field + '__isnull': True}) | Q(**{field + '__gte': 1, field + '__lte': 5}),
                                     name='evaluation_' + field + '_range')
              for field in ('accuracy_score', 'relevance_score', 'clarity_score')],
        ]


class EvaluationReward(models.Model):
    vote = models.OneToOneField(EvaluationVote, on_delete=models.SET_NULL, null=True, related_name='reward')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='evaluation_rewards')
    credits = models.PositiveIntegerField()
    consumed_credits = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=[(x, x) for x in ('pending', 'approved', 'rejected', 'credited')], default='pending')
    transaction = models.OneToOneField('ai_user.CreditTransaction', on_delete=models.SET_NULL, null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    review_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    credited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(consumed_credits__lte=models.F('credits')), name='reward_consumed_within_grant')]


class EvaluationBudget(models.Model):
    """Single durable lock for cross-user spending caps; created by migration."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    comparison_day = models.DateField(null=True)
    comparison_reserved = models.PositiveBigIntegerField(default=0)
    reward_credits_distributed = models.PositiveBigIntegerField(default=0)


class ModelPerformance(models.Model):
    model = models.ForeignKey('api.AIModel', on_delete=models.CASCADE, related_name='evaluation_performance')
    category = models.CharField(max_length=24, choices=CATEGORIES)
    total_comparisons = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    ties = models.PositiveIntegerField(default=0)
    estimated_quality = models.FloatField(default=0.5)
    average_latency = models.FloatField(default=0)
    average_cost = models.FloatField(default=0)
    statistics = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['model', 'category'], name='performance_model_category_unique')]
