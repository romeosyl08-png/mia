from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0024_persona_sharing_big_persona')]

    operations = [
        migrations.AddField(
            model_name='chatpreferences',
            name='favorite_model_ids',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
