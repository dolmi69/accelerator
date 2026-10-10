import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .capabilities import role
from .forms import InviteForm, ProfileForm, RegistrationForm
from .models import Invitation, Membership, Notification, Profile, SiteControl


def form_page(request, form, heading, **extra):
    return render(request, 'form.html', {'form':form, 'heading':heading, **extra})


@login_required
@require_http_methods(['GET','POST'])
def profile(request):
    form = ProfileForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error(None, 'Логин или email уже занят.')
        else:
            messages.success(request, 'Профиль сохранён.')
            return redirect('profile')
    return render(request, 'profile.html', {'form': form, 'profile': form.profile})


@require_GET
def avatar(request, user_id):
    profile = get_object_or_404(Profile.objects.filter(user__is_active=True), user_id=user_id)
    if not profile.avatar:
        raise Http404()
    response = FileResponse(profile.avatar.open('rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'no-cache'
    return response


@require_GET
def public_profile(request,user_id):
    person = get_object_or_404(get_user_model(),pk=user_id,is_active=True)
    return render(request,'public_profile.html',{'person':person,'profile':Profile.objects.filter(user=person).first()})


@require_http_methods(['GET','POST'])
def setup_owner(request, token):
    path = settings.BASE_DIR / '.owner-setup'
    try:
        correct = path.read_text().strip()
    except OSError:
        correct = ''
    if not correct or not secrets.compare_digest(correct, token) or SiteControl.objects.filter(pk=1, setup_claimed=True).exists():
        raise Http404()
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if not SiteControl.objects.filter(pk=1, setup_claimed=False).update(setup_claimed=True):
                    raise Http404()
                user = form.save()
                Membership.objects.filter(user=user).update(role='owner')
                user.is_staff = user.is_superuser = True
                user.save(update_fields=['is_staff','is_superuser'])
        except IntegrityError:
            form.add_error(None, 'Логин или email уже занят.')
        else:
            path.unlink(missing_ok=True)
            login(request, user)
            return redirect('manage_site')
    return form_page(request, form, 'Создать владельца сайта', note='Это отдельный аккаунт созданного сайта.')


@login_required
@require_http_methods(['GET','POST'])
def team(request):
    if role(request.user) != 'owner':
        return HttpResponse('Управление командой доступно владельцу.', status=403)
    form = InviteForm(request.POST or None)
    control, _ = SiteControl.objects.get_or_create(pk=1)
    invitation = None
    if request.method == 'POST':
        if request.POST.get('action') == 'settings':
            control.allow_listings = request.POST.get('allow_listings') == 'on'
            control.save(update_fields=['allow_listings'])
            return redirect('team')
        if request.POST.get('action') == 'role':
            if not request.POST.get('member','').isdigit():
                return HttpResponse(status=400)
            member = get_object_or_404(Membership, pk=request.POST.get('member'))
            if member.role == 'owner' or member.user_id == request.user.pk or request.POST.get('role') not in {'client','editor'}:
                return HttpResponse('Эту роль нельзя изменить.', status=400)
            member.role = request.POST['role']; member.save(update_fields=['role'])
            return redirect('team')
        if form.is_valid():
            invitation = Invitation.objects.create(email=form.cleaned_data['email'].lower(), role=form.cleaned_data['role'],
                recipient=form.cleaned_data['username'],
                expires_at=timezone.now()+timedelta(days=7))
    return render(request, 'team.html', {'form':form, 'control':control,
        'members':Membership.objects.select_related('user').order_by('pk')[:100],
        'invite_url':request.build_absolute_uri(reverse('accept_invite',args=[invitation.token])) if invitation else ''})


@login_required
@require_http_methods(['GET','POST'])
def accept_invite(request, token):
    invitation = get_object_or_404(Invitation, token=token, expires_at__gt=timezone.now(), accepted_at=None)
    if invitation.recipient_id != request.user.pk:
        return HttpResponse('Войдите с аккаунтом, которому выдано приглашение.', status=403)
    if request.method == 'POST':
        with transaction.atomic():
            if not Invitation.objects.filter(pk=invitation.pk, accepted_at=None, expires_at__gt=timezone.now()).update(accepted_at=timezone.now()):
                raise Http404()
            member, _ = Membership.objects.get_or_create(user=request.user)
            if member.role != 'owner':
                member.role = invitation.role; member.save(update_fields=['role'])
        return redirect('manage_site')
    return render(request,'confirm.html',{'heading':'Принять приглашение', 'note':f'Роль: {Membership.Role(invitation.role).label}.'})


@login_required
@require_GET
def notifications(request):
    return render(request,'notifications.html',{'notices':Notification.objects.filter(user=request.user)[:100]})


@login_required
@require_POST
def read_notification(request, notice_id):
    row = get_object_or_404(Notification,user=request.user,pk=notice_id)
    row.read = True; row.save(update_fields=['read'])
    return redirect('notifications')
