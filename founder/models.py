"""Данные основателя, стартапа, диалогов и состояния Бруно."""

import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse


def default_user_handle():
    return f"founder_{uuid.uuid4().hex[:12]}"


def avatar_upload_path(instance, filename):
    return f"avatars/{instance.pk}/{uuid.uuid4().hex}.jpg"


handle_validator = RegexValidator(
    r"\A[a-z][a-z0-9_]{2,31}\Z",
    "Тег: 3–32 символа, латинские буквы, цифры и _. Первый символ — буква.",
)


def attachment_upload_path(instance, filename):
    """Имя файла на диске не зависит от имени, переданного пользователем."""
    suffix = Path(filename).suffix.lower()[:10]
    return (
        f"startup_uploads/{instance.message.session.startup_id}/"
        f"{uuid.uuid4().hex}{suffix}"
    )


class User(AbstractUser):
    # Для первого релиза вход по username; email обязателен и уникален.
    email = models.EmailField(unique=True)
    handle = models.CharField('Тег', max_length=32, unique=True, default=default_user_handle,
                              validators=[handle_validator])
    display_name = models.CharField('Имя в профиле', max_length=80, blank=True)
    bio = models.TextField('О себе', max_length=600, blank=True)
    occupation = models.CharField('Чем занимаетесь', max_length=120, blank=True)
    location = models.CharField('Город', max_length=100, blank=True)
    profile_website = models.URLField('Сайт или портфолио', blank=True)
    avatar = models.ImageField(upload_to=avatar_upload_path, blank=True)

    class Meta(AbstractUser.Meta):
        constraints = [models.CheckConstraint(
            condition=Q(handle__regex=r'^[a-z][a-z0-9_]{2,31}$'), name='user_handle_format',
        )]

    @property
    def public_name(self):
        return self.display_name or self.username

    def get_absolute_url(self):
        return reverse('user_profile', kwargs={'handle': self.handle})

    @property
    def avatar_url(self):
        if not self.avatar:
            return ''
        return reverse('user_avatar', kwargs={'user_id': self.pk}) + '?v=' + Path(self.avatar.name).stem


class StartupProfile(models.Model):
    class Stage(models.TextChoices):
        IDEA = "idea", "Идея"
        VALIDATION = "validation", "Проверка"
        TRACTION = "traction", "Первые результаты"
        GROWTH = "growth", "Рост"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="startups",
    )
    name = models.CharField(max_length=160)
    one_line_pitch = models.CharField(max_length=300, blank=True)
    problem = models.TextField(blank=True)
    solution = models.TextField(blank=True)
    target_customer = models.TextField(blank=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.IDEA)
    website = models.URLField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class StartupMetrics(models.Model):
    """Снимок радара: старые оценки не перезаписываются."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Оценка основателя"
        AI = "ai", "Оценка Бруно"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(
        StartupProfile,
        on_delete=models.CASCADE,
        related_name="metric_snapshots",
    )
    product = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    market = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    finance = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    team = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    pitch = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    assessment_notes = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    ai_model = models.CharField(max_length=100, blank=True)
    assessment_details = models.JSONField(default=dict, blank=True)
    assessment_evidence = models.JSONField(default=dict, blank=True)
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-assessed_at", "-id"]
        indexes = [
            models.Index(fields=["startup", "-assessed_at"], name="startup_metrics_recent"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(product__gte=0, product__lte=100), name="metrics_product_range"),
            models.CheckConstraint(condition=Q(market__gte=0, market__lte=100), name="metrics_market_range"),
            models.CheckConstraint(condition=Q(finance__gte=0, finance__lte=100), name="metrics_finance_range"),
            models.CheckConstraint(condition=Q(team__gte=0, team__lte=100), name="metrics_team_range"),
            models.CheckConstraint(condition=Q(pitch__gte=0, pitch__lte=100), name="metrics_pitch_range"),
        ]

    @property
    def overall_score(self):
        return round((self.product + self.market + self.finance + self.team + self.pitch) / 5)


class MascotState(models.Model):
    class Mood(models.TextChoices):
        CURIOUS = "curious", "Любопытный"
        SLEEPY = "sleepy", "Сонный"
        FOCUSED = "focused", "Сосредоточенный"
        CONFIDENT = "confident", "Уверенный"

    class Outfit(models.TextChoices):
        HOODIE = "hoodie", "Худи"
        PAJAMAS = "pajamas", "Пижама"
        JACKET = "jacket", "Пиджак"
        SUIT = "suit", "Деловой костюм"

    startup = models.OneToOneField(
        StartupProfile,
        on_delete=models.CASCADE,
        related_name="mascot_state",
    )
    mood = models.CharField(max_length=20, choices=Mood.choices, default=Mood.CURIOUS)
    outfit = models.CharField(max_length=20, choices=Outfit.choices, default=Outfit.HOODIE)
    reason = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class ChatSession(models.Model):
    class Mode(models.TextChoices):
        COFOUNDER = "cofounder", "ИИ-сооснователь"
        PITCH = "pitch", "Симулятор питча"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(
        StartupProfile,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    mode = models.CharField(max_length=20, choices=Mode.choices)
    title = models.CharField(max_length=160, blank=True)
    focus_axis = models.CharField(max_length=12, blank=True, choices=[
        ("product", "Продукт"), ("market", "Рынок"), ("finance", "Финансы"),
        ("team", "Команда"), ("pitch", "Ясность идеи"),
    ])
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["startup", "-created_at"], name="chat_session_recent"),
        ]


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        ASSISTANT = "assistant", "Ассистент"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(blank=True)
    provider = models.CharField(max_length=40, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["session", "created_at"], name="chat_message_order"),
        ]


class ChatAttachment(models.Model):
    """Приватный файл и извлечённый текст, доступный модели."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_upload_path, max_length=500)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    extracted_text = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class StartupMemory(models.Model):
    """Фрагмент контекста с указанием первоисточника."""

    class Kind(models.TextChoices):
        NOTE = "note", "Заметка"
        FACT = "fact", "Факт"
        ASSUMPTION = "assumption", "Гипотеза"
        DECISION = "decision", "Решение"
        RISK = "risk", "Риск"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(StartupProfile, on_delete=models.CASCADE, related_name="memories")
    source_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="derived_memories",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.NOTE)
    content = models.TextField()
    as_of_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    embedding_ref = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        indexes = [
            models.Index(fields=["startup", "is_active", "-created_at"], name="startup_memory_lookup"),
        ]

    def clean(self):
        if (
            self.startup_id
            and self.source_message_id
            and self.source_message.session.startup_id != self.startup_id
        ):
            raise ValidationError("Источник памяти должен принадлежать этому стартапу.")


class StartupAchievement(models.Model):
    """Полученное достижение сохраняется, даже если следующая оценка снизилась."""

    startup = models.ForeignKey(StartupProfile, on_delete=models.CASCADE, related_name="achievements")
    code = models.CharField(max_length=40)
    earned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["startup", "code"], name="unique_startup_achievement")]
        ordering = ["earned_at"]


class PitchReport(models.Model):
    """Результат тренировочного интервью с инвестором."""

    session = models.OneToOneField(ChatSession, on_delete=models.CASCADE, related_name="pitch_report")
    score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    summary = models.TextField()
    mistakes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(score__gte=0, score__lte=100), name="pitch_report_score_range"),
        ]

    def clean(self):
        if self.session_id and self.session.mode != ChatSession.Mode.PITCH:
            raise ValidationError("Отчёт возможен только для тренировочного питча.")


class BusinessAxis(models.TextChoices):
    PRODUCT = 'product', 'Продукт'
    MARKET = 'market', 'Рынок'
    FINANCE = 'finance', 'Финансы'
    TEAM = 'team', 'Команда'
    PITCH = 'pitch', 'Ясность идеи'


class BrunoTask(models.Model):
    """A concrete experiment; completing it never awards radar points directly."""
    class Status(models.TextChoices):
        TODO = 'todo', 'В работе'
        DONE = 'done', 'Выполнено'
        SKIPPED = 'skipped', 'Отложено'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(StartupProfile, on_delete=models.CASCADE, related_name='bruno_tasks')
    axis = models.CharField(max_length=12, choices=BusinessAxis.choices)
    title = models.CharField(max_length=160)
    instructions = models.TextField(max_length=1200)
    success_criterion = models.CharField(max_length=500)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO)
    ai_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(
            fields=['startup', 'axis'], condition=Q(status='todo'), name='one_open_task_per_axis',
        )]


class ProjectReview(models.Model):
    """Полный разбор проекта от Бруно: диагноз стадии, риски и план шагов.

    Разбор — мнение по словам основателя, а не проверка фактов. Шаги плана
    становятся заданиями только по явному выбору основателя.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(StartupProfile, on_delete=models.CASCADE, related_name='reviews')
    data = models.JSONField(default=dict)
    ai_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-created_at', '-id']


class MessageFeedback(models.Model):
    """Оценка ответа Бруно основателем. Неудачные ответы становятся новыми
    сценариями проверки (`manage.py bruno_feedback`)."""
    class Rating(models.IntegerChoices):
        UP = 1, 'Полезно'
        DOWN = -1, 'Мимо'

    message = models.OneToOneField(ChatMessage, on_delete=models.CASCADE, related_name='feedback')
    rating = models.SmallIntegerField(choices=Rating.choices)
    comment = models.CharField('Что было не так', max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class EvidenceEntry(models.Model):
    """Founder-reported observations, including negative and inconclusive results."""
    class Outcome(models.TextChoices):
        SUPPORTED = 'supported', 'Гипотеза получила поддержку'
        REFUTED = 'refuted', 'Гипотеза не подтвердилась'
        UNCLEAR = 'unclear', 'Пока недостаточно данных'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    startup = models.ForeignKey(StartupProfile, on_delete=models.CASCADE, related_name='evidence_entries')
    task = models.ForeignKey(BrunoTask, on_delete=models.SET_NULL, null=True, blank=True, related_name='evidence_entries')
    axis = models.CharField('Направление', max_length=12, choices=BusinessAxis.choices)
    claim = models.CharField('Что проверяли', max_length=300)
    observation = models.TextField('Что получилось: факты, числа, период', max_length=4000)
    source = models.CharField('Откуда сведения', max_length=500, blank=True)
    source_url = models.URLField('Ссылка на источник (необязательно)', max_length=1000, blank=True)
    observed_on = models.DateField('Дата наблюдения', default=timezone.localdate)
    outcome = models.CharField('Результат проверки', max_length=12, choices=Outcome.choices, default=Outcome.UNCLEAR)
    next_question = models.CharField('Что ещё нужно выяснить', max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-observed_on', '-created_at', '-id']

    def clean(self):
        super().clean()
        if self.task_id:
            if self.task.startup_id != self.startup_id:
                raise ValidationError({'task': 'Задание должно принадлежать этому проекту.'})
            if self.task.axis != self.axis:
                raise ValidationError({'axis': 'Выберите направление связанного задания.'})


class ProjectCard(models.Model):
    """Private working copy; community reads only the explicitly published snapshot."""

    startup = models.OneToOneField(StartupProfile, on_delete=models.CASCADE, related_name='project_card')
    name = models.CharField('Название', max_length=160)
    tagline = models.CharField('Идея в одном предложении', max_length=240, blank=True)
    summary = models.TextField('Коротко о проекте', max_length=600, blank=True)
    problem = models.TextField('Проблема', max_length=400, blank=True)
    solution = models.TextField('Решение', max_length=400, blank=True)
    audience = models.CharField('Для кого', max_length=300, blank=True)
    business_model = models.CharField('Как зарабатываем', max_length=300, blank=True)
    traction = models.TextField('Что уже получилось', max_length=400, blank=True)
    looking_for = models.CharField('Кого или что ищем', max_length=300, blank=True)
    stage = models.CharField('Стадия', max_length=20, choices=StartupProfile.Stage.choices, default='idea')
    website = models.URLField('Сайт', blank=True)
    share_radar = models.BooleanField('Показывать баллы радара в опубликованной карточке', default=False)
    revision = models.PositiveIntegerField(default=0)
    published_data = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)


class ProjectBookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='project_bookmarks')
    card = models.ForeignKey(ProjectCard, on_delete=models.CASCADE, related_name='bookmarks')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'card'], name='unique_project_bookmark')]


class DirectConversation(models.Model):
    """One private thread per pair, regardless of which card opened it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_low = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations_low')
    user_high = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations_high')
    source_card = models.ForeignKey(ProjectCard, on_delete=models.SET_NULL, null=True, blank=True)
    low_read_id = models.PositiveBigIntegerField(default=0)
    high_read_id = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['user_low', 'user_high'], name='unique_direct_pair'),
            models.CheckConstraint(condition=Q(user_low__lt=models.F('user_high')), name='ordered_direct_pair'),
        ]

    def other_user(self, user_id):
        if user_id not in (self.user_low_id, self.user_high_id):
            raise ValueError('Not a conversation participant')
        return self.user_high if user_id == self.user_low_id else self.user_low

    def read_id_for(self, user_id):
        if user_id not in (self.user_low_id, self.user_high_id):
            raise ValueError('Not a conversation participant')
        return self.low_read_id if user_id == self.user_low_id else self.high_read_id


class DirectMessage(models.Model):
    conversation = models.ForeignKey(DirectConversation, on_delete=models.CASCADE, related_name='direct_messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='direct_messages')
    client_id = models.UUIDField()
    content = models.TextField(max_length=4000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['conversation', 'id'], name='direct_message_history')]
        constraints = [models.UniqueConstraint(fields=['sender', 'client_id'], name='unique_direct_message_retry')]


class UserBlock(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocked_users')
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocked_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'blocked'], name='unique_user_block'),
            models.CheckConstraint(condition=~Q(user=models.F('blocked')), name='no_self_block'),
        ]
