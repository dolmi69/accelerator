"""Three editable example records, seeded once without an AI request."""
from uuid import uuid4
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from .capabilities import enabled
from .models import Item, Membership, SiteControl


def examples():
    text = (settings.SITE.get('name', '') + ' ' + settings.SITE.get('catalog_context', '')).lower()
    if any(word in text for word in ('коф', 'coffee', 'напит')):
        titles, category, prices = ['Эспрессо', 'Капучино', 'Латте'], 'Напитки', [150, 220, 250]
    elif any(word in text for word in ('аренд', 'инструмент', 'оборудован')):
        titles, category, prices = ['Дрель', 'Перфоратор', 'Шлифмашина'], 'Оборудование', [500, 800, 600]
    elif any(word in text for word in ('парф', 'perfum', 'аромат')):
        titles, category, prices = ['Цитрусовый аромат', 'Древесный аромат', 'Цветочный аромат'], 'Ароматы', [1500, 1800, 1600]
    elif any(word in text for word in ('учеб', 'учёб', 'тихое', 'silent', 'коворкин')):
        titles, category, prices = ['Место у окна', 'Тихая зона', 'Переговорная'], 'Места', [200, 250, 500]
    else:
        titles, category, prices = ['Пример товара 1', 'Пример товара 2', 'Пример товара 3'], 'Примеры', [100, 200, 300]
    return [dict(title=title, category=category, price=price, stock=3, published=True,
                 description='Демонстрационная позиция. Цена и описание — примеры. Замените их своими данными перед публикацией.')
            for title, price in zip(titles, prices)]


def seed_catalog_examples():
    if not enabled('catalog'):
        return 0
    with transaction.atomic():
        control, _ = SiteControl.objects.get_or_create(pk=1)
        if not SiteControl.objects.filter(pk=1, catalog_examples_initialized=False).update(catalog_examples_initialized=True):
            return 0
        # An existing catalogue belongs to its owner; never add examples to it.
        if Item.objects.exists():
            return 0
        owner_link = Membership.objects.filter(role='owner').select_related('user').first()
        owner = owner_link.user if owner_link else get_user_model().objects.filter(is_superuser=True).first()
        if owner is None:
            owner = get_user_model().objects.create_user(username='examples_' + uuid4().hex, is_active=False)
            SiteControl.objects.filter(pk=1).update(catalog_example_owner=owner)
        Item.objects.bulk_create([Item(owner=owner, **data) for data in examples()])
        return 3


def claim_catalog_examples(owner):
    control = SiteControl.objects.get(pk=1)
    if control.catalog_example_owner_id:
        Item.objects.filter(owner_id=control.catalog_example_owner_id).update(owner=owner)
        control.catalog_example_owner = None
        control.save(update_fields=['catalog_example_owner'])
