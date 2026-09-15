from django.contrib import admin
from .models import CreditAccount, CreditTransaction


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