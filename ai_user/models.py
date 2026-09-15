from django.conf import settings
from django.db import models, transaction


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
        PAID = "paid", "Payee"
        FAILED = "failed", "Echouee"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stripe_session_id = models.CharField(max_length=255, unique=True)
    amount_cents = models.PositiveIntegerField()
    credits = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]