from django.db import migrations


PERSONA_APPEARANCES = {
    "Assistant général": {
        "appearance_name": "Le Comptoir",
        "appearance_avatar": "🐘",
        "appearance_accent": "#E8A33D",
        "appearance_theme": "light",
    },
    "Tuteur de langues": {
        "appearance_name": "Tuteur de langues",
        "appearance_avatar": "📚",
        "appearance_accent": "#3F8E8A",
        "appearance_theme": "light",
    },
    "Coach code": {
        "appearance_name": "Coach code",
        "appearance_avatar": "⌘",
        "appearance_accent": "#4E9F3D",
        "appearance_theme": "dark",
    },
}


def seed_persona_appearance(apps, schema_editor):
    Persona = apps.get_model("chat", "Persona")
    database = schema_editor.connection.alias
    for name, defaults in PERSONA_APPEARANCES.items():
        personas = Persona.objects.using(database).filter(name=name)
        for persona in personas.iterator():
            updates = {
                field: value
                for field, value in defaults.items()
                if not getattr(persona, field)
            }
            if updates:
                Persona.objects.using(database).filter(pk=persona.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("chat", "0010_alter_generatedmedia_image")]

    operations = [migrations.RunPython(seed_persona_appearance, migrations.RunPython.noop)]