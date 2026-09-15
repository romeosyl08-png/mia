from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '0004_conversation_message_persona_conversation_persona'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='AIModel'),
                migrations.DeleteModel(name='APIKey'),
                migrations.DeleteModel(name='APIProvider'),
            ],
        ),
    ]