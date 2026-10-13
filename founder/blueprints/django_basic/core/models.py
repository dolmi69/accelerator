import uuid
from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="first_chats")
    second = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="second_chats")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["first", "second"], name="chat_pair"),
            models.CheckConstraint(condition=Q(first__lt=F("second")), name="chat_pair_order"),
        ]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    nonce = models.UUIDField(default=uuid.uuid4)
    content = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["sender", "nonce"], name="message_nonce")]


class SavedResult(models.Model):
    """Private text results; never render user content as HTML."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_results')
    title = models.CharField(max_length=120)
    content = models.TextField(max_length=1800)
    nonce = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['owner', 'nonce'], name='result_nonce')]


class RateBucket(models.Model):
    """Shared, bounded counters; work across ASGI threads and processes."""
    key = models.CharField(max_length=100, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    window = models.BigIntegerField(default=0)


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='site_profile')
    display_name = models.CharField(max_length=80, blank=True)
    bio = models.TextField(max_length=1000, blank=True)
    avatar = models.FileField(upload_to='avatars/', blank=True)
    email_key = models.EmailField(unique=True, null=True, blank=True)
    email_notifications = models.BooleanField(default=False)


class SiteControl(models.Model):
    """A single row protects the initial owner claim and site-wide preferences."""
    setup_claimed = models.BooleanField(default=False)
    allow_listings = models.BooleanField(default=False)


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Владелец'
        EDITOR = 'editor', 'Сотрудник'
        CLIENT = 'client', 'Участник'
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='site_membership')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)


class Invitation(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(blank=True)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Membership.Role.choices, default=Membership.Role.EDITOR)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)


class Item(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=6000)
    category = models.CharField(max_length=60, blank=True, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stock = models.PositiveIntegerField(null=True, blank=True)
    published = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.CheckConstraint(condition=Q(price__isnull=True) | Q(price__gte=0), name='item_price_nonnegative')]

    def __str__(self):
        return self.title


class Asset(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, null=True, blank=True, related_name='assets')
    file = models.FileField(upload_to='assets/')
    name = models.CharField(max_length=150)
    image = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'item'], name='favorite_unique')]


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        CONTACTED = 'contacted', 'В работе'
        CLOSED = 'closed', 'Закрыта'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=80)
    email = models.EmailField()
    text = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.CharField(max_length=250)
    url = models.CharField(max_length=250, blank=True)
    dedupe_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class Slot(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='slots')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reserved = models.BooleanField(default=False)

    class Meta:
        ordering = ['starts_at', 'pk']
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at')), name='slot_positive_duration')]


class Booking(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    slot = models.ForeignKey(Slot, on_delete=models.CASCADE)
    canceled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['slot'], condition=Q(canceled=False), name='slot_single_booking')]


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новый'
        PROCESSING = 'processing', 'В работе'
        DONE = 'done', 'Выполнен'
        CANCELED = 'canceled', 'Отменён'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='purchases')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sales')
    nonce = models.UUIDField()
    total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    contact = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def payment_settled(self):
        return Payment.objects.filter(order=self).exclude(status='canceled').exists()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'nonce'], name='checkout_nonce')]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True)
    title = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    stock_reserved = models.BooleanField(default=False)


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    provider_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=24, default='pending')
    confirmation_url = models.URLField(max_length=1000, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)


class Review(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField()
    text = models.TextField(max_length=2000)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'item'], name='one_review_per_user'),
            models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name='review_rating_range')]


class Page(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=100, unique=True)
    text = models.TextField(max_length=12000)
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
