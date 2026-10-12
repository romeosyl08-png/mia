import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from api.models import AIModel, APIProvider


class CatalogBootstrapTests(TestCase):
    def run_command(self, name, **options):
        return call_command(name, stdout=io.StringIO(), stderr=io.StringIO(), **options)

    def test_seed_and_catalog_sync_initialize_cloud_models_on_fresh_database(self):
        self.run_command('seed_providers')
        self.assertFalse(AIModel.objects.filter(provider__slug='openrouter', is_active=True).exists())
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'data': [{
            'id': 'bootstrap/model', 'name': 'Bootstrap model',
            'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
            'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
        }]}).encode()
        with patch('api.management.commands.sync_openrouter_models.urlopen', return_value=response):
            self.run_command('sync_openrouter_models', allow_stale=True)
        model = AIModel.objects.get(provider__slug='openrouter', model_id='bootstrap/model')
        self.assertTrue(model.is_active)
        self.assertEqual(model.input_price_per_million, 1)
        self.assertEqual(model.output_price_per_million, 2)

    def test_outage_keeps_existing_catalog_but_does_not_silently_start_empty(self):
        provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter')
        with patch('api.management.commands.sync_openrouter_models.urlopen', side_effect=URLError('outage')):
            with self.assertRaises(CommandError):
                self.run_command('sync_openrouter_models', allow_stale=True)
            model = AIModel.objects.create(provider=provider, name='Existing', model_id='existing', input_price_per_million=1)
            self.run_command('sync_openrouter_models', allow_stale=True)
        model.refresh_from_db()
        self.assertTrue(model.is_active)

    def test_invalid_catalog_rolls_back_partial_updates(self):
        provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter')
        model = AIModel.objects.create(provider=provider, name='Existing', model_id='existing', input_price_per_million=1)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'data': [
            {'id': 'existing', 'pricing': {'prompt': '0.000002', 'completion': '0.000002'}},
            {'id': 'invalid', 'pricing': {'prompt': 'invalid', 'completion': '0.000002'}},
        ]}).encode()
        with patch('api.management.commands.sync_openrouter_models.urlopen', return_value=response):
            self.run_command('sync_openrouter_models', allow_stale=True)
        model.refresh_from_db()
        self.assertEqual(model.input_price_per_million, 1)
