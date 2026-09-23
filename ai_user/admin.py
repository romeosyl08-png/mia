from django.contrib import admin
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from .models import CreditAccount, CreditTransaction, RechargePurchase
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

class CreditTransactionInline(admin.TabularInline):
    model = CreditTransaction
    extra = 0
    readonly_fields = ("amount", "reason", "note", "created_at")
    can_delete = False
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        # Les transactions se créent via credit()/debit(), pas à la main.
        return False


@admin.register(CreditAccount)
class CreditAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
    inlines = [CreditTransactionInline]


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("account", "amount", "reason", "note", "created_at")
    list_filter = ("reason",)
    search_fields = ("account__user__username", "note")
    readonly_fields = ("account", "amount", "reason", "note", "created_at")

    def has_add_permission(self, request):
        return False


@login_required
def wave_pending_payments(request):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    pending = RechargePurchase.objects.filter(
        method=RechargePurchase.Method.WAVE,
        status=RechargePurchase.Status.PENDING,
    ).select_related("user").order_by("created_at")
    return render(request, "wave_pending.html", {"pending": pending})


@login_required
def confirm_wave_payment(request, purchase_id):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    purchase = get_object_or_404(
        RechargePurchase, id=purchase_id,
        status=RechargePurchase.Status.PENDING,
        method=RechargePurchase.Method.WAVE,
    )
    with transaction.atomic():
        purchase.status = RechargePurchase.Status.PAID
        purchase.paid_at = timezone.now()
        purchase.save(update_fields=["status", "paid_at"])
        purchase.user.credit_account.credit(
            purchase.credits, reason="recharge", note=f"Wave manuel — ref {purchase.reference or 'N/A'}"
        )
    return redirect("wave-pending-payments")


@login_required
def reject_wave_payment(request, purchase_id):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    purchase = get_object_or_404(
        RechargePurchase, id=purchase_id,
        status=RechargePurchase.Status.PENDING,
        method=RechargePurchase.Method.WAVE,
    )
    purchase.delete()
    return redirect("wave-pending-payments")