import chat.storage
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0014_persona_memory"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generatedmedia",
            name="kind",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Vidéo"), ("audio", "Audio")],
                db_index=True,
                default="image",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="generatedmedia",
            name="image",
            field=models.FileField(
                storage=chat.storage.PrivateGeneratedMediaStorage(),
                upload_to="generated/media/%Y/%m/",
            ),
        ),
    ]