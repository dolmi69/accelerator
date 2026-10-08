"""Project cards, discovery and private founder-to-founder conversations."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, OuterRef, Q, Subquery, When
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.community_forms import CARD_FIELDS, CardRefineForm, ProjectCardForm
from founder.models import (DirectConversation, DirectMessage, LabPublication, ProjectBookmark, ProjectCard, Promotion,
                            StartupProfile, UserBlock)
from founder.services.access import can_edit as can_edit_project, get_startup, team_members
from founder.services.ai import AIServiceError
from founder.services.messaging import blocked_pair, open_conversation, participant_filter
from founder.services.project_cards import (StaleCardError, card_values, generate_card, get_card, save_card)
from founder.services.lab_testing import publication_for


def published_cards():
    return ProjectCard.objects.filter(published_at__isnull=False, startup__owner__is_active=True).select_related('startup__owner')


def _active(kind):
    now = timezone.now()
    return Promotion.objects.filter(startup_id=OuterRef('startup_id'), kind=kind, starts_at__lte=now, ends_at__gt=now)


def with_promotions(cards):
    return cards.annotate(is_promoted=Exists(_active(Promotion.Kind.FEED_TOP)),
                          is_highlighted=Exists(_active(Promotion.Kind.HIGHLIGHT)))


def testers_wanted(limit=6):
    """Опубликованные для всех прототипы, авторы которых позвали тестировщиков за монеты."""
    return list(with_promotions(published_cards()).filter(
        Exists(_active(Promotion.Kind.TESTERS)),
        startup__lab_publication__visibility=LabPublication.Visibility.PUBLIC,
    ).order_by('?')[:limit])


def editor_context(startup, card, form=None, refine_form=None, generated=False):
    return {'startup': startup, 'card': card, 'workspace_tab': 'card', 'generated': generated,
            'form': form if form is not None else ProjectCardForm(instance=card, initial={'revision': card.revision}),
            'refine_form': refine_form if refine_form is not None else CardRefineForm(),
            'latest': startup.metric_snapshots.first(), 'lab_version': startup.lab_versions.first(),
            'lab_publication': publication_for(startup)}


@login_required
def card_edit(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
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
    startup = get_startup(request, startup_id, edit=True)
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
    card = get_object_or_404(ProjectCard, startup=get_startup(request, startup_id, edit=True))
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
    cards = with_promotions(published_cards()).annotate(
        is_saved=Exists(ProjectBookmark.objects.filter(user=request.user, card_id=OuterRef('pk'))),
    )
    if query:
        cards = cards.filter(Q(published_data__name__icontains=query) | Q(published_data__summary__icontains=query)
                             | Q(published_data__tagline__icontains=query) | Q(published_data__looking_for__icontains=query))
    if stage in StartupProfile.Stage.values:
        cards = cards.filter(published_data__stage=stage)
    if saved:
        cards = cards.filter(is_saved=True)
    # Оплаченное монетами продвижение поднимает карточку, внутри групп порядок прежний.
    page = Paginator(cards.order_by('-is_promoted', '-published_at', '-pk'), 12).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    show_testers = page.number == 1 and not (query or stage or saved)
    return render(request, 'community/feed.html', {
        'page': page, 'query': query, 'stage': stage, 'saved': saved, 'stages': StartupProfile.Stage.choices,
        'filter_query': params.urlencode(), 'community_tab': True,
        'testers_wanted': testers_wanted() if show_testers else [],
    })


@login_required
def card_detail(request, startup_id):
    card = get_object_or_404(with_promotions(published_cards()), startup_id=startup_id)
    publication = publication_for(card.startup)
    can_edit = card.startup.owner_id == request.user.pk or can_edit_project(request.user, card.startup_id)
    if publication and publication.visibility == 'private' and not can_edit:
        publication = None
    return render(request, 'community/card_detail.html', {
        'card': card, 'public': card.published_data, 'author': card.startup.owner,
        'is_owner': can_edit, 'team': team_members(card.startup),
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
    ).annotate(unread_total=Count('direct_messages', filter=(
        Q(direct_messages__id__gt=F('read_cursor')) & ~Q(direct_messages__sender_id=request.user.pk)
    )))
    page = Paginator(query.order_by('-updated_at', '-id'), 30).get_page(request.GET.get('page'))
    threads = []
    for thread in page:
        threads.append({'thread': thread, 'other': thread.other_user(request.user.pk),
                        'last': {'content': thread.last_content}, 'unread': thread.unread_total})
    return render(request, 'community/inbox.html', {
        'threads': threads, 'page': page, 'active': active,
        'other': active.other_user(request.user.pk) if active else None,
        'blocked_by_me': UserBlock.objects.filter(user=request.user, blocked=active.other_user(request.user.pk)).exists() if active else False,
        'blocked': blocked_pair(active.user_low_id, active.user_high_id) if active else False,
    })


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
