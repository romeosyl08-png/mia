from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('chat', '0025_chatpreferences_favorite_model_ids')]

    operations = [
        migrations.AlterField(
            model_name='generatedmedia', name='conversation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='generated_media', to='chat.conversation'),
        ),
    ]
