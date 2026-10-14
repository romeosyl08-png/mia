from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('api', '0006_aimodel_input_modalities')]

    operations = [
        migrations.AddField(
            model_name='aimodel',
            name='supported_parameters',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
