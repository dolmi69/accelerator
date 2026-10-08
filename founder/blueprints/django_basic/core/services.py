"""Domain mutations: access is checked by views; invariants live here too."""
import logging
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from .models import Booking, Item, Membership, Notification, Order, OrderLine, Profile, Slot

from .capabilities import enabled

logger = logging.getLogger(__name__)


def notify(user_id, text, url='', *, key=None):
    if not enabled('notifications'):
        return None
    values = {'user_id':user_id, 'text':text[:250], 'url':url}
    if key:
        row, created = Notification.objects.get_or_create(dedupe_key=key, defaults=values)
    else:
        row, created = Notification.objects.create(**values), True
    if created:
        # Database commits first. Mail failure must not roll back a booking/order.
        transaction.on_commit(lambda: email_notice(user_id, text))
    return row


def email_notice(user_id, text):
    if not settings.EMAIL_NOTIFICATIONS_ENABLED:
        return
    profile = Profile.objects.filter(user_id=user_id, email_notifications=True).select_related('user').first()
    if profile and profile.user.email:
        try:
            send_mail(settings.SITE['name'], text, settings.DEFAULT_FROM_EMAIL, [profile.user.email])
        except Exception:
            logger.warning('Notification email delivery failed')


def notify_team(text, url=''):
    for user_id in Membership.objects.filter(role__in=['owner','editor']).values_list('user_id', flat=True)[:50]:
        notify(user_id, text, url)


@transaction.atomic
def reserve_slot(user, slot_id):
    slot = Slot.objects.select_related('item').get(pk=slot_id)
    if slot.starts_at <= timezone.now() or not slot.item.published:
        raise ValidationError('Это время уже недоступно.')
    if not Slot.objects.filter(pk=slot_id, reserved=False).update(reserved=True):
        raise ValidationError('Это время уже забронировали. Выберите другое.')
    booking = Booking.objects.create(user=user, slot=slot)
    notify(slot.item.owner_id, f'Новая запись: {slot.item.title}', reverse('bookings'))
    notify(user.pk, f'Вы записались: {slot.item.title}', reverse('bookings'))
    return booking


@transaction.atomic
def cancel_booking(booking):
    if Booking.objects.filter(pk=booking.pk, canceled=False).update(canceled=True):
        Slot.objects.filter(pk=booking.slot_id).update(reserved=False)
        notify(booking.user_id, 'Запись отменена', reverse('bookings'))


def cart_rows(cart):
    if not isinstance(cart, dict) or len(cart) > 50:
        return []
    valid = {int(key):quantity for key, quantity in cart.items() if str(key).isdigit()
        and type(quantity) is int and 1 <= quantity <= 100}
    return [{'item':item, 'quantity':valid[item.pk], 'subtotal':item.price * valid[item.pk]}
        for item in Item.objects.filter(pk__in=valid, published=True, price__gt=0)]


@transaction.atomic
def checkout(user, cart, nonce, contact):
    old = Order.objects.filter(user=user, nonce=nonce).first()
    if old:
        return old
    rows = cart_rows(cart)
    if not rows or len(rows) != len(cart):
        raise ValidationError('Корзина изменилась. Проверьте товары перед заказом.')
    sellers = {row['item'].owner_id for row in rows}
    if len(sellers) != 1:
        raise ValidationError('Оформляйте товары разных продавцов отдельными заказами.')
    order = Order.objects.create(user=user, seller_id=sellers.pop(), nonce=nonce,
        contact=contact, total=sum((row['subtotal'] for row in rows), Decimal('0')))
    for row in rows:
        item, quantity = row['item'], row['quantity']
        # Conditional update also prevents overselling on SQLite (no row locks).
        if item.stock is not None:
            if not Item.objects.filter(pk=item.pk, stock__gte=quantity, price=item.price, published=True).update(stock=F('stock')-quantity):
                raise ValidationError(f'Недостаточно товара «{item.title}» или его цена изменилась.')
        elif not Item.objects.filter(pk=item.pk, stock__isnull=True, price=item.price, published=True).exists():
            raise ValidationError('Товар изменился. Проверьте корзину.')
        OrderLine.objects.create(order=order, item=item, title=item.title, price=item.price,
            quantity=quantity, stock_reserved=item.stock is not None)
    notify(order.seller_id, f'Новый заказ №{order.pk}', reverse('order', args=[order.pk]))
    notify(user.pk, f'Заказ №{order.pk} сохранён', reverse('order', args=[order.pk]))
    return order


@transaction.atomic
def transition_order(order, status):
    transitions = {'new':{'processing','canceled'}, 'processing':{'done','canceled'}}
    current = Order.objects.get(pk=order.pk)
    if status not in transitions.get(current.status, set()):
        raise ValidationError('Такой переход статуса недоступен.')
    if status == 'canceled' and current.payment_settled:
        raise ValidationError('Оплаченный заказ отменяется после возврата через платёжный сервис.')
    if Order.objects.filter(pk=order.pk, status=current.status).update(status=status):
        if status == 'canceled':
            for line in current.lines.filter(stock_reserved=True, item__isnull=False):
                Item.objects.filter(pk=line.item_id, stock__isnull=False).update(stock=F('stock')+line.quantity)
        notify(current.user_id, f'Заказ №{order.pk}: {Order.Status(status).label}', reverse('order', args=[order.pk]))
