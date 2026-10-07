"""A bounded zero-token dispatcher; uncertain requests continue to the AI editor."""
import re
from founder.blueprints.django_basic.module_catalog import BASE_MODULES, DEFAULT_BASE_FEATURES, MODULES, OPTIONAL_MODULES, normalize_modules

MODULE_CHOICES = [(key,MODULES[key][0]) for key in OPTIONAL_MODULES]
MODULE_GROUPS = (
    ('Базовые возможности', DEFAULT_BASE_FEATURES),
    ('Основы маркетплейса', ('leads', 'catalog', 'pages', 'uploads')),
    ('Работа с клиентами', ('favorites', 'reviews', 'booking')),
    ('Продажи и оплата', ('orders', 'payments')),
)
DEFAULT_OPTIONAL = [key for key in OPTIONAL_MODULES if key != 'payments']
PATTERNS = {
    'registration': r'регистрац\w*|зарегистр\w*|авторизац\w*|логин\w*|вход\w*|войти',
    'password_reset': r'сброс\w*\s+парол\w*|восстанов\w*\s+парол\w*',
    'profile': r'профил\w*|аватар\w*',
    'chat': r'чат\w*|сообщени\w*|переписк\w*|вебсокет\w*|websocket\w*',
    'notifications': r'уведомлени\w*',
    'team': r'рол[ьи]\w*|права\s+доступа|команд\w*',
    'catalog': r'каталог\w*|поиск\w*|фильтр\w*',
    'leads': r'заявк\w*|обратн\w*\s+связ\w*',
    'uploads': r'загрузк\w*\s+(?:файл\w*|фотографи\w*)|файл\w*|фотографи\w*|галере\w*',
    'favorites': r'избранн\w*',
    'booking': r'бронирован\w*|запис\w*\s+на\s+(?:при[её]м|консультац\w*)',
    'orders': r'корзин\w*|заказ\w*',
    'reviews': r'отзыв\w*',
    'pages': r'публикаци\w*|редактор\s+страниц\w*',
    'payments': r'оплат\w*|юкасс\w*|yookassa',
}
FILLER = set('добавь добавьте добавим добавить сделай сделайте создай создать подключи подключить пожалуйста пж мне нам на сайте сайт этот этом в наш нашем базовую базовый базовые простую простой простые функцию функции возможность так чтобы чтоб можно было и или также еще ещё с между пользователями пользователями мог могли смог можно нужно только регистрацию'.split())


def module_command(prompt):
    remaining = prompt.lower().replace('ё','е').strip()
    if re.search(r'\b(не|без|убери|удали|отключи)\b',remaining):
        return None
    found = []
    for key,pattern in PATTERNS.items():
        expression = r'\b(?:' + pattern + r')\b'
        if re.search(expression,remaining):
            found.append(key)
            remaining = re.sub(expression,' ',remaining)
    if not found or any(word not in FILLER for word in re.findall(r'\w+',remaining)):
        return None
    return normalize_modules(found)
