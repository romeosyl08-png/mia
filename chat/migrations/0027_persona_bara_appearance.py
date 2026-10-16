from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0026_generatedmedia_independent_conversation')]

    operations = [
        migrations.AddField(
            model_name='persona',
            name='bara_appearance_accent',
            field=models.CharField(default='#007ACC', max_length=7),
        ),
        migrations.AddField(
            model_name='persona',
            name='bara_appearance_theme',
            field=models.CharField(
                choices=[('light', 'Clair'), ('dark', 'Sombre')],
                default='dark',
                max_length=10,
            ),
        ),
    ]
