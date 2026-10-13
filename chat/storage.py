from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """Stockage hors de MEDIA_ROOT, jamais exposé par le serveur Web.

    `url()` lève volontairement une erreur : les fichiers concernés
    (médias générés, pièces jointes des espaces Bara) ne doivent être
    servis que par une vue qui vérifie les droits de l'utilisateur.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        super().__init__(*args, **kwargs)

    def url(self, name):
        raise ValueError("Private media must be served through the owner-checked view.")


# Ancien nom, conservé pour ne pas casser les migrations existantes.
PrivateGeneratedMediaStorage = PrivateMediaStorage
