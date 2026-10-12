from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0019_alter_routingobservation_strategy')]

    operations = [
        migrations.AddField(model_name='message', name='tool_events', field=models.JSONField(blank=True, default=list)),
    ]
