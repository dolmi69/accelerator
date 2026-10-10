import json
from datetime import timedelta
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
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from founder.forms import ChatSendForm, MetricsForm, RegisterForm, StartupForm
from founder.models import (
    BrunoTask, BusinessAxis, ChatAttachment, ChatMessage, ChatSession, MascotState, MentorIdea, MessageFeedback,
    PanelVerdict, PitchReport, ProjectPicture, StartupProfile,
)
from founder.services import mentor
from founder.services.ai import AIServiceError, provider_label, stream_reply
from founder.services.bruno import looks_like_evidence
from founder.services.mascot import update_mascot
from founder.services.memory import conversation_context, remember_user_message
from founder.services.onboarding import cofounder_opening, has_founder_conversation, has_profile_description
from founder.services.metrics import AXES, radar_grid, radar_points
from founder.services import panel
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
        # Новая встреча продолжает прошлую: главный пробел и открытое задание.
        content=mentor.followup_opening(startup) or cofounder_opening(startup),
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
            "product": "Что в продукте уже работает и кто им пользуется?",
            "market": "Кто ваш покупатель и какие признаки интереса уже видны?",
            "finance": "Как планируете брать оплату и какие доходы или расходы уже известны?",
            "team": "Кто сейчас в команде и за что отвечает каждый?",
            "pitch": "Как объяснить пользу сервиса клиенту в двух предложениях?",
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
                content=(f"Давай подтянем направление «{labels[axis]}». "
                         + (f"Вот что я отметил в прошлый раз: {reason}\n\n" if reason else "\n\n")
                         + questions[axis]),
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


@login_required
@require_POST
def panel_create(request, startup_id):
    startup = get_object_or_404(StartupProfile, id=startup_id, owner=request.user)
    session = panel.create_panel(startup)
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
    chat_messages = list(reversed(page.object_list))
    feedback = {item.message_id: item for item in MessageFeedback.objects.filter(message__session=session)}
    ideas = {}
    for idea in MentorIdea.objects.filter(message__session=session):
        ideas.setdefault(idea.message_id, []).append(idea)
    previous_user_text = ""
    for message in chat_messages:
        # Под ответом на вопрос о рынке — ссылка на полный анализ: модель о нём часто забывает.
        message.market_link = (session.mode == ChatSession.Mode.COFOUNDER and message.role == ChatMessage.Role.ASSISTANT
                               and message.provider != "system" and mentor.answer_kind(previous_user_text) == "market")
        if message.role == ChatMessage.Role.USER:
            previous_user_text = message.content
        message.mentor_ideas = ideas.get(message.id, [])
        # Кнопка дневника под сообщением с результатом проверки; оценка под ответом Бруно.
        message.evidence_candidate = (session.mode == ChatSession.Mode.COFOUNDER
                                      and message.role == ChatMessage.Role.USER
                                      and looks_like_evidence(message.content))
        message.rateable = message.role == ChatMessage.Role.ASSISTANT and message.provider != "system"
        message.user_feedback = feedback.get(message.id)
        message.shark = panel.shark_info(message.speaker)
    is_panel = session.mode == ChatSession.Mode.PANEL
    return render(request, "founder/chat.html", {
        "startup": session.startup,
        "mascot": mascot,
        "session": session,
        "chat_messages": chat_messages,
        "chat_page": page,
        "report": report,
        "is_pitch": session.mode == ChatSession.Mode.PITCH,
        "is_panel": is_panel,
        "is_training": session.is_training,
        "panel": _panel_context(session) if is_panel else None,
        "picture": mentor.picture_view(session.startup) if session.mode == ChatSession.Mode.COFOUNDER else None,
        "quick_replies": QUICK_REPLIES if session.mode == ChatSession.Mode.COFOUNDER else (),
        "has_assessment_context": has_profile_description(session.startup) or has_founder_conversation(session.startup),
        "ai_available": settings.AI_PROVIDER != "demo",
    })


# Подсказки под полем ввода: начинающему проще нажать, чем сформулировать.
QUICK_REPLIES = ("Объясни подробнее", "Давай подумаем, как улучшить проект", "Как можно развить идею?",
                 "Насколько актуальна идея и кто конкуренты?", "Давай посчитаем деньги", "Не знаю",
                 "Помоги подготовиться к встрече с куратором", "Подведи итог встречи")


@login_required
@require_POST
def idea_task(request, startup_id, session_id, idea_id):
    session = _owned_session(request, startup_id, session_id)
    idea = get_object_or_404(MentorIdea, pk=idea_id, message__session=session)
    try:
        mentor.idea_to_task(idea)
        messages.success(request, "Идея в заданиях. Результат проверки запишите в дневник.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(reverse("chat_detail", args=[startup_id, session_id]) + f"#message-{idea.message_id}")


@login_required
@require_POST
def picture_edit(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    picture, _ = ProjectPicture.objects.get_or_create(startup=session.startup)
    facts = dict(picture.facts)
    for key, _label in mentor.AREAS:
        text = " ".join(request.POST.get(key, "").split())[:300]
        status = request.POST.get(f"{key}_status")
        if status not in mentor.STATUSES:
            status = "guess" if text else "unknown"
        facts[key] = {"text": "" if status == "unknown" else text, "status": status if text else "unknown"}
    picture.facts = facts
    picture.save(update_fields=["facts", "updated_at"])
    messages.success(request, "Картина проекта обновлена. Бруно учтёт правки в следующем ответе.")
    return redirect("chat_detail", startup_id=startup_id, session_id=session_id)


def _answers_left_label(left):
    word = "ответ" if left % 10 == 1 and left % 100 != 11 else (
        "ответа" if left % 10 in (2, 3, 4) and left % 100 not in (12, 13, 14) else "ответов")
    return f"Ещё {left} {word} до голосования"


def _panel_context(session):
    answers = panel.answer_count(session)
    verdict = PanelVerdict.objects.filter(session=session).first()
    open_axes = set(session.startup.bruno_tasks.filter(status=BrunoTask.Status.TODO).values_list("axis", flat=True))
    axis_labels = dict(BusinessAxis.choices)
    votes = []
    for index, vote in enumerate(verdict.votes if verdict else []):
        shark = panel.shark_info(vote["shark"])
        votes.append({**vote, "index": index, "shark_info": shark, "axis_label": axis_labels[shark["axis"]],
                      "axis_busy": not vote.get("task_id") and shark["axis"] in open_axes})
    left = max(0, panel.MIN_ANSWERS - answers)
    return {
        "answers": answers, "min_answers": panel.MIN_ANSWERS, "max_answers": panel.MAX_ANSWERS,
        "left_label": _answers_left_label(left), "can_vote": answers >= panel.MIN_ANSWERS,
        "full": answers >= panel.MAX_ANSWERS,
        "progress": min(100, answers * 100 // panel.MIN_ANSWERS),
        "speaker": panel.shark_info(panel.current_speaker(session)),
        "sharks": [panel.shark_info(key) for key in panel.ORDER],
        "verdict": verdict, "votes": votes,
    }


@login_required
@require_POST
def message_feedback(request, startup_id, session_id, message_id):
    session = _owned_session(request, startup_id, session_id)
    message = get_object_or_404(session.messages.exclude(provider="system"), pk=message_id,
                                role=ChatMessage.Role.ASSISTANT)
    rating = {"up": MessageFeedback.Rating.UP, "down": MessageFeedback.Rating.DOWN}.get(request.POST.get("rating"))
    if rating is None:
        return HttpResponse("Неизвестная оценка.", status=400)
    comment = request.POST.get("comment", "").strip()[:500] if rating == MessageFeedback.Rating.DOWN else ""
    MessageFeedback.objects.update_or_create(message=message, defaults={"rating": rating, "comment": comment})
    return redirect(reverse("chat_detail", args=[startup_id, session_id]) + f"#message-{message.id}")


# Потолок одного ответа в чате (в символах): обычный ответ Бруно меньше 2 000.
CHAT_REPLY_CHAR_LIMIT = 60_000


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@login_required
@require_POST
def chat_send(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    if session.completed_at:
        return JsonResponse({"error": "Эта сессия уже завершена."}, status=409)
    is_panel = session.mode == ChatSession.Mode.PANEL
    if is_panel and panel.answer_count(session) >= panel.MAX_ANSWERS:
        return JsonResponse({"error": "Акулы услышали достаточно. Нажмите «Голосование»."}, status=409)
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
    turn = panel.next_turn(session) if is_panel else None

    def speaker_event(key):
        shark = panel.SHARKS[key]
        return _sse({"type": "speaker", "speaker": key, "name": shark["name"],
                     "title": shark["title"], "initial": shark["initial"]})

    def generate():
        # Панель: реплика соседа и ответ акулы хода сохраняются отдельными сообщениями.
        parts = [(turn.speaker if turn else "", [])]
        try:
            if turn:
                yield speaker_event(turn.speaker)
            if turn:
                events = panel.split_aside(stream_reply(session, context_messages, memories, turn=turn), turn.speaker)
            else:
                if (session.mode == ChatSession.Mode.COFOUNDER and settings.BRUNO_MENTOR_PLAN
                        and settings.AI_PROVIDER != "demo"):
                    searching = mentor.answer_kind(user_message.content) == "market"
                    yield _sse({"type": "status", "text": "Бруно ищет в открытых источниках…" if searching
                                else "Бруно думает над проектом…"})
                events = (("text", delta) for delta in stream_reply(session, context_messages, memories))
            total_chars = 0
            for kind, delta in events:
                if kind == "speaker":
                    parts.append((delta, []))
                    yield speaker_event(delta)
                    continue
                if not isinstance(delta, str):
                    raise AIServiceError("Модель вернула некорректный текст ответа.")
                # Восстановлено из beta 0.4 (потерялось при мерже): сбойный поток не должен
                # бесконечно писать в базу и браузер.
                total_chars += len(delta)
                if total_chars > CHAT_REPLY_CHAR_LIMIT:
                    raise AIServiceError("Ответ слишком большой. Попросите Бруно ответить короче.")
                parts[-1][1].append(delta)
                yield _sse({"type": "delta", "text": delta})
            answers = [(speaker, "".join(chunks).strip()) for speaker, chunks in parts]
            answers = [(speaker, text) for speaker, text in answers if text]
            if not answers:
                raise AIServiceError("Модель не вернула текст ответа.")
            started = timezone.now()
            for offset, (speaker, text) in enumerate(answers):
                assistant_message = ChatMessage.objects.create(
                    session=session,
                    role=ChatMessage.Role.ASSISTANT,
                    content=text,
                    speaker=speaker,
                    provider=provider,
                    model_name=model_name,
                    created_at=started + timedelta(microseconds=offset),
                )
            mentor.save_ideas(getattr(session, "mentor_plan", None), assistant_message)
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
                    # Восстановлено из beta 0.4: клиент ушёл посреди ответа — закрываем
                    # генератор сразу, чтобы поток модели не висел до сборки мусора.
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


@login_required
@require_POST
def panel_vote(request, startup_id, session_id):
    session = _owned_session(request, startup_id, session_id)
    if session.mode != ChatSession.Mode.PANEL:
        raise Http404("Это не панель акул.")
    try:
        panel.run_vote(session)
    except (AIServiceError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("chat_detail", startup_id=startup_id, session_id=session_id)
    return redirect(reverse("chat_detail", args=[startup_id, session_id]) + "?reveal=1#verdict")


@login_required
@require_POST
def panel_vote_task(request, startup_id, session_id, index):
    session = _owned_session(request, startup_id, session_id)
    verdict = get_object_or_404(PanelVerdict, session=session)
    try:
        task = panel.condition_to_task(verdict, index)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    if task is None:
        messages.error(request, "По этому направлению уже есть задание в работе. Сначала завершите или отложите его.")
        return redirect(reverse("chat_detail", args=[startup_id, session_id]) + "#verdict")
    messages.success(request, "Условие акулы добавлено в задания. Результат запишите в дневник, когда сделаете.")
    return redirect("tasks", startup_id=startup_id)
