from django.conf import settings
from django.db import migrations, models


def create_big_personas(apps, schema_editor):
    Persona = apps.get_model('chat', 'Persona')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    alias = schema_editor.connection.alias
    for user_id in User.objects.using(alias).values_list('pk', flat=True).iterator():
        Persona.objects.using(alias).get_or_create(user_id=user_id, is_big_persona=True, defaults={
            'name': 'Big Persona', 'appearance_name': 'Big Persona', 'appearance_avatar': '✦',
            'appearance_accent': '#6C6FCB', 'appearance_theme': 'light',
            'prompt': 'Tu es Big Persona, l’assistant transversal de ce compte. Combine les mémoires et réglages des personas dont la liaison est autorisée, en privilégiant la demande actuelle. Utilise les informations du compte et les réglages fonctionnels du site fournis. Si des préférences se contredisent, demande une précision. Les informations de contexte ne donnent aucune permission supplémentaire sur le site.',
            'tone': 'neutre', 'temperature': 0.7, 'length': 'moyenne',
        })


class Migration(migrations.Migration):
    dependencies = [('chat', '0023_message_interrupted_message_recovery_token'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='persona', name='is_big_persona', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='persona', name='share_with_personas', field=models.BooleanField(default=False)),
        migrations.AddConstraint(model_name='persona', constraint=models.UniqueConstraint(fields=('user',), condition=models.Q(is_big_persona=True), name='one_big_persona_per_user')),
        migrations.AddConstraint(model_name='persona', constraint=models.CheckConstraint(condition=models.Q(is_big_persona=False) | models.Q(share_with_personas=False), name='big_persona_not_shared_source')),
        migrations.RunPython(create_big_personas, migrations.RunPython.noop),
    ]
