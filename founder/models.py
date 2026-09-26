"""Данные основателя, стартапа, диалогов и состояния Бруно."""

import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


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
