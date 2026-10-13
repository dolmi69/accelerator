from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from founder.models import User
from founder.profile_forms import UserProfileForm
from founder.services.messaging import blocked_pair, start_direct_conversation
from founder.services.user_profiles import save_user_profile


@login_required
@require_http_methods(['GET', 'POST'])
def profile_edit(request):
    form = UserProfileForm(request.POST if request.method == 'POST' else None,
                           request.FILES if request.method == 'POST' else None, instance=request.user)
    old_avatar_url = request.user.avatar_url
    if request.method == 'POST' and form.is_valid():
        try:
            user = save_user_profile(request.user.pk, form.cleaned_data)
        except IntegrityError:
            form.add_error('handle', 'Тег уже занят другим пользователем. Выберите другой.')
        else:
            messages.success(request, 'Профиль обновлён.')
            return redirect(user.get_absolute_url())
    return render(request, 'profiles/edit.html', {'form': form, 'current_avatar_url': old_avatar_url},
                  status=400 if form.errors else 200)


@login_required
@require_GET
def my_profile(request):
    return redirect(request.user.get_absolute_url())


@login_required
@require_GET
def user_profile(request, handle):
    author = get_object_or_404(User, handle__iexact=handle, is_active=True)
    if handle != author.handle:
        return redirect(author.get_absolute_url())
    from founder.community_views import published_cards
    cards = published_cards().filter(startup__owner=author).order_by('-published_at')
    page = Paginator(cards, 9).get_page(request.GET.get('page'))
    from founder.services.card_reports import mark_report_state
    page.object_list = mark_report_state(page.object_list, request.user)
    return render(request, 'profiles/detail.html', {
        'author': author, 'page': page, 'is_owner': author.pk == request.user.pk,
        'can_message': author.pk != request.user.pk and not blocked_pair(author.pk, request.user.pk),
    })


@login_required
@require_GET
def member_directory(request):
    query = request.GET.get('q', '').strip().removeprefix('@')[:120]
    users = User.objects.filter(is_active=True)
    if query:
        users = users.filter(Q(handle__icontains=query) | Q(display_name__icontains=query)
                             | Q(occupation__icontains=query))
    page = Paginator(users.order_by('handle', 'pk'), 18).get_page(request.GET.get('page'))
    return render(request, 'profiles/directory.html', {'page': page, 'query': query})


@login_required
@require_GET
def handle_available(request):
    from django.core.exceptions import ValidationError
    from django.http import JsonResponse
    from founder.models import handle_validator
    handle = request.GET.get('handle', '').strip().removeprefix('@').lower()
    try:
        handle_validator(handle)
    except ValidationError as exc:
        return JsonResponse({'available': False, 'message': exc.messages[0]})
    available = not User.objects.filter(handle__iexact=handle).exclude(pk=request.user.pk).exists()
    return JsonResponse({'available': available, 'handle': handle,
                         'message': 'Это ваш тег' if handle == request.user.handle else
                         ('Тег свободен' if available else 'Этот тег уже занят')})


@login_required
@require_GET
def user_avatar(request, user_id):
    user = get_object_or_404(User, pk=user_id, is_active=True)
    if not user.avatar:
        raise Http404('Аватарка не задана')
    try:
        avatar_file = user.avatar.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Аватарка не найдена') from exc
    response = FileResponse(avatar_file, content_type='image/jpeg')
    response['Cache-Control'] = 'private, no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_POST
def profile_message(request, user_id):
    author = get_object_or_404(User, pk=user_id, is_active=True)
    thread = start_direct_conversation(request.user, author)
    return redirect('conversation', conversation_id=thread.pk)
