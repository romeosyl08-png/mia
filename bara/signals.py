from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import FileVersion


@receiver(post_delete,sender=FileVersion)
def remove_version_blob(sender,instance,**kwargs):
    if instance.binary_file:
        storage,name = instance.binary_file.storage,instance.binary_file.name
        transaction.on_commit(lambda:storage.delete(name))
