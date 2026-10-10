"""Explicit, accessible widgets; Django still owns validation and escaping."""
from django import template

register = template.Library()
PLACEHOLDERS = {
    'username': 'Например, alex', 'email': 'you@example.com',
    'display_name': 'Как к вам обращаться', 'bio': 'Несколько слов о себе',
    'title': 'Короткое понятное название', 'description': 'Расскажите о предложении',
    'category': 'Например, Напитки', 'price': '0,00', 'stock': 'Без ограничения',
    'name': 'Ваше имя', 'text': 'Напишите здесь…', 'contact': 'Телефон, email или комментарий',
    'slug': 'Адрес латиницей, например about',
}


@register.filter
def ui_widget(field):
    widget = field.field.widget
    kind = getattr(widget, 'input_type', '')
    attrs = {}
    if kind not in {'checkbox', 'file', 'hidden'}:
        attrs['placeholder'] = PLACEHOLDERS.get(field.name, 'Введите пароль' if kind == 'password' else '')
    if field.name == 'username':
        attrs.update(autocomplete='username', autocapitalize='none', spellcheck='false')
    elif kind == 'email':
        attrs.update(autocomplete='email', inputmode='email')
    elif kind == 'password':
        attrs['autocomplete'] = 'current-password' if field.name in {'password', 'old_password'} else 'new-password'
    elif kind == 'file':
        attrs['data-file-input'] = ''
        if field.name == 'avatar': attrs['accept'] = 'image/jpeg,image/png,image/webp'
    descriptions = []
    if field.help_text: descriptions.append(field.auto_id + '_helptext')
    if field.errors:
        attrs['aria-invalid'] = 'true'
        descriptions.append(field.auto_id + '_errors')
    if descriptions: attrs['aria-describedby'] = ' '.join(descriptions)
    return field.as_widget(attrs=attrs)
