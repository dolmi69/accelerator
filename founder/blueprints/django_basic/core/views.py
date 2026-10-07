from django.conf import settings
import os
import re
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import Conversation
from .forms import RegistrationForm
from django.db import IntegrityError, transaction
from .lab_viewer import launcher, ancestors
from .capabilities import enabled
from django.contrib.auth.views import LoginView as DjangoLoginView


class LoginView(DjangoLoginView):
    def get_default_redirect_url(self):
        return "/people/" if enabled("chat") else "/profile/"


@require_GET
def home(request):
    return render(request, "home.html")


@require_GET
def health(request):
    return JsonResponse({"project_id": settings.SITE["project_id"], "version_id": settings.SITE["version_id"], "pid": os.getpid()})


@require_GET
def prototype(request):
    # Read as plain bytes: never interpret model output as a Django template.
    html = (settings.BASE_DIR / 'prototype.html').read_text(encoding='utf-8')
    if launcher():
        script = "<script>addEventListener('keydown',e=>{if(e.key==='Escape'&&!e.isComposing){e.preventDefault();parent.postMessage({type:'cofounder:lab-exit'},'*')}})</script>"
        html = re.sub(r'(<head\b[^>]*>)', lambda match: match[0] + script, html, count=1, flags=re.I)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = (
        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
        "form-action 'none'; base-uri 'none'; frame-ancestors " + (ancestors() if launcher() else "'self'")
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("people" if enabled("chat") else "profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(None, 'Логин или email уже занят.')
        else:
            login(request, user)
            return redirect("people" if enabled("chat") else "profile")
    return render(request, "auth.html", {"form": form, "heading": "Создать аккаунт"})


@login_required
@require_GET
def people(request):
    query = request.GET.get("q", "").strip()[:80]
    users = get_user_model().objects.filter(is_active=True).exclude(pk=request.user.pk).order_by("username")
    if query:
        users = users.filter(username__icontains=query)
    return render(request, "people.html", {"people": users[:50], "query": query})


@login_required
@require_POST
def start_chat(request, user_id):
    other = get_object_or_404(get_user_model(), pk=user_id, is_active=True)
    if other.pk == request.user.pk:
        return HttpResponse("Выберите другого пользователя.", status=400)
    first, second = sorted([request.user.pk, other.pk])
    conversation, _ = Conversation.objects.get_or_create(first_id=first, second_id=second)
    return redirect("chat", conversation_id=conversation.pk)


def owned_chat(user, conversation_id):
    return get_object_or_404(Conversation.objects.select_related("first", "second").filter(
        Q(first=user) | Q(second=user)), pk=conversation_id)


@login_required
@require_GET
def inbox(request):
    conversations = Conversation.objects.filter(Q(first=request.user) | Q(second=request.user)).select_related("first", "second").order_by("-created_at")[:50]
    return render(request, "inbox.html", {"conversations": conversations})


@login_required
@require_GET
def chat(request, conversation_id):
    conversation = owned_chat(request.user, conversation_id)
    other = conversation.second if conversation.first_id == request.user.pk else conversation.first
    recent = list(conversation.messages.select_related("sender").order_by("-id")[:50])[::-1]
    return render(request, "chat.html", {"conversation": conversation, "other": other, "recent": recent})


@login_required
@require_GET
def history(request, conversation_id):
    conversation = owned_chat(request.user, conversation_id)
    try:
        after = int(request.GET.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Некорректный номер сообщения."}, status=400)
    rows = list(conversation.messages.filter(id__gt=after).select_related("sender").order_by("id")[:101])
    return JsonResponse({"messages": [{"id": row.pk, "sender_id": row.sender_id, "sender": row.sender.username,
        "content": row.content, "nonce": str(row.nonce), "created_at": row.created_at.isoformat()} for row in rows[:100]], "more": len(rows) > 100})
