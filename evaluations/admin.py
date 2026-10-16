from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from . import rewards
from .models import ComparisonResponse, ComparisonSession, EvaluationBudget, EvaluationReward, EvaluationVote, ModelPerformance


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(ComparisonSession)
class ComparisonAdmin(ReadOnlyAdmin):
    list_display = ['id', 'category', 'selection_mode', 'status', 'reserved_credits', 'settled', 'created_at']
    list_filter = ['selection_mode', 'status', 'category', 'analysis_consent']
    # Private content is intentionally absent even from the admin detail.
    exclude = ['prompt', 'request_digest', 'selection_metadata']

    def get_readonly_fields(self, request, obj=None):
        return [f for f in super().get_readonly_fields(request, obj) if f not in self.exclude]

    def get_urls(self):
        return [path('dashboard/', self.admin_site.admin_view(self.dashboard), name='evaluations_dashboard')] + super().get_urls()

    def dashboard(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        responses = ComparisonResponse.objects.all()
        sessions = ComparisonSession.objects.all()
        reward_totals = EvaluationReward.objects.filter(status='credited').aggregate(credits=Sum('credits'), consumed=Sum('consumed_credits'))
        provider_cost = responses.aggregate(total=Sum('provider_cost'))['total'] or Decimal(0)
        budget = EvaluationBudget.objects.get(pk=1)
        metrics = {
            'Comparaisons': sessions.count(), 'Votes': EvaluationVote.objects.count(),
            'Coût fournisseur USD (mesuré ou borne estimée)': provider_cost,
            'Coût moyen fournisseur USD': provider_cost / max(1, sessions.count()),
            'Jetons facturés': responses.aggregate(total=Sum('credits_charged'))['total'] or 0,
            'Jetons bonus distribués (budget historique)': budget.reward_credits_distributed,
            'Jetons bonus consommés (comptes présents)': reward_totals['consumed'] or 0,
            'Équivalent coût bonus consommés USD, hors marge': Decimal(reward_totals['consumed'] or 0) / Decimal(15000),
            'Évaluations suspectes': EvaluationVote.objects.exclude(suspicious_reasons=[]).count(),
            'Taux d’erreurs API': responses.filter(status='failed').count() / max(1, responses.exclude(status__in=['pending', 'processing']).count()),
            'Appels sans consommation vérifiée': responses.filter(status='completed', usage_reliable=False).count(),
            'Dépassements absorbés (jetons)': responses.aggregate(total=Sum('billing_shortfall'))['total'] or 0,
        }
        return render(request, 'admin/evaluations/dashboard.html', {
            **self.admin_site.each_context(request), 'title': 'Comparaisons et observation du routeur',
            'metrics': metrics, 'modes': sessions.values('selection_mode').annotate(count=Count('id')),
            'models': responses.values('model__name', 'model__provider__name').annotate(count=Count('id')).order_by('-count')[:20],
            'performance': ModelPerformance.objects.select_related('model__provider').order_by('category', '-estimated_quality'),
        })


@admin.register(EvaluationVote)
class VoteAdmin(ReadOnlyAdmin):
    list_display = ['id', 'comparison_id', 'preferred_response', 'suspicious_reasons', 'created_at']
    list_filter = ['preferred_response', 'comparison__selection_mode', 'comparison__category']


@admin.register(EvaluationReward)
class RewardAdmin(ReadOnlyAdmin):
    list_display = ['id', 'vote_id', 'credits', 'consumed_credits', 'status', 'reviewed_by', 'credited_at', 'review_link']
    list_filter = ['status']
    actions = ['approve_selected', 'credit_selected', 'reject_selected']

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return [f for f in fields if f != 'review_note'] if obj and obj.status == 'pending' else fields

    def save_model(self, request, obj, form, change):
        # Only a review note is editable. All monetary transitions use locked services.
        EvaluationReward.objects.filter(pk=obj.pk, status='pending').update(review_note=obj.review_note)

    @admin.display(description='Revue privée')
    def review_link(self, obj):
        return format_html('<a href="{}">Examiner</a>', reverse('admin:evaluation_reward_review', args=[obj.pk]))

    def get_urls(self):
        return [path('<int:pk>/review/', self.admin_site.admin_view(self.review), name='evaluation_reward_review')] + super().get_urls()

    def review(self, request, pk):
        rewards.reviewer_allowed(request.user)
        reward = get_object_or_404(EvaluationReward.objects.select_related('vote__comparison'), pk=pk)
        if request.method == 'POST':
            try:
                rewards.approve(reward.pk, request.user, request.POST.get('review_note', ''))
                self.log_change(request, reward, 'Qualité vérifiée et bonus approuvé après revue privée.')
                self.message_user(request, 'Qualité validée. Le crédit reste une action distincte soumise aux plafonds.')
                return redirect('admin:evaluations_evaluationreward_changelist')
            except ValueError as error:
                self.message_user(request, str(error), messages.ERROR)
        evaluation = reward.vote
        response = render(request, 'admin/evaluations/reward_review.html', {
            **self.admin_site.each_context(request), 'title': f'Revue privée du bonus #{reward.pk}',
            'reward': reward, 'evaluation': evaluation,
            'comparison': evaluation.comparison if evaluation else None,
            'responses': evaluation.comparison.responses.select_related('model__provider') if evaluation else [],
        })
        response['Cache-Control'] = 'private, no-store'
        return response

    @admin.action(description='Valider la qualité (motivation individuelle obligatoire)')
    def approve_selected(self, request, queryset):
        for reward in queryset:
            try:
                rewards.approve(reward.pk, request.user, reward.review_note)
            except ValueError as error:
                self.message_user(request, f'#{reward.pk} : {error}', messages.ERROR)

    @admin.action(description='Créditer les bonus validés')
    def credit_selected(self, request, queryset):
        for reward in queryset:
            try:
                rewards.credit(reward.pk, request.user)
            except ValueError as error:
                self.message_user(request, f'#{reward.pk} : {error}', messages.ERROR)

    @admin.action(description='Rejeter les bonus non crédités')
    def reject_selected(self, request, queryset):
        rewards.reviewer_allowed(request.user)
        for reward_id, user_id in queryset.values_list('pk', 'user_id'):
            with transaction.atomic():
                from ai_user.models import CreditAccount
                CreditAccount.objects.select_for_update().get(user_id=user_id)
                EvaluationReward.objects.filter(pk=reward_id, status__in=['pending', 'approved']).update(status='rejected', reviewed_by=request.user)


@admin.register(ModelPerformance)
class PerformanceAdmin(ReadOnlyAdmin):
    list_display = ['model', 'category', 'total_comparisons', 'estimated_quality', 'average_latency', 'average_cost', 'updated_at']
    list_filter = ['category']
