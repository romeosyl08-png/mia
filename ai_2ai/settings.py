from pathlib import Path
import os
from decimal import Decimal
import environ
import dj_database_url

env = environ.Env()


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

environ.Env.read_env(BASE_DIR / ".env")
FERNET_KEY = env("FERNET_KEY")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env(
    "SECRET_KEY",
    default="local-development-secret-key-change-this-value-before-production-2026",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=True)
BARA_CODE_EXECUTION_ENABLED = env.bool("BARA_CODE_EXECUTION_ENABLED", default=False)
BARA_SANDBOX_IMAGE = env('BARA_SANDBOX_IMAGE', default='ai2ai-runtime:1')
BARA_SANDBOX_RUNTIME = env('BARA_SANDBOX_RUNTIME', default='docker')
EMBEDDING_MODEL = env('EMBEDDING_MODEL', default='')
EMBEDDING_API_URL = env('EMBEDDING_API_URL', default='https://openrouter.ai/api/v1/embeddings')
EMBEDDING_API_KEY = env('EMBEDDING_API_KEY', default='')

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '')
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID",default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET",default="")
PUBLIC_BASE_URL = env(
    "PUBLIC_BASE_URL",
    default=os.environ.get("RENDER_EXTERNAL_URL", ""),
).rstrip("/")
GOOGLE_REDIRECT_URI = env("GOOGLE_REDIRECT_URI", default="").strip()
MICROSOFT_CLIENT_ID = env("MICROSOFT_CLIENT_ID", default="")
MICROSOFT_CLIENT_SECRET = env("MICROSOFT_CLIENT_SECRET", default="")
MICROSOFT_TENANT_ID = env("MICROSOFT_TENANT_ID", default="common")
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
DEFAULT_CURRENCY = env("DEFAULT_CURRENCY", default="XOF").upper()
EUR_PER_USD = Decimal(env("EUR_PER_USD", default="0.92"))
IP_CURRENCY_LOOKUP_ENABLED = env.bool("IP_CURRENCY_LOOKUP_ENABLED", default=True)
IP_CURRENCY_LOOKUP_URL = env(
    "IP_CURRENCY_LOOKUP_URL",
    default="https://ipapi.co/{ip}/country/",
)
IP_CURRENCY_LOOKUP_TIMEOUT_SECONDS = env.float(
    "IP_CURRENCY_LOOKUP_TIMEOUT_SECONDS", default=1.5
)
IP_CURRENCY_CACHE_SECONDS = env.int("IP_CURRENCY_CACHE_SECONDS", default=86400)
PAYDUNYA_MASTER_KEY = env("PAYDUNYA_MASTER_KEY", default="")
PAYDUNYA_PRIVATE_KEY = env("PAYDUNYA_PRIVATE_KEY", default="")
PAYDUNYA_TOKEN = env("PAYDUNYA_TOKEN", default="")
PAYDUNYA_STORE_NAME = env("PAYDUNYA_STORE_NAME", default="2AI")
PAYDUNYA_XOF_PER_USD = Decimal(env("PAYDUNYA_XOF_PER_USD", default="600"))
PAYDUNYA_MINIMUM_XOF = env.int("PAYDUNYA_MINIMUM_XOF", default=200)
PAYDUNYA_CHECKOUT_URL = env(
    "PAYDUNYA_CHECKOUT_URL",
    default="",
)
OPENROUTER_IMAGE_MODEL = env('OPENROUTER_IMAGE_MODEL', default='bytedance-seed/seedream-4.5')
OPENROUTER_VIDEO_MODEL = env('OPENROUTER_VIDEO_MODEL', default='alibaba/wan-3.0')
STUDIO_IMAGE_EDIT_MODEL = env('STUDIO_IMAGE_EDIT_MODEL', default='google/gemini-3.1-flash-image')
STUDIO_MEDIA_TIMEOUT_SECONDS = env.int('STUDIO_MEDIA_TIMEOUT_SECONDS', default=600)
MEDIA_GENERATION_CREDITS = {
    'image': env.int('MEDIA_IMAGE_CREDITS', default=630),
    'video': env.int('MEDIA_VIDEO_CREDITS', default=16_800),
    'audio': env.int('MEDIA_AUDIO_CREDITS', default=2_000),
}
MEDIA_GENERATION_MODELS = {
    'fal': {
        'image': env('FAL_IMAGE_MODEL', default='fal-ai/flux/schnell'),
        'video': env('FAL_VIDEO_MODEL', default='fal-ai/veo3.1'),
        'audio': env('FAL_AUDIO_MODEL', default='fal-ai/musicgen'),
    },
    'replicate': {
        'image': env('REPLICATE_IMAGE_MODEL', default='black-forest-labs/flux-schnell'),
        'video': env('REPLICATE_VIDEO_MODEL', default='minimax/video-01'),
        'audio': env('REPLICATE_AUDIO_MODEL', default='meta/musicgen'),
    },
    'apiframe': {
        'image': env('APIFRAME_IMAGE_MODEL', default='flux-2-pro'),
        'video': env('APIFRAME_VIDEO_MODEL', default='seedance-2.5'),
        'audio': env('APIFRAME_AUDIO_MODEL', default='suno'),
    },
}
API_RATE_LIMIT_REQUESTS = env.int('API_RATE_LIMIT_REQUESTS', default=60)
API_RATE_LIMIT_WINDOW_SECONDS = env.int('API_RATE_LIMIT_WINDOW_SECONDS', default=60)

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'chat.apps.ChatConfig',
    'studio.apps.StudioConfig',
    'ai_user.apps.AiUserConfig',
    'api.apps.ApiConfig',
    'bara',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ai_2ai.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ai_2ai.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

database_url = env("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
DATABASES = {
    "default": dj_database_url.parse(
        database_url,
        conn_max_age=600,
        conn_health_checks=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
            if not DEBUG else
            'django.contrib.staticfiles.storage.StaticFilesStorage'
        ),
    },
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)


SEARCH_API_KEY = env("SEARCH_API_KEY", default="")

MEDIA_URL = '/media/'
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))
PRIVATE_MEDIA_ROOT = Path(env('PRIVATE_MEDIA_ROOT', default=str(BASE_DIR / 'private_media')))
CHAT_HISTORY_MAX_MESSAGES = env.int('CHAT_HISTORY_MAX_MESSAGES', default=48)
CHAT_HISTORY_MAX_CHARS = env.int('CHAT_HISTORY_MAX_CHARS', default=48_000)

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

REDIS_URL = env("REDIS_URL", default="")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }

BARA_USER_STORAGE_LIMIT_MB = env.int("BARA_USER_STORAGE_LIMIT_MB", default=50)
BARA_USER_FILE_LIMIT = env.int("BARA_USER_FILE_LIMIT", default=300)

API_MAX_OUTPUT_TOKENS = env.int("API_MAX_OUTPUT_TOKENS", default=4096)
LOGIN_URL = "/auth/"
