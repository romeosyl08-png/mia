"""Isolated, offline SQLite settings; never touches the configured application DB."""
from ai_2ai.settings import *  # noqa: F403
import os
import dj_database_url

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
if os.environ.get('EVALUATION_TEST_POSTGRES_DSN'):
    DATABASES = {'default': dj_database_url.parse(os.environ['EVALUATION_TEST_POSTGRES_DSN'])}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
IP_CURRENCY_LOOKUP_ENABLED = False
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
COMPARISON_ENABLED = False
COMPARISON_AUTOMATIC_ENABLED = False
EVALUATION_REWARDS_ENABLED = False
EVALUATION_ROUTING_ENABLED = False
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'root': {'level': 'WARNING'}}
