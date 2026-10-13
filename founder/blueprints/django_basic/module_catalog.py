"""Reviewed capabilities. This file is shared by the exporter and generated site."""
# Login and owner access remain available even with public signup disabled.
BASE_MODULES = ('accounts', 'profile', 'team')
DEFAULT_BASE_FEATURES = ('registration', 'password_reset', 'chat', 'notifications')
DEFAULT_MODULES = ('registration',)
MODULES = {
    'accounts': ('Вход в аккаунт', ()),
    'registration': ('Регистрация', ()),
    'password_reset': ('Сброс пароля', ()),
    'profile': ('Профиль и аватарка', ()),
    'chat': ('Чаты между пользователями', ()),
    'notifications': ('Уведомления', ()),
    'team': ('Команда и права доступа', ()),
    'catalog': ('Каталог, поиск и фильтры', ()),
    'leads': ('Заявки и обратная связь', ()),
    'uploads': ('Фотографии и файлы', ()),
    'favorites': ('Избранное', ('catalog',)),
    'booking': ('Бронирование времени', ('catalog',)),
    'orders': ('Корзина и заказы', ('catalog',)),
    'reviews': ('Отзывы с модерацией', ('catalog',)),
    'pages': ('Страницы и публикации', ()),
    'payments': ('Оплата через ЮKassa (нужен свой магазин)', ('orders',)),
}
OPTIONAL_MODULES = tuple(key for key in MODULES if key not in BASE_MODULES)


def normalize_modules(values=None):
    if values is None:
        values = DEFAULT_MODULES
    if not isinstance(values, (list, tuple, set)) or any(key not in MODULES for key in values):
        raise ValueError('Неизвестный модуль сайта')
    chosen = set(BASE_MODULES) | set(values)
    pending = list(chosen)
    while pending:
        for dependency in MODULES[pending.pop()][1]:
            if dependency not in chosen:
                chosen.add(dependency)
                pending.append(dependency)
    return [key for key in MODULES if key in chosen]
