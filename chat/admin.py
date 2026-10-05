from django.contrib import admin

from .models import Conversation, Message, Persona, RoutingFeedback, RoutingObservation


admin.site.register(Persona)
admin.site.register(Conversation)
admin.site.register(Message)


@admin.register(RoutingObservation)
class RoutingObservationAdmin(admin.ModelAdmin):
	list_display = (
		"created_at", "surface", "task_category", "user", "model_id",
		"recommended_model_id", "outcome", "recommendation_confidence",
		"recommendation_score", "recommended_estimated_credits", "charged_credits", "duration_ms",
	)
	list_filter = ("surface", "task_category", "outcome", "recommendation_basis")
	search_fields = ("user__username", "model_id", "provider_slug", "recommended_model_id")
	readonly_fields = tuple(field.name for field in RoutingObservation._meta.fields)
	date_hierarchy = "created_at"

	def has_view_permission(self, request, obj=None):
		return request.user.has_perm("chat.view_routingobservation")

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(RoutingFeedback)
class RoutingFeedbackAdmin(admin.ModelAdmin):
	list_display = ("created_at", "user", "rating", "correction_needed", "observation")
	list_filter = ("rating", "correction_needed")
	readonly_fields = tuple(field.name for field in RoutingFeedback._meta.fields)

	def has_view_permission(self, request, obj=None):
		return request.user.has_perm("chat.view_routingfeedback")

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False