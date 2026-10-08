"""Community testing: explicit publication, opt-in capture, private results."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from founder.models import CoinTransaction, LabPublication, LabTestEvent, LabTestSession, StartupProfile
from founder.lab_views import _owned_startup, _version, preview_response
from founder.services.access import member_filter, team_user_ids
from founder.services.coins import award, balance
from founder.services.json_utils import bounded_json_loads
from founder.services.request_limits import consume_limit, RequestLimitExceeded


def _published(request, startup_id):
    # Private prototypes and unfinished cards are visible only to the project team.
    audience = member_filter(request.user, 'startup__') | Q(
        visibility=LabPublication.Visibility.PUBLIC, startup__project_card__published_at__isnull=False)
    return get_object_or_404(LabPublication.objects.select_related('version', 'startup').filter(audience),
        startup_id=startup_id, version__startup_id=startup_id, startup__owner__is_active=True)


def _session(request, startup_id, session_id):
    publication = _published(request, startup_id)
    session = get_object_or_404(LabTestSession, pk=session_id, version=publication.version, tester=request.user)
    return publication, session


@login_required
@require_POST
def lab_publish(request, startup_id):
    startup = _owned_startup(request, startup_id)
    visibility = request.POST.get('visibility')
    if visibility is not None and visibility not in LabPublication.Visibility.values:
        return HttpResponse('Выберите доступ: приватно или для всех.', status=400)
    version = None
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        if request.POST.get('action') == 'hide':
            LabPublication.objects.filter(startup=startup).delete()
            messages.success(request, 'Прототип убран из карточки. Результаты тестов сохранены.')
        else:
            version = _version(startup, request.POST.get('version'))
            previous = LabPublication.objects.filter(startup=startup).first()
            visibility = visibility or (previous.visibility if previous else LabPublication.Visibility.PUBLIC)
            LabPublication.objects.update_or_create(startup=startup, defaults={
                'version': version, 'published_at': timezone.now(), 'visibility': visibility})
            messages.success(request, 'Прототип добавлен в карточку. ' + (
                'Он доступен только вам.' if visibility == LabPublication.Visibility.PRIVATE else
                'Посетители опубликованной карточки смогут его попробовать.'))
    url = reverse('lab', args=[startup.pk])
    if version:
        url += f'?version={version.pk}'
    return redirect(url)


@login_required
@require_GET
def lab_trial(request, startup_id):
    publication = _published(request, startup_id)
    card = getattr(publication.startup, 'project_card', None)
    public = (card.published_data if card and card.published_at else None) or {
        'name': publication.startup.name, 'tagline': publication.startup.one_line_pitch,
    }
    return render(request, 'founder/lab_trial.html', {'publication': publication,
        'public': public,
        'is_owner': request.user.pk in team_user_ids(publication.startup)})


@login_required
@require_GET
def lab_public_preview(request, startup_id, version_id):
    publication = _published(request, startup_id)
    if publication.version_id != version_id:
        raise Http404('Эта версия не опубликована')
    return preview_response(publication.version.html)


@login_required
@require_POST
def lab_test_start(request, startup_id):
    publication = _published(request, startup_id)
    # A stale tab must not silently test a different version.
    if request.POST.get('version') != str(publication.version_id):
        return JsonResponse({'error': 'Автор обновил прототип. Перезагрузите страницу.'}, status=409)
    try:
        consume_limit(f'lab-test-day:{request.user.pk}', 30, 86400)
    except RequestLimitExceeded as exc:
        return JsonResponse({'error': str(exc)}, status=429)
    session = LabTestSession.objects.create(version=publication.version, tester=request.user)
    return JsonResponse({'session': str(session.pk),
        'preview': reverse('lab_test_preview', args=[startup_id, session.pk]),
        'events': reverse('lab_test_events', args=[startup_id, session.pk]),
        'finish': reverse('lab_test_finish', args=[startup_id, session.pk])})


@login_required
@require_GET
def lab_test_preview(request, startup_id, session_id):
    _, session = _session(request, startup_id, session_id)
    if session.finished_at or timezone.now() - session.created_at > timedelta(minutes=30):
        raise Http404('Тест уже завершён')
    return preview_response(session.version.html, channel=str(session.pk), parent_origin=request.build_absolute_uri('/').rstrip('/'))


def _json(request):
    if len(request.body) > 16_000:
        raise ValueError('Слишком большой запрос.')
    payload = bounded_json_loads(request.body.decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('Неверный формат запроса.')
    return payload


def _event(raw):
    if not isinstance(raw, dict) or set(raw) - {'sequence', 'kind', 'target', 'label', 'depth'}:
        raise ValueError('Неверное событие.')
    if type(raw.get('sequence')) is not int or not 1 <= raw['sequence'] <= 200:
        raise ValueError('Неверный номер события.')
    if raw.get('kind') not in LabTestEvent.Kind.values:
        raise ValueError('Неизвестное событие.')
    result = {key: raw[key] for key in ('sequence', 'kind')}
    for key, limit in (('target', 160), ('label', 80)):
        value = raw.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('Неверное описание события.')
        result[key] = value if raw['kind'] in {'click', 'form'} else ''
    depth = raw.get('depth', 0)
    if type(depth) is not int or not 0 <= depth <= 100:
        raise ValueError('Неверная глубина прокрутки.')
    result['depth'] = depth if raw['kind'] == 'scroll' else 0
    return result


@login_required
@require_POST
def lab_test_events(request, startup_id, session_id):
    _, session = _session(request, startup_id, session_id)
    try:
        payload = _json(request)
        if set(payload) != {'events'} or not isinstance(payload['events'], list) or not 1 <= len(payload['events']) <= 25:
            raise ValueError('Неверный список событий.')
        items = [_event(raw) for raw in payload['events']]
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        return JsonResponse({'error': 'События не прошли проверку.'}, status=400)
    with transaction.atomic():
        session = LabTestSession.objects.select_for_update().get(pk=session.pk)
        now = timezone.now()
        if session.finished_at or now - session.created_at > timedelta(minutes=30):
            return JsonResponse({'error': 'Тест завершён. Начните новый.'}, status=409)
        LabTestEvent.objects.bulk_create([LabTestEvent(session=session, **item) for item in items], ignore_conflicts=True)
        session.last_activity_at = now
        session.save(update_fields=['last_activity_at'])
    return JsonResponse({'saved': True})


@login_required
@require_POST
def lab_test_finish(request, startup_id, session_id):
    publication, session = _session(request, startup_id, session_id)
    try:
        payload = _json(request)
        if set(payload) != {'rating', 'feedback', 'duration'}:
            raise ValueError()
        rating, feedback, duration = payload['rating'], payload['feedback'], payload['duration']
        if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
            raise ValueError()
        if not isinstance(feedback, str) or len(feedback) > 1000:
            raise ValueError()
        if type(duration) is not int or not 0 <= duration <= 1800:
            raise ValueError()
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return JsonResponse({'error': 'Проверьте оценку и отзыв (до 1000 символов).'}, status=400)
    with transaction.atomic():
        session = LabTestSession.objects.select_for_update().get(pk=session.pk)
        if session.finished_at is None:
            now = timezone.now()
            session.rating, session.feedback, session.finished_at = rating, feedback.strip(), now
            session.duration_seconds = min(duration, max(0, int((now - session.created_at).total_seconds())), 1800)
            session.save(update_fields=['rating', 'feedback', 'finished_at', 'duration_seconds'])
    # Монеты получают только внешние тестировщики, оставившие отзыв или оценку.
    earned = 0
    if (session.rating or len(session.feedback) >= 20) and request.user.pk not in team_user_ids(publication.startup):
        earned = award(request.user, CoinTransaction.Kind.TEST, key=session.pk, startup=publication.startup,
                       note=publication.startup.name)
    return JsonResponse({'saved': True, **({'coins_earned': earned, 'coins': balance(request.user)} if earned else {})})
