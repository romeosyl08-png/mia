from django.db import migrations


DEFAULT_PERSONAS = [
    {"name": "Assistant général", "prompt": "Tu es un assistant poli, clair et utile.", "tone": "neutre", "temperature": 0.7, "length": "moyenne"},
    {"name": "Tuteur de langues", "prompt": "Tu es un tuteur patient qui aide à apprendre une langue étrangère, corrige en douceur et explique la grammaire simplement.", "tone": "pedagogue", "temperature": 0.5, "length": "moyenne"},
    {"name": "Coach code", "prompt": "Tu es un mentor en développement logiciel, tu donnes des explications concrètes avec des exemples de code.", "tone": "decontracte", "temperature": 0.4, "length": "longue"},
]


def create_missing_personas(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Persona = apps.get_model('chat', 'Persona')
    for user in User.objects.all():
        if not Persona.objects.filter(user_id=user.id).exists():
            Persona.objects.bulk_create([
                Persona(user_id=user.id, **persona_data)
                for persona_data in DEFAULT_PERSONAS
            ])


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '0005_move_api_models'),
        ('ai_user', '0003_rechargepurchase'),
    ]

    operations = [migrations.RunPython(create_missing_personas, migrations.RunPython.noop)]