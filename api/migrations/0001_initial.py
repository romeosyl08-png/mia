import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('chat', '0005_move_api_models'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.CreateModel(
            name='APIProvider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Nom lisible, ex: 'Anthropic Claude'", max_length=100, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=100, unique=True)),
                ('base_url', models.URLField(blank=True, help_text="URL de base de l'API, ex: https://api.anthropic.com")),
                ('docs_url', models.URLField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
                'db_table': 'chat_apiprovider',
            },
        )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.CreateModel(
            name='APIKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Nom donné à cette clé', max_length=100)),
                ('encrypted_key', models.TextField(help_text='Clé API chiffrée')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='keys', to='api.apiprovider')),
            ],
            options={
                'ordering': ['provider__name', 'name'],
                'unique_together': {('provider', 'name')},
                'db_table': 'chat_apikey',
            },
        )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.CreateModel(
            name='AIModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="Nom affiché dans l'interface", max_length=100)),
                ('model_id', models.CharField(help_text="Identifiant attendu par l'API", max_length=150)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='models', to='api.apiprovider')),
            ],
            options={
                'ordering': ['provider__name', 'name'],
                'unique_together': {('provider', 'model_id')},
                'db_table': 'chat_aimodel',
            },
        )],
        ),
    ]
