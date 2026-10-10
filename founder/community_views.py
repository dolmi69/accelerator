"""Project cards, discovery and private founder-to-founder conversations."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, OuterRef, Q, Subquery, When
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from founder.community_forms import CARD_FIELDS, CardRefineForm, ProjectCardForm
from founder.models import (DirectAttachment, DirectConversation, DirectMessage, ProjectBookmark, ProjectCard,
                            StartupProfile, UserBlock)
from founder.services.ai import AIServiceError
from founder.services.chat_search import search_response
from founder.services.direct_attachments import (ALLOWED_EXTENSIONS, MAX_ATTACHMENTS_PER_MESSAGE,
                                                 MAX_PENDING_PER_CONVERSATION, max_bytes, remove_stale_uploads,
                                                 serialize_attachment, store_attachment)
from founder.services.request_limits import RequestLimitExceeded, consume_limit
from founder.services.messaging import blocked_pair, open_conversation, participant_filter
from founder.services.project_cards import (StaleCardError, card_values, generate_card, get_card, save_card)
from founder.services.lab_testing import publication_for


def published_cards():
    return ProjectCard.objects.filter(published_at__isnull=False, startup__owner__is_active=True).select_related('startup__owner')


def editor_context(startup, card, form=None, refine_form=None, generated=False):
    return {'startup': startup, 'card': card, 'workspace_tab': 'card', 'generated': generated,
            'form': form if form is not None else ProjectCardForm(instance=card, initial={'revision': card.revision}),
            'refine_form': refine_form if refine_form is not None else CardRefineForm(),
            'latest': startup.metric_snapshots.first(), 'lab_version': startup.lab_versions.first(),
            'lab_publication': publication_for(startup)}


@login_required
def card_edit(request, startup_id):
    startup = get_object_or_404(StartupProfile, pk=startup_id, owner=request.user)
    card = get_card(startup)
    if request.method == 'GET':
        return render(request, 'community/card_edit.html', editor_context(startup, card))
    form = ProjectCardForm(request.POST, instance=card)
    refine_form = CardRefineForm(request.POST)
    generated = False
    action = request.POST.get('action')
    if action not in ('save', 'publish', 'generate', 'refine'):
        return HttpResponseBadRequest('Неизвестное действие.')
    valid = form.is_valid() and refine_form.is_valid()
    if valid and action == 'publish' and not (form.cleaned_data['summary'] or form.cleaned_data['tagline']):
        form.add_error('summary', 'Добавьте короткое описание проекта перед публикацией.')
        valid = False
    if valid:
        revision = form.cleaned_data['revision']
        try:
            if action in ('generate', 'refine'):
                # This only proposes form values; saving/publishing is a separate explicit step.
                initial = generate_card(startup, form.cleaned_data, refine_form.cleaned_data['instruction'],
                                        assess=action == 'generate')
                form = ProjectCardForm(initial={**initial, 'revision': revision})
                generated = True
                messages.success(request, 'Бруно подготовил вариант. Проверьте поля ниже и сохраните карточку.')
            else:
                save_card(card, form.cleaned_data, revision, publish=action == 'publish')
                messages.success(request, 'Карточка опубликована в сообществе.' if action == 'publish' else
                                 'Черновик сохранён. Для обновления карточки в ленте нажмите «Опубликовать».')
                return redirect('card_edit', startup_id=startup.pk)
        except (AIServiceError, StaleCardError) as exc:
            form.add_error(None, str(exc))
    return render(request, 'community/card_edit.html', editor_context(startup, card, form, refine_form, generated),
                  status=400 if form.errors or refine_form.errors else 200)


@login_required
@require_POST
def card_generate(request, startup_id):
    startup = get_object_or_404(StartupProfile, pk=startup_id, owner=request.user)
    card = get_card(startup)
    try:
        initial = generate_card(startup, card_values(card), assess=True)
    except AIServiceError as exc:
        messages.error(request, str(exc))
        return redirect('card_edit', startup_id=startup.pk)
    messages.success(request, 'Радар обновлён. Бруно подготовил карточку — проверьте и сохраните её.')
    return render(request, 'community/card_edit.html', editor_context(
        startup, card, ProjectCardForm(initial={**initial, 'revision': card.revision}), generated=True,
    ))


@login_required
@require_POST
def card_unpublish(request, startup_id):
    card = get_object_or_404(ProjectCard, startup_id=startup_id, startup__owner=request.user)
    # Clearing the snapshot also prevents accidental reuse of withdrawn content.
    from django.db.models import F
    ProjectCard.objects.filter(pk=card.pk).update(published_at=None, published_data={}, revision=F('revision') + 1)
    messages.success(request, 'Карточка снята с публикации. Черновик остался у вас.')
    return redirect('card_edit', startup_id=startup_id)


@login_required
def community_feed(request):
    query = request.GET.get('q', '').strip()[:120]
    stage = request.GET.get('stage', '')
    saved = request.GET.get('saved') == '1'
    cards = published_cards().annotate(
        is_saved=Exists(ProjectBookmark.objects.filter(user=request.user, card_id=OuterRef('pk'))),
    )
    if query:
        cards = cards.filter(Q(published_data__name__icontains=query) | Q(published_data__summary__icontains=query)
                             | Q(published_data__tagline__icontains=query) | Q(published_data__looking_for__icontains=query))
    if stage in StartupProfile.Stage.values:
        cards = cards.filter(published_data__stage=stage)
    if saved:
        cards = cards.filter(is_saved=True)
    page = Paginator(cards.order_by('-published_at', '-pk'), 12).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'community/feed.html', {
        'page': page, 'query': query, 'stage': stage, 'saved': saved, 'stages': StartupProfile.Stage.choices,
        'filter_query': params.urlencode(), 'community_tab': True,
    })


@login_required
def card_detail(request, startup_id):
    card = get_object_or_404(published_cards(), startup_id=startup_id)
    publication = publication_for(card.startup)
    if publication and publication.visibility == 'private' and card.startup.owner_id != request.user.pk:
        publication = None
    return render(request, 'community/card_detail.html', {
        'card': card, 'public': card.published_data, 'author': card.startup.owner,
        'is_owner': card.startup.owner_id == request.user.pk,
        'is_saved': card.bookmarks.filter(user=request.user).exists(),
        'can_message': not blocked_pair(request.user.pk, card.startup.owner_id),
        'lab_publication': publication,
    })


@login_required
@require_POST
def bookmark(request, startup_id):
    card = get_object_or_404(published_cards(), startup_id=startup_id)
    if request.POST.get('save') == '1':
        ProjectBookmark.objects.get_or_create(user=request.user, card=card)
    else:
        ProjectBookmark.objects.filter(user=request.user, card=card).delete()
    return redirect('card_detail', startup_id=startup_id)


@login_required
@require_POST
def conversation_start(request, startup_id):
    card = get_object_or_404(published_cards(), startup_id=startup_id)
    thread = open_conversation(request.user, card)
    return redirect('conversation', conversation_id=thread.pk)


@login_required
def inbox(request, conversation_id=None):
    query = DirectConversation.objects.filter(participant_filter(request.user.pk)).select_related('user_low', 'user_high', 'source_card')
    active = get_object_or_404(query, pk=conversation_id) if conversation_id else None
    query = query.annotate(
        read_cursor=Case(When(user_low_id=request.user.pk, then=F('low_read_id')), default=F('high_read_id')),
        last_content=Subquery(DirectMessage.objects.filter(conversation_id=OuterRef('pk')).order_by('-id').values('content')[:1]),
        last_message_id=Subquery(DirectMessage.objects.filter(conversation_id=OuterRef('pk')).order_by('-id').values('id')[:1]),
    ).annotate(unread_total=Count('direct_messages', filter=(
        Q(direct_messages__id__gt=F('read_cursor')) & ~Q(direct_messages__sender_id=request.user.pk)
    )))
    page = Paginator(query.order_by('-updated_at', '-id'), 30).get_page(request.GET.get('page'))
    threads = []
    for thread in page:
        threads.append({'thread': thread, 'other': thread.other_user(request.user.pk),
                        'last': {'content': thread.last_content or ('📎 Вложение' if thread.last_message_id else '')},
                        'unread': thread.unread_total})
    return render(request, 'community/inbox.html', {
        'threads': threads, 'page': page, 'active': active,
        'other': active.other_user(request.user.pk) if active else None,
        'blocked_by_me': UserBlock.objects.filter(user=request.user, blocked=active.other_user(request.user.pk)).exists() if active else False,
        'blocked': blocked_pair(active.user_low_id, active.user_high_id) if active else False,
        'attachment_extensions': ','.join(sorted(ALLOWED_EXTENSIONS)),
        'attachment_max_bytes': max_bytes(),
        'attachment_max_files': MAX_ATTACHMENTS_PER_MESSAGE,
    })


def _json_error(message, status, retry_after=None):
    response = JsonResponse({'error': message}, status=status)
    if retry_after is not None:
        response['Retry-After'] = str(retry_after)
    return response


@login_required
@require_POST
def direct_attachment_upload(request, conversation_id):
    thread = get_object_or_404(DirectConversation.objects.select_related('user_low', 'user_high'),
                               participant_filter(request.user.pk), pk=conversation_id)
    if blocked_pair(thread.user_low_id, thread.user_high_id) or not thread.other_user(request.user.pk).is_active:
        return _json_error('Отправка файлов в этом диалоге недоступна.', 403)
    try:
        consume_limit(f'direct-upload:{request.user.pk}', 30, 60)
        consume_limit(f'direct-upload-day:{request.user.pk}', 300, 86400)
    except RequestLimitExceeded as exc:
        return _json_error('Слишком много файлов подряд. Подождите немного.', 429, exc.retry_after)
    except OperationalError:
        return _json_error('Сервис занят. Повторите через несколько секунд.', 503, 5)
    remove_stale_uploads(request.user)
    pending = DirectAttachment.objects.filter(uploader=request.user, conversation=thread, message__isnull=True)
    if pending.count() >= MAX_PENDING_PER_CONVERSATION:
        return _json_error('Слишком много неотправленных файлов. Отправьте или уберите часть из них.', 400)
    try:
        attachment = store_attachment(request.user, thread, request.FILES.get('file'))
    except ValidationError as exc:
        return _json_error(exc.messages[0], 400)
    return JsonResponse(serialize_attachment(attachment), status=201)


@login_required
@require_POST
def direct_attachment_delete(request, attachment_id):
    # Only a file that is still in the composer; sent files stay in the history.
    attachment = get_object_or_404(DirectAttachment, pk=attachment_id, uploader=request.user, message__isnull=True)
    attachment.delete()
    return HttpResponse(status=204)


@login_required
@require_GET
def direct_attachment(request, attachment_id):
    attachment = get_object_or_404(
        DirectAttachment.objects.filter(
            Q(conversation__user_low_id=request.user.pk) | Q(conversation__user_high_id=request.user.pk),
        ).filter(Q(message__isnull=False) | Q(uploader=request.user)),
        pk=attachment_id,
    )
    image = attachment.kind == DirectAttachment.Kind.IMAGE
    field = attachment.preview if request.GET.get('preview') and attachment.preview else attachment.file
    try:
        handle = field.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Файл не найден') from exc
    if field is attachment.preview:
        response = FileResponse(handle, content_type='image/webp')
    elif image and not request.GET.get('download'):
        response = FileResponse(handle, content_type=attachment.content_type, filename=attachment.original_name)
    else:
        # Never render user files in the browser: always a download.
        response = FileResponse(handle, as_attachment=True, filename=attachment.original_name,
                                content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    if not response.get('Content-Disposition', '').startswith('attachment'):
        # Photos opened in a tab render in a sandbox. Not on downloads: Chrome then drops the file name.
        response['Content-Security-Policy'] = "default-src 'none'; img-src 'self'; sandbox"
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@login_required
@require_GET
def conversation_search(request, conversation_id):
    thread = get_object_or_404(DirectConversation.objects.select_related('user_low', 'user_high'),
                               participant_filter(request.user.pk), pk=conversation_id)
    other_name = thread.other_user(request.user.pk).public_name
    rows = thread.direct_messages.order_by('-id').values_list(
        'id', 'content', 'sender_id', 'created_at').iterator(chunk_size=500)

    def describe(row):
        own = row[2] == request.user.pk
        return {'id': row[0], 'own': own, 'author': 'Вы' if own else other_name,
                'created_at': row[3].isoformat()}

    return search_response(request, rows, describe)


@login_required
@require_POST
def block_contact(request, conversation_id):
    thread = get_object_or_404(DirectConversation, participant_filter(request.user.pk), pk=conversation_id)
    other = thread.other_user(request.user.pk)
    if request.POST.get('block') == '1':
        UserBlock.objects.get_or_create(user=request.user, blocked=other)
    else:
        UserBlock.objects.filter(user=request.user, blocked=other).delete()
    layer = get_channel_layer()
    for user_id in (thread.user_low_id, thread.user_high_id):
        async_to_sync(layer.group_send)(f'inbox.{user_id}', {
            'type': 'inbox.event', 'kind': 'blocked', 'conversation': str(thread.pk),
            'blocked': blocked_pair(thread.user_low_id, thread.user_high_id),
        })
    return redirect('conversation', conversation_id=thread.pk)
