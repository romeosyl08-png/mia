from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ai_user", "0002_telegramidentity"),
    ]

    operations = [
        migrations.CreateModel(
            name="RechargePurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("stripe_session_id", models.CharField(max_length=255, unique=True)),
                ("amount_cents", models.PositiveIntegerField()),
                ("credits", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("pending", "En attente"), ("paid", "Payee"), ("failed", "Echouee")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]