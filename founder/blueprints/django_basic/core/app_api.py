"""Allowlisted capabilities for the isolated front end. No arbitrary proxy or code."""
from functools import wraps
import json
from uuid import UUID
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, OperationalError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from .capabilities import enabled
from .chat_service import save_message, broadcast_message
from .models import Conversation, SavedResult


class ActionError(Exception):
    def __init__(self, message, status=400, code='INVALID_REQUEST'):
        self.message, self.status, self.code = message, status, code


def endpoint(method, *, authenticated=True, module=None):
    def decorate(view):
        @wraps(view)
        def wrapped(request):
            try:
                if request.method != method:
                    raise ActionError('Этот способ вызова не поддерживается.', 405)
                if authenticated and (not request.user.is_authenticated or not request.user.is_active):
                    raise ActionError('Войдите в аккаунт этого сайта, чтобы сохранить или отправить результат.', 401, 'AUTH_REQUIRED')
                if module and not enabled(module):
                    raise ActionError('Чаты не подключены к этому сайту.', 404, 'MODULE_DISABLED')
                return view(request)
            except ActionError as exc:
                return JsonResponse({'error': exc.message, 'code': exc.code}, status=exc.status)
            except (IntegrityError, OperationalError):
                return JsonResponse({'error': 'Сервис занят. Повторите действие.', 'code': 'BUSY'}, status=503)
        return wrapped
    return decorate


def body(request, allowed):
    if request.content_type != 'application/json':
        raise ActionError('Ожидались данные JSON.')
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        raise ActionError('Некорректные данные.') from None
    if not isinstance(data, dict) or set(data) - allowed:
        raise ActionError('Некорректные поля запроса.')
    return data


def nonce_value(data):
    try:
        return UUID(str(data.get('nonce', '')))
    except ValueError:
        raise ActionError('Обновите страницу и повторите действие.') from None


def result_payload(row):
    return {'id': str(row.pk), 'title': row.title, 'content': row.content, 'created_at': row.created_at.isoformat()}


def own_result(user, data, nonce):
    if 'result_id' in data:
        if 'title' in data or 'content' in data:
            raise ActionError('Передайте сохранённый результат либо новый текст.')
        try:
            identifier = UUID(str(data['result_id']))
        except ValueError:
            raise ActionError('Результат не найден.', 404) from None
        row = SavedResult.objects.filter(pk=identifier, owner=user).first()
        if not row:
            raise ActionError('Результат не найден.', 404)
        return row
    title, content = data.get('title'), data.get('content')
    if (not isinstance(title, str) or not title.strip() or len(title) > 120
            or not isinstance(content, str) or not content.strip() or len(content) > 1800
            or '\x00' in title + content):
        raise ActionError('Укажите название до 120 и текст результата до 1800 символов.')
    row, _ = SavedResult.objects.get_or_create(owner=user, nonce=nonce,
        defaults={'title': title.strip(), 'content': content.strip()})
    if (row.title, row.content) != (title.strip(), content.strip()):
        raise ActionError('Повторный запрос содержит другой результат.', 409, 'CONFLICT')
    return row


@endpoint('GET', authenticated=False)
def session(request):
    active = request.user.is_authenticated and request.user.is_active
    return JsonResponse({'authenticated': bool(active),
        'user': {'id': request.user.pk, 'username': request.user.username} if active else None,
        'capabilities': {'results': True, 'chat': enabled('chat')}})


@endpoint('GET')
def results(request):
    rows = SavedResult.objects.filter(owner=request.user)
    if request.GET.get('id'):
        try:
            rows = rows.filter(pk=UUID(request.GET['id']))
        except ValueError:
            raise ActionError('Результат не найден.', 404) from None
    return JsonResponse({'results': [result_payload(row) for row in rows[:50]]})


@endpoint('GET', module='chat')
def recipients(request):
    users = get_user_model().objects.filter(is_active=True).exclude(pk=request.user.pk)
    query = request.GET.get('q', '').strip()[:80]
    if query:
        users = users.filter(username__icontains=query)
    return JsonResponse({'recipients': list(users.order_by('username').values('id', 'username')[:50])})


@endpoint('POST')
def save_result(request):
    data = body(request, {'title', 'content', 'nonce'})
    with transaction.atomic():
        row = own_result(request.user, data, nonce_value(data))
    return JsonResponse({'persisted': True, 'result': result_payload(row)})


@endpoint('POST', module='chat')
def share_result(request):
    data = body(request, {'title', 'content', 'result_id', 'recipient_id', 'nonce'})
    recipient_id = data.get('recipient_id')
    if type(recipient_id) is not int or recipient_id == request.user.pk:
        raise ActionError('Выберите другого участника.')
    recipient = get_user_model().objects.filter(pk=recipient_id, is_active=True).first()
    if not recipient:
        raise ActionError('Участник не найден.', 404)
    nonce = nonce_value(data)
    with transaction.atomic():
        row = own_result(request.user, data, nonce)
        first, second = sorted((request.user.pk, recipient.pk))
        conversation, _ = Conversation.objects.get_or_create(first_id=first, second_id=second)
        message, created = save_message(request.user.pk, conversation.pk, row.title + '\n' + row.content, nonce)
        if message is None:
            raise ActionError('Слишком много сообщений или повторный запрос изменён.', 409, 'CONFLICT')
        if created:
            transaction.on_commit(lambda: broadcast_message(conversation.pk, message))
    return JsonResponse({'persisted': True, 'result': result_payload(row),
        'message_id': message['id'], 'recipient': {'id': recipient.pk, 'username': recipient.username},
        'chat_url': reverse('chat', args=[conversation.pk])})


@login_required
def saved_results_page(request):
    return render(request, 'results.html', {'results': SavedResult.objects.filter(owner=request.user)[:100]})
