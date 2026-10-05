import json
from uuid import UUID
from pathlib import Path
from asgiref.sync import sync_to_async

from django.conf import settings
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from founder.forms import ChatSendForm, MetricsForm, RegisterForm, StartupForm
from founder.models import (
    ChatAttachment, ChatMessage, ChatSession, MascotState,
    PitchReport, StartupProfile,
)
from founder.services.ai import AIServiceError, provider_label, stream_reply
from founder.services.mascot import update_mascot
from founder.services.memory import conversation_context, remember_user_message
from founder.services.onboarding import cofounder_opening, has_founder_conversation, has_profile_description
from founder.services.metrics import AXES, radar_grid, radar_points
from founder.services.pitch import finish_pitch
from founder.services.radar_assessment import assess_startup
from founder.services.achievements import achievement_cards, award_achievements
from founder.services.profile import evidence_display, grid_context, history_context, project_cards


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("startup_create")
    return render(request, "registration/register.html", {"form": form})


@require_GET
def about(request):
    return render(request, "founder/about.html")


@login_required
def home(request):
    cards = project_cards(request.user)
    if not cards:
        return redirect("startup_create")
    return render(request, "founder/projects.html", {"project_cards": cards, **grid_context()})


@login_required
def startup_create(request):
    form = StartupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            startup = form.save(commit=False)
            startup.owner = request.user
            startup.save()
            update_mascot(startup)
            session = _create_cofounder_session(startup)
        return redirect("chat_detail", startup_id=startup.id, session_id=session.id)
    return render(request, "founder/startup_form.html", {"form": form, "is_edit": False})


@login_required
def startup_edit(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    form = StartupForm(request.POST or None, instance=startup)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Описание стартапа обновлено.")
        return redirect("dashboard", startup_id=startup.id)
    return render(request, "founder/startup_form.html", {
        "form": form, "startup": startup, "is_edit": True,
    })


def _dashboard_context(startup, metrics_form=None, *, snapshot_id=None, history_page=1):
    snapshots = list(startup.metric_snapshots.all()[:6])
    latest = snapshots[0] if snapshots else None
    state, _ = MascotState.objects.get_or_create(startup=startup)
    initial = {key: getattr(latest, key) for key, _ in AXES} if latest else {}
    if latest:
        initial["assessment_notes"] = latest.assessment_notes
        initial.update({
            f"{key}_reason": latest.assessment_details.get(key, "")
            for key, _ in AXES
        })
    form = metrics_form if metrics_form is not None else MetricsForm(initial=initial)
    return {
        "startup": startup,
        "latest": latest,
        "mascot": state,
        "metrics_form": form,
        "edit_rows": [(label, form[key], form[f"{key}_reason"]) for key, label in AXES],
        "profile_rows": [
            {"key": key, "label": label, "score": getattr(latest, key, None),
             "reason": latest.assessment_details.get(key, "") if latest else "",
             "evidence": evidence_display(startup, latest, key)}
            for key, label in AXES
        ],
        "achievements": achievement_cards(startup),
        **history_context(startup, snapshot_id, history_page),
        "axes": [(label, getattr(latest, key, None)) for key, label in AXES],
        "ai_explanations": (
            [(label, latest.assessment_details.get(key, "")) for key, label in AXES]
            if latest and latest.source == latest.Source.AI else []
        ),
        "ai_available": settings.AI_PROVIDER != "demo",
        "radar_points": radar_points(latest),
        "radar_grid_25": radar_grid(25),
        "radar_grid_50": radar_grid(50),
        "radar_grid_75": radar_grid(75),
        "radar_grid_100": radar_grid(100),
        "recent_sessions": startup.chat_sessions.filter(mode=ChatSession.Mode.COFOUNDER)[:5],
        "metric_history": snapshots,
        "score_delta": (
            snapshots[0].overall_score - snapshots[1].overall_score
            if len(snapshots) > 1 else None
        ),
    }


@login_required
def dashboard(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    return render(request, "founder/dashboard.html", _dashboard_context(
        startup, snapshot_id=request.GET.get("snapshot"), history_page=request.GET.get("history_page", 1),
    ))


@login_required
@require_POST
def metrics_create(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    form = MetricsForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            metrics = form.save(commit=False)
            metrics.startup = startup
            previous = startup.metric_snapshots.first()
            metrics.assessment_details = {
                key: form.cleaned_data[f"{key}_reason"].strip()
                for key, _ in AXES
            }
            # Не выдаём ручную правку за новый вывод ИИ. Для неизменённых строк
            # оставляем проверенную ссылку на исходное сообщение.
            metrics.assessment_evidence = {
                key: (previous.assessment_evidence.get(key, {"status": "manual"})
                      if previous and getattr(previous, key) == getattr(metrics, key)
                      and previous.assessment_details.get(key, "") == metrics.assessment_details[key]
                      else {"status": "manual"})
                for key, _ in AXES
            }
            metrics.save()
            update_mascot(startup, metrics)
            award_achievements(startup, metrics)
        messages.success(request, "Таблица обновлена. Бруно отреагировал на новые оценки.")
        return redirect("dashboard", startup_id=startup.id)
    return render(request, "founder/dashboard.html", _dashboard_context(startup, form), status=400)


@login_required
@require_POST
def metrics_assess(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    try:
        assess_startup(startup)
        messages.success(request, "Бруно составил таблицу по пяти направлениям. Её можно доработать вручную.")
    except AIServiceError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard", startup_id=startup.id)


def _create_cofounder_session(startup):
    session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
    ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=cofounder_opening(startup),
        provider="system",
    )
    return session


@login_required
@require_POST
def chat_create(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    session = _create_cofounder_session(startup)
    return redirect("chat_detail", startup_id=startup.id, session_id=session.id)


@login_required
@require_POST
def chat_refine(request, startup_id, axis):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    labels = dict(AXES)
    if axis not in labels:
        raise Http404("Направление не найдено")
    session = startup.chat_sessions.filter(focus_axis=axis, completed_at__isnull=True).first()
    if session is None:
        questions = {
            "product": "Что уже работает в продукте и кто им пользуется?",
            "market": "Кто ваш покупатель и какие признаки интереса вы уже заметили?",
            "finance": "Как вы берёте оплату и какие доходы и расходы уже известны?",
            "team": "Кто входит в команду и за что отвечает каждый?",
            "pitch": "Как бы вы объяснили пользу сервиса клиенту в двух предложениях?",
        }
        latest = startup.metric_snapshots.first()
        reason = latest.assessment_details.get(axis, "") if latest else ""
        with transaction.atomic():
            session = ChatSession.objects.create(
                startup=startup, mode=ChatSession.Mode.COFOUNDER, focus_axis=axis,
                title=f"Уточняем: {labels[axis]}",
            )
            ChatMessage.objects.create(
                session=session, role=ChatMessage.Role.ASSISTANT, provider="system",
                content=f"Давайте уточним направление «{labels[axis]}».\n{reason}\n\n{questions[axis]}",
            )
    return redirect("chat_detail", startup_id=startup.id, session_id=session.id)


@login_required
@require_POST
def pitch_create(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.PITCH, title="Инвестор: продажи и спрос")
    ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=(f'Сегодня я инвестор проекта «{startup.name}». Обсудим продажи и спрос. '
                 + (f'В профиле ваш клиент: {startup.target_customer[:300].rstrip('.')}. ' if startup.target_customer else '')
                 + 'Кто конкретно принимает решение заплатить за ваш продукт и какую проблему решает этой покупкой? Если продаж пока нет, опишите предполагаемого первого покупателя.'),
        provider="system",
    )
    return redirect("chat_detail", startup_id=startup.id, session_id=session.id)


def _owned_session(request, startup_id, session_id):
    return get_object_or_404(
        ChatSession.objects.select_related("startup"),
        id=session_id,
        startup_id=startup_id,
        startup__owner=request.user,
    )


@login_required
def chat_detail(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    history = session.messages.order_by("-created_at", "-id")
    page_number = request.GET.get("page", 1)
    if request.GET.get("message"):
        try:
            cited_id = UUID(request.GET["message"])
        except ValueError as exc:
            raise Http404("Сообщение не найдено") from exc
        cited = get_object_or_404(history, pk=cited_id)
        newer = history.filter(Q(created_at__gt=cited.created_at) | Q(created_at=cited.created_at, id__gt=cited.id)).count()
        page_number = newer // 50 + 1
    page = Paginator(history.prefetch_related("attachments"), 50).get_page(page_number)
    report = PitchReport.objects.filter(session=session).first()
    mascot, _ = MascotState.objects.get_or_create(startup=session.startup)
    return render(request, "founder/chat.html", {
        "startup": session.startup,
        "mascot": mascot,
        "session": session,
        "chat_messages": list(reversed(page.object_list)),
        "chat_page": page,
        "report": report,
        "is_pitch": session.mode == ChatSession.Mode.PITCH,
        "has_assessment_context": has_profile_description(session.startup) or has_founder_conversation(session.startup),
        "ai_available": settings.AI_PROVIDER != "demo",
    })


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@login_required
@require_POST
def chat_send(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    if session.completed_at:
        return JsonResponse({"error": "Эта сессия уже завершена."}, status=409)
    form = ChatSendForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"error": form.errors.get_json_data()}, status=400)

    try:
        extracted_text = form.extracted_text()
    except forms.ValidationError as exc:
        return JsonResponse({"error": exc.messages[0]}, status=400)

    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            session=session,
            role=ChatMessage.Role.USER,
            content=form.cleaned_data["content"],
        )
        upload = form.cleaned_data.get("attachment")
        if upload:
            ChatAttachment.objects.create(
                message=user_message,
                file=upload,
                original_name=Path(upload.name).name[:255],
                content_type=upload.content_type or "text/plain",
                size_bytes=upload.size,
                extracted_text=extracted_text,
            )
        remember_user_message(user_message)

    context_messages, memories = conversation_context(session, user_message)
    provider, model_name = provider_label()

    def generate():
        chunks = []
        total_chars = 0
        try:
            for delta in stream_reply(session, context_messages, memories):
                if not isinstance(delta, str):
                    raise AIServiceError("Модель вернула некорректный ответ. Попробуйте ещё раз.")
                total_chars += len(delta)
                if total_chars > 60_000:
                    raise AIServiceError("Ответ слишком большой. Попросите Бруно ответить короче.")
                chunks.append(delta)
                yield _sse({"type": "delta", "text": delta})
            answer = "".join(chunks).strip()
            if not answer:
                raise AIServiceError("Модель не вернула текст ответа.")
            assistant_message = ChatMessage.objects.create(
                session=session,
                role=ChatMessage.Role.ASSISTANT,
                content=answer,
                provider=provider,
                model_name=model_name,
            )
            yield _sse({"type": "done", "message_id": str(assistant_message.id)})
        except AIServiceError as exc:
            yield _sse({"type": "error", "message": str(exc)})

    if settings.CHAT_BUFFERED_RESPONSES:
        # Keep the frontend event format, but deliver one ordinary HTTP body.
        response = HttpResponse("".join(generate()), content_type="text/plain; charset=utf-8")
    else:
        events = generate()
        if hasattr(request, 'scope'):
            # Daphne serves ASGI. Advance the sync provider/ORM iterator off the
            # event loop so existing Bruno chat keeps genuinely streaming.
            sync_events = events
            async def async_events():
                sentinel = object()
                try:
                    while True:
                        event = await sync_to_async(next, thread_sensitive=True)(sync_events, sentinel)
                        if event is sentinel:
                            break
                        yield event
                finally:
                    await sync_to_async(sync_events.close, thread_sensitive=True)()
            events = async_events()
        response = StreamingHttpResponse(events, content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
@require_POST
def pitch_finish(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    if session.mode != ChatSession.Mode.PITCH:
        raise Http404("Это не сессия питча.")
    try:
        finish_pitch(session)
        messages.success(request, "Бруно подготовил разбор питча.")
    except (AIServiceError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("chat_detail", startup_id=startup_id, session_id=session_id)
