"""Vérifie et régularise les factures dont l'IPN n'a pas été reçu."""

from django.core.management.base import BaseCommand, CommandError

from ai_user.models import RechargePurchase
from ai_user.paydunya import PayDunyaError, confirm_purchase, is_configured


class Command(BaseCommand):
    help = "Vérifie les recharges PayDunya en attente et crédite les paiements confirmés."

    def add_arguments(self, parser):
        parser.add_argument("--purchase-id", type=int, help="Vérifier uniquement cette recharge.")

    def handle(self, *args, **options):
        if not is_configured():
            raise CommandError("PayDunya n'est pas configuré.")
        purchases = RechargePurchase.objects.filter(
            method=RechargePurchase.Method.PAYDUNYA,
            status=RechargePurchase.Status.PENDING,
        ).order_by("pk")
        if options["purchase_id"] is not None:
            purchases = purchases.filter(pk=options["purchase_id"])

        confirmed = pending = failed = errors = credits = 0
        for purchase in purchases.iterator():
            try:
                result = confirm_purchase(purchase)
            except PayDunyaError as error:
                errors += 1
                self.stderr.write(f"Recharge {purchase.pk} : {error}")
                continue
            if result.status == RechargePurchase.Status.PAID:
                confirmed += 1
                credits += result.credits
            elif result.status == RechargePurchase.Status.FAILED:
                failed += 1
            else:
                pending += 1
        self.stdout.write(
            f"Confirmées : {confirmed} ({credits} jetons) ; en attente : {pending} ; "
            f"échouées : {failed} ; erreurs : {errors}."
        )
        if errors:
            raise CommandError("Certaines recharges n'ont pas pu être vérifiées ; relancer la commande.")
