from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_delete_userapikey'),
    ]

    operations = [
        migrations.AddField(
            model_name='aimodel',
            name='openrouter_rankings',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
