from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateGeneratedMediaStorage(FileSystemStorage):
	def __init__(self, *args, **kwargs):
		kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
		super().__init__(*args, **kwargs)

	def url(self, name):
		raise ValueError("Generated media must be served through the owner-checked view.")
