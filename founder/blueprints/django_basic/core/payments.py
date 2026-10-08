"""Optional redirect checkout. Webhooks trigger a fresh authenticated API lookup."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .capabilities import enabled, module_required
from .models import Order, Payment
from .services import notify


def configured():
    return enabled('payments') and bool(settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY and settings.PAYMENT_RETURN_BASE)


def provider(method, path, *, payload=None, key=None):
    headers = {'Idempotence-Key':str(key)} if key else {}
    try:
        response = httpx.request(method,'https://api.yookassa.ru/v3/' + path,json=payload,headers=headers,
            auth=(settings.YOOKASSA_SHOP_ID,settings.YOOKASSA_SECRET_KEY),timeout=15,trust_env=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data,dict):
            raise ValueError()
        return data
    except (httpx.HTTPError,ValueError):
        raise ValidationError('Платёжный сервис сейчас недоступен. Повторите позже: повтор не создаёт второй платёж.') from None


def apply_provider(payment,data):
    try:
        amount = Decimal(data['amount']['value'])
        valid = (data['id'] == payment.provider_id and data['amount']['currency'] == 'RUB'
            and amount.is_finite() and amount == payment.amount
            and data.get('metadata',{}).get('order_id') == str(payment.order_id))
    except (KeyError,TypeError,AttributeError,InvalidOperation):
        valid = False
    if not valid:
        raise ValidationError('Ответ платёжного сервиса не соответствует заказу.')
    status = data.get('status')
    if status not in {'pending','waiting_for_capture','succeeded','canceled'}:
        raise ValidationError('Неизвестный статус платежа.')
    if status == 'succeeded' and data.get('paid') is not True:
        raise ValidationError('Оплата ещё не подтверждена.')
    with transaction.atomic():
        # Ignore late pending events after a final state.
        changed = Payment.objects.filter(pk=payment.pk).exclude(status__in=['succeeded','canceled']).update(status=status)
        if changed and status == 'succeeded':
            order = payment.order
            notify(order.user_id,f'Заказ №{order.pk} оплачен',reverse('order',args=[order.pk]),key=f'paid-buyer:{payment.pk}')
            notify(order.seller_id,f'Получена оплата заказа №{order.pk}',reverse('order',args=[order.pk]),key=f'paid-seller:{payment.pk}')


@login_required
@module_required('payments')
@require_POST
def pay(request,order_id):
    from .module_views import accessible_order
    order = accessible_order(request,order_id)
    if order.user_id != request.user.pk:
        return HttpResponse(status=403)
    if not configured():
        messages.error(request,'Автор сайта ещё не подключил оплату. Заказ сохранён.')
        return redirect('order',order_id=order.pk)
    try:
        with transaction.atomic():
            # Share a DB write lock with order cancellation, before contacting the provider.
            if not Order.objects.filter(pk=order.pk,status__in=['new','processing']).update(status=F('status')):
                raise ValidationError('Этот заказ уже закрыт.')
            payment,_ = Payment.objects.get_or_create(order=order,defaults={'amount':order.total})
        if not payment.provider_id:
            payload = {'amount':{'value':str(payment.amount),'currency':'RUB'},'capture':True,
                'confirmation':{'type':'redirect','return_url':settings.PAYMENT_RETURN_BASE.rstrip('/')+reverse('order',args=[order.pk])},
                'description':f'Заказ №{order.pk}','metadata':{'order_id':str(order.pk)}}
            data = provider('POST','payments',payload=payload,key=payment.pk)
            provider_id = data.get('id','')
            confirmation = data.get('confirmation') or {}
            url = confirmation.get('confirmation_url','') if isinstance(confirmation,dict) else ''
            if not isinstance(url,str):
                raise ValidationError('Некорректная ссылка оплаты.')
            parsed = urlsplit(url)
            safe_url = isinstance(url,str) and parsed.scheme == 'https' and parsed.hostname in {'yoomoney.ru','yookassa.ru'}
            if not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',provider_id) or (data.get('status') not in {'succeeded','canceled'} and not safe_url):
                raise ValidationError('Не удалось получить безопасную ссылку оплаты.')
            Payment.objects.filter(pk=payment.pk).update(provider_id=provider_id,confirmation_url=url if safe_url else '')
            payment.refresh_from_db()
            apply_provider(payment,data)
        else:
            apply_provider(payment,provider('GET','payments/'+payment.provider_id))
        payment.refresh_from_db()
        if payment.status in {'pending','waiting_for_capture'} and payment.confirmation_url:
            return redirect(payment.confirmation_url)
        messages.info(request,'Оплата подтверждена.' if payment.status == 'succeeded' else 'Платёж отменён. Обратитесь к продавцу.')
    except ValidationError as exc:
        messages.error(request,' '.join(exc.messages))
    return redirect('order',order_id=order.pk)


@csrf_exempt
@module_required('payments')
@require_POST
def webhook(request):
    if not configured():
        return HttpResponse(status=503)
    try:
        incoming = json.loads(request.body)
        provider_id = incoming['object']['id']
        if not isinstance(provider_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',provider_id):
            raise ValueError()
    except (ValueError,KeyError,TypeError):
        return HttpResponse(status=400)
    payment = Payment.objects.filter(provider_id=provider_id).first()
    if not payment:
        # Creation may still be persisting the ID; let the provider retry.
        return HttpResponse(status=503)
    try:
        apply_provider(payment,provider('GET','payments/'+provider_id))
    except ValidationError:
        return HttpResponse(status=503)
    return JsonResponse({'ok':True})
