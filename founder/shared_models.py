"""Tenant-owned data used only by the trusted, declarative BrunoHost runtime."""

import uuid

from django.db import models


class SharedSpace(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    startup = models.ForeignKey("StartupProfile", on_delete=models.CASCADE)
    version = models.ForeignKey("LabSiteVersion", null=True, on_delete=models.SET_NULL)
    schema = models.JSONField()
    live = models.BooleanField()
    import_digest = models.CharField(max_length=64, blank=True)
    file_bytes = models.PositiveBigIntegerField(default=0)
    file_count = models.PositiveIntegerField(default=0)
    record_count = models.PositiveIntegerField(default=0)
    member_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["startup"], condition=models.Q(live=True), name="bh_one_live_space"
            )
        ]


class SharedMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(SharedSpace, on_delete=models.CASCADE)
    username = models.CharField(max_length=60)
    password = models.CharField(max_length=256)
    role = models.CharField(max_length=40, default="member")
    session_epoch = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["space", "username"], name="bh_local_username")]


class SharedRecord(models.Model):
    space = models.ForeignKey(SharedSpace, on_delete=models.CASCADE)
    collection = models.CharField(max_length=40)
    owner = models.ForeignKey(SharedMember, on_delete=models.CASCADE)
    values = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=["space", "collection", "id"], name="bh_record_list")]


class SharedFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(SharedSpace, on_delete=models.CASCADE)
    owner = models.ForeignKey(SharedMember, on_delete=models.CASCADE)
    collection = models.CharField(max_length=40)
    name = models.CharField(max_length=150)
    mime = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    content = models.BinaryField()


class SharedBooking(models.Model):
    record = models.OneToOneField(SharedRecord, primary_key=True, on_delete=models.CASCADE)
    space = models.ForeignKey(SharedSpace, on_delete=models.CASCADE)
    resource = models.ForeignKey(SharedRecord, related_name="+", on_delete=models.CASCADE)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")), name="bh_positive_interval"
            )
        ]
