from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('bara', '0018_workspace_files_private_storage')]

    operations = [
        migrations.AddField(
            model_name='workmessage',
            name='tool_events',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
