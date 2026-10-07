from django.db import models

from django.conf import settings

from .crypto import decrypt_value, encrypt_value
import secrets
import hashlib

class APIProvider(models.Model):
    """Un fournisseur externe compatible OpenAI/Anthropic."""
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Nom lisible, ex: 'Anthropic Claude'",
    )
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    base_url = models.URLField(blank=True, help_text="URL de base de l'API, ex: https://api.anthropic.com")
    docs_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        db_table = "chat_apiprovider"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.slugify_name(self.name)
        super().save(*args, **kwargs)

    @staticmethod
    def slugify_name(name):
        from django.utils.text import slugify
        return slugify(name)

    def __str__(self):
        return self.name


class AIModel(models.Model):
    """Un modèle disponible auprès d'un fournisseur d'API."""
    provider = models.ForeignKey(APIProvider, on_delete=models.CASCADE, related_name="models")
    name = models.CharField(max_length=100, help_text="Nom affiché dans l'interface")
    model_id = models.CharField(max_length=150, help_text="Identifiant attendu par l'API")
    is_active = models.BooleanField(default=True)

    input_price_per_million = models.DecimalField(max_digits=8, decimal_places=4, default=0, help_text="Prix en $ pour 1M tokens en entrée")
    output_price_per_million = models.DecimalField(max_digits=8, decimal_places=4, default=0, help_text="Prix en $ pour 1M tokens en sortie")
    openrouter_rankings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    

    class Meta:
        ordering = ["provider__name", "name"]
        unique_together = ("provider", "model_id")
        db_table = "chat_aimodel"

    def __str__(self):
        return f"{self.name} ({self.provider.name})"


class APIKey(models.Model):
    """Clé API chiffrée, conservée de façon compatible avec les usages existants."""
    provider = models.ForeignKey(APIProvider, on_delete=models.CASCADE, related_name="keys")
    name = models.CharField(max_length=100, help_text="Nom donné à cette clé")
    encrypted_key = models.TextField(help_text="Clé API chiffrée")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["provider__name", "name"]
        unique_together = ("provider", "name")
        db_table = "chat_apikey"

    def set_key(self, plain_key: str):
        self.encrypted_key = encrypt_value(plain_key)

    def get_key(self) -> str:
        return decrypt_value(self.encrypted_key)

    @property
    def key(self):
        return self.get_key()

    @key.setter
    def key(self, value):
        self.set_key(value)

    @property
    def masked_key(self):
        try:
            key = self.get_key()
            return key[:6] + "..." + key[-4:] if len(key) > 10 else "****"
        except Exception:
            return "****"

    def __str__(self):
        return f"{self.provider.name} — {self.name}"

