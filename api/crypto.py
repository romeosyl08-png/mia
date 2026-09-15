from cryptography.fernet import Fernet
from django.conf import settings

_fernet = Fernet(settings.FERNET_KEY.encode())


def encrypt_value(plain_text: str) -> str:
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    return _fernet.decrypt(encrypted_text.encode()).decode()