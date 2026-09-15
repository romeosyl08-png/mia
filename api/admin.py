from django.contrib import admin
from django import forms

from .models import AIModel, APIKey, APIProvider


class APIKeyAdminForm(forms.ModelForm):
    key = forms.CharField(label="Clé API", required=False, widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = APIKey
        fields = ("provider", "name", "key", "is_active")

    def save(self, commit=True):
        instance = super().save(commit=False)
        key = self.cleaned_data.get("key")
        if key:
            instance.set_key(key)
        if commit:
            instance.save()
        return instance


class APIKeyInline(admin.TabularInline):
    model = APIKey
    form = APIKeyAdminForm
    extra = 1
    fields = ("name", "key", "is_active", "last_used_at")
    readonly_fields = ("last_used_at",)


@admin.register(APIProvider)
class APIProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at", "key_count")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [APIKeyInline]

    @admin.display(description="Clés")
    def key_count(self, obj):
        return obj.keys.count()


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    form = APIKeyAdminForm
    list_display = ("name", "provider", "masked_key_display", "is_active", "last_used_at", "created_at")
    list_filter = ("provider", "is_active")
    search_fields = ("name", "provider__name")
    readonly_fields = ("last_used_at", "created_at", "masked_key_display")
    fields = ("provider", "name", "key", "is_active", "masked_key_display", "last_used_at", "created_at")

    @admin.display(description="Aperçu")
    def masked_key_display(self, obj):
        return obj.masked_key


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ("name", "model_id", "provider", "is_active", "created_at")
    list_filter = ("provider", "is_active")
    search_fields = ("name", "model_id", "provider__name")
    readonly_fields = ("created_at",)
