from io import BytesIO
from pathlib import Path
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Item, Lead, Membership, Page, Profile, Review, Slot


def email_for(user, email):
    email = email.strip().lower()
    if get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk if user else None).exists():
        raise ValidationError('Этот email уже используется.')
    if Profile.objects.filter(email_key=email).exclude(user=user).exists():
        raise ValidationError('Этот email уже используется.')
    return email


def safe_upload(upload, *, image_only=False):
    """Re-encode images (no SVG/metadata), other files only as attachments."""
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('Файл должен быть меньше 5 МБ.')
    suffix = Path(upload.name).suffix.lower()
    if suffix in {'.jpg', '.jpeg', '.png', '.webp'}:
        try:
            with Image.open(upload) as source:
                if source.width * source.height > 20_000_000:
                    raise ValidationError('Изображение слишком большое.')
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((1600, 1600))
                output = BytesIO()
                image.save(output, 'JPEG', quality=85)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValidationError('Не удалось прочитать изображение.') from None
        return ContentFile(output.getvalue(), name=uuid.uuid4().hex + '.jpg'), True
    if image_only:
        raise ValidationError('Выберите JPG, PNG или WebP.')
    data = upload.read()
    if suffix == '.pdf' and data.startswith(b'%PDF-'):
        return ContentFile(data, name=uuid.uuid4().hex + '.pdf'), False
    if suffix == '.txt':
        try:
            data.decode('utf-8')
        except UnicodeError:
            raise ValidationError('Текстовый файл должен быть в UTF-8.') from None
        return ContentFile(data, name=uuid.uuid4().hex + '.txt'), False
    raise ValidationError('Разрешены JPG, PNG, WebP, PDF и TXT.')


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label='Email для восстановления пароля', required=False)

    class Meta(UserCreationForm.Meta):
        fields = ('username', 'email')

    def clean_email(self):
        value = self.cleaned_data['email']
        return email_for(None, value) if value else ''

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            Profile.objects.create(user=user, email_key=user.email or None)
            Membership.objects.create(user=user)
        return user


class ProfileForm(forms.Form):
    username = forms.CharField(label='Логин', max_length=150, validators=get_user_model()._meta.get_field('username').validators)
    email = forms.EmailField(label='Email', required=False)
    display_name = forms.CharField(label='Имя', max_length=80, required=False)
    bio = forms.CharField(label='О себе', max_length=1000, required=False, widget=forms.Textarea(attrs={'rows':3}))
    avatar = forms.FileField(label='Аватарка', required=False)
    remove_avatar = forms.BooleanField(label='Удалить аватарку', required=False)
    email_notifications = forms.BooleanField(label='Получать уведомления на email', required=False)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        self.profile, _ = Profile.objects.get_or_create(user=user)
        kwargs.setdefault('initial', {**{key:getattr(self.profile, key) for key in ('display_name','bio','email_notifications')},
            'username':user.username, 'email':user.email})
        super().__init__(*args, **kwargs)

    def clean_username(self):
        name = self.cleaned_data['username']
        if get_user_model().objects.filter(username__iexact=name).exclude(pk=self.user.pk).exists():
            raise ValidationError('Этот логин занят.')
        return name

    def clean_email(self):
        value = self.cleaned_data['email']
        return email_for(self.user, value) if value else ''

    def clean_avatar(self):
        upload = self.cleaned_data.get('avatar')
        return safe_upload(upload, image_only=True)[0] if upload else None

    def save(self):
        from django.db import transaction
        with transaction.atomic():
            self.user.username = self.cleaned_data['username']
            self.user.email = self.cleaned_data['email']
            self.user.save(update_fields=['username','email'])
            for key in ('display_name', 'bio', 'email_notifications'):
                setattr(self.profile, key, self.cleaned_data[key])
            self.profile.email_key = self.user.email or None
            old = self.profile.avatar.name
            if self.cleaned_data.get('avatar'):
                self.profile.avatar = self.cleaned_data['avatar']
            elif self.cleaned_data['remove_avatar']:
                self.profile.avatar = ''
            self.profile.save()
            if old and old != self.profile.avatar.name:
                transaction.on_commit(lambda: self.profile.avatar.storage.delete(old))


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ['title','description','category','price','stock','published']
        labels = {'title':'Название','description':'Описание','category':'Категория','price':'Цена, ₽',
            'stock':'Количество (пусто — без ограничения)', 'published':'Опубликовать'}
        widgets = {'description':forms.Textarea(attrs={'rows':5})}


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ['name','email','text']
        labels = {'name':'Имя','email':'Email','text':'Что вас интересует'}
        widgets = {'text':forms.Textarea(attrs={'rows':4})}


class UploadForm(forms.Form):
    file = forms.FileField(label='Фотография или файл')

    def clean_file(self):
        return safe_upload(self.cleaned_data['file'])


class SlotForm(forms.ModelForm):
    class Meta:
        model = Slot
        fields = ['starts_at', 'ends_at']
        labels = {'starts_at':'Начало (время Москвы)','ends_at':'Окончание'}
        widgets = {key:forms.DateTimeInput(attrs={'type':'datetime-local'}, format='%Y-%m-%dT%H:%M') for key in fields}

    def clean(self):
        values = super().clean()
        start, end = values.get('starts_at'), values.get('ends_at')
        if start and end and (start <= timezone.now() or end <= start):
            raise ValidationError('Укажите будущее время и окончание после начала.')
        return values


class CheckoutForm(forms.Form):
    contact = forms.CharField(label='Контакты и пожелания к заказу', max_length=250)
    nonce = forms.UUIDField(widget=forms.HiddenInput)


class InviteForm(forms.Form):
    username = forms.CharField(label='Логин зарегистрированного участника',max_length=150)
    email = forms.EmailField(label='Email (необязательно)',required=False)
    role = forms.ChoiceField(label='Роль', choices=[('editor','Сотрудник'),('client','Участник')])

    def clean_username(self):
        user = get_user_model().objects.filter(username=self.cleaned_data['username'],is_active=True).first()
        if not user:
            raise ValidationError('Сначала участник должен зарегистрироваться на этом сайте.')
        return user


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating','text']
        labels = {'rating':'Оценка от 1 до 5','text':'Ваш отзыв'}
        widgets = {'text':forms.Textarea(attrs={'rows':3})}


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ['title','slug','text','published']
        labels = {'title':'Название','slug':'Адрес страницы','text':'Содержание','published':'Опубликовать'}
