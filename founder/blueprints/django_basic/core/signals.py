from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Asset, Profile


@receiver(post_delete,sender=Asset)
@receiver(post_delete,sender=Profile)
def remove_file_after_commit(sender,instance,**kwargs):
    file = instance.file if sender is Asset else instance.avatar
    if file.name:
        storage,name = file.storage,file.name
        transaction.on_commit(lambda:storage.delete(name))
