"""Страницы баланса и пополнения (USDT TRC-20 на один основной адрес)."""

import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from payments.models import Balance, Invoice
from payments.services import hd


@login_required
@require_GET
def wallet(request):
    """Баланс, история пополнений и кнопка «Пополнить»."""
    balance = Balance.for_user(request.user)
    history = Invoice.objects.filter(user=request.user).order_by("-created_at")[:20]
    return render(
        request,
        "payments/wallet.html",
        {"balance": balance, "history": history, "enabled": settings.PAYMENTS_ENABLED},
    )


@login_required
@require_GET
def topup_page(request):
    return render(request, "payments/create.html", {"enabled": settings.PAYMENTS_ENABLED})


def _topup_error(request, message, status):
    return render(
        request,
        "payments/create.html",
        {"enabled": settings.PAYMENTS_ENABLED, "error": message},
        status=status,
    )


@login_required
@require_POST
def topup_create(request):
    if not settings.PAYMENTS_ENABLED:
        return _topup_error(request, "Приём оплаты выключен (PAYMENTS_ENABLED=0).", 503)

    raw = (request.POST.get("amount") or "").replace(",", ".").strip()
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return _topup_error(request, "Некорректная сумма.", 400)
    if amount <= 0:
        return _topup_error(request, "Сумма должна быть больше нуля.", 400)

    max_amount = Decimal(settings.PAYMENTS_MAX_INVOICE_AMOUNT)
    if amount > max_amount:
        return _topup_error(request, f"Максимальная сумма — {max_amount} USDT.", 400)

    try:
        address = hd.receiving_address()
    except ImproperlyConfigured:
        return _topup_error(request, "Кошелёк не настроен: задайте PAYMENTS_XPUB в .env.", 503)

    invoice = Invoice.objects.create(
        user=request.user,
        order_id=f"top_{uuid.uuid4().hex[:12]}",
        description="Пополнение баланса",
        address=address,
        amount_expected=amount,
        status=Invoice.Status.PENDING,
        expires_at=timezone.now() + timedelta(minutes=settings.PAYMENTS_INVOICE_TTL_MINUTES),
    )
    return redirect("invoice_detail", invoice_id=invoice.id)


@login_required
@require_GET
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    return render(
        request,
        "payments/pay.html",
        {"invoice": invoice, "required": settings.PAYMENTS_REQUIRED_CONFIRMATIONS},
    )


@login_required
@require_GET
def invoice_status(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    return JsonResponse(
        {
            "status": invoice.status,
            "is_paid": invoice.is_paid,
            "amount_received": invoice.amount_received_text,
            "confirmations": invoice.confirmations,
        }
    )


@login_required
@require_GET
def invoice_qr(request, invoice_id):
    import qrcode
    from io import BytesIO

    invoice = get_object_or_404(Invoice, pk=invoice_id)
    image = qrcode.make(invoice.address)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")
