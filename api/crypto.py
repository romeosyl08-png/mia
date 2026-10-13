"""Chiffrement des clés fournisseur au repos.

`MultiFernet` permet une rotation sans interruption : on chiffre avec la clé
courante et on déchiffre avec la courante ou les anciennes. Sans cela,
changer `FERNET_KEY` rend toutes les clés enregistrées illisibles d'un coup.
"""

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver

_fernet = None


def _load_keys():
    raw = getattr(settings, "FERNET_KEY", "")
    if not raw:
        raise ImproperlyConfigured(
            "FERNET_KEY est obligatoire : elle chiffre les clés fournisseur en base. "
            "Génère-la avec : python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())" puis renseigne-la dans .env '
            "(voir .env.example)."
        )
    keys = [raw]
    previous = getattr(settings, "FERNET_PREVIOUS_KEYS", "") or ""
    keys.extend(key.strip() for key in previous.split(",") if key.strip())
    fernet_keys = []
    for key in keys:
        try:
            fernet_keys.append(Fernet(key.encode() if isinstance(key, str) else key))
        except Exception as error:
            raise ImproperlyConfigured(
                f"Clé Fernet invalide : {error!s}. Attendu : 32 octets encodés en base64 URL-safe."
            ) from error
    return MultiFernet(fernet_keys)


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = _load_keys()
    return _fernet


def encrypt_value(plain_text: str) -> str:
    return _get_fernet().encrypt(plain_text.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    return _get_fernet().decrypt(encrypted_text.encode()).decode()


@receiver(setting_changed)
def _reset_fernet(*, setting, **kwargs):
    """Recharge les clés quand un réglage change (tests, rotation à chaud)."""
    global _fernet
    if setting in {"FERNET_KEY", "FERNET_PREVIOUS_KEYS"}:
        _fernet = None
