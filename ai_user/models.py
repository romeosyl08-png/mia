import hashlib
import secrets

from django.conf import settings
from django.db import models, transaction

class GoogleIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_identity")
    google_sub = models.CharField(max_length=100, unique=True)  # identifiant unique Google
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    
class TelegramIdentity(models.Model):
    """Associe un compte Django a un identifiant Telegram verifie."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_identity",
    )
    telegram_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Telegram {self.telegram_id} - {self.user}"


class InsufficientCreditsError(Exception):
    """Levée quand un débit dépasse le solde disponible."""
    pass


class CreditAccount(models.Model):
    """
    Le portefeuille de jetons d'un utilisateur.
    Un compte par utilisateur (créé automatiquement, voir signals.py).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credit_account",
    )
    balance = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-balance"]

    def __str__(self):
        return f"{self.user} — {self.balance} jetons"

    @transaction.atomic
    def credit(self, amount, reason="recharge", note=""):
        """Ajoute des jetons (recharge, remboursement...)."""
        if amount <= 0:
            raise ValueError("Le montant crédité doit être positif.")
        account = type(self).objects.select_for_update().get(pk=self.pk)
        account.balance += amount
        account.save(update_fields=["balance", "updated_at"])
        self.balance = account.balance
        return account.transactions.create(amount=amount, reason=reason, note=note)

    @transaction.atomic
    def debit(self, amount, reason="usage", note=""):
        """Retire des jetons (consommation par un message, etc.)."""
        if amount <= 0:
            raise ValueError("Le montant débité doit être positif.")
        account = type(self).objects.select_for_update().get(pk=self.pk)
        if account.balance < amount:
            raise InsufficientCreditsError(
                f"Solde insuffisant : {account.balance} < {amount}"
            )
        account.balance -= amount
        account.save(update_fields=["balance", "updated_at"])
        self.balance = account.balance
        return account.transactions.create(amount=-amount, reason=reason, note=note)


class CreditTransaction(models.Model):
    """Une ligne de l'historique : chaque recharge ou consommation."""

    class Reason(models.TextChoices):
        RECHARGE = "recharge", "Recharge"
        USAGE = "usage", "Consommation"
        ADJUSTMENT = "adjustment", "Ajustement manuel"

    account = models.ForeignKey(
        CreditAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    amount = models.IntegerField(
        help_text="Positif pour une recharge, négatif pour une consommation."
    )
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.USAGE)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.amount > 0 else ""
        return f"{self.account.user} {sign}{self.amount} ({self.reason})"


class RechargePurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        PAID = "paid", "Payé"
        FAILED = "failed", "Échoué"

    class Method(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        CINETPAY = "cinetpay", "CinetPay"
        

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stripe_session_id = models.CharField(max_length=200, blank=True, null=True, unique=True)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.STRIPE)
    reference = models.CharField(max_length=100, blank=True, help_text="Référence ou numéro de transaction Wave donné par l'utilisateur")
    phone_number = models.CharField(max_length=20, blank=True)
    amount_cents = models.PositiveIntegerField()
    credits = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} — {self.credits} crédits ({self.get_status_display()})"




class UserAPIKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100, default="Clé par défaut")
    key_prefix = models.CharField(max_length=20)   # partie visible, ex: sk-ai2ai-Ab3d...
    key_hash = models.CharField(max_length=64, unique=True)  # sha256 hex, jamais réversible
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        db_table = "api_userapikey"

    @staticmethod
    def generate():
        raw = secrets.token_urlsafe(32)
        full_key = f"sk-ai2ai-{raw}"
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()
        prefix = full_key[:17] + "..."
        return full_key, prefix, key_hash

    def __str__(self):
        return f"{self.user} — {self.name} ({self.key_prefix})"

class ApiCall(models.Model):
    STATUS_CHOICES = [
        ("ok", "Réussi"),
        ("error", "Échec (remboursé)"),
        ("interrupted", "Interrompu"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_calls")
    key = models.ForeignKey("UserAPIKey", on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    key_prefix = models.CharField(max_length=30, blank=True)  # copie : l'historique survit à la révocation de la clé
    model_id = models.CharField(max_length=150)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    credits = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="ok")
    streamed = models.BooleanField(default=False)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]