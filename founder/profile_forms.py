"""Member identity is independent of login credentials."""
from django import forms
from django.core.validators import URLValidator

from founder.models import User, handle_validator
from founder.services.avatars import prepare_avatar


class HandleValidationMixin:
    def clean_handle(self):
        value = self.cleaned_data['handle'].strip().removeprefix('@').lower()
        if not value:
            # Registration can suggest the login; a collision uses the generated default.
            candidate = self.cleaned_data.get('username', '').lower()
            try:
                handle_validator(candidate)
            except forms.ValidationError:
                candidate = self.instance.handle
            if User.objects.filter(handle__iexact=candidate).exclude(pk=self.instance.pk).exists():
                candidate = self.instance.handle
            value = candidate
        handle_validator(value)
        if User.objects.filter(handle__iexact=value).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Этот тег уже занят. Попробуйте другой.')
        return value


PROFILE_FIELDS = ('display_name', 'handle', 'occupation', 'bio', 'location', 'profile_website')


class UserProfileForm(HandleValidationMixin, forms.ModelForm):
    handle = forms.CharField(label='Ваш тег', max_length=33, help_text='3–32 символа: латинские буквы, цифры и _. Начните с буквы.')
    avatar_upload = forms.FileField(label='Новая аватарка', required=False,
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
        help_text='JPG, PNG или WebP до 5 МБ. Фото будет обрезано по центру в квадрат.')
    remove_avatar = forms.BooleanField(label='Удалить текущую аватарку', required=False)
    profile_website = forms.URLField(label='Сайт или портфолио', required=False,
                                    validators=[URLValidator(schemes=['http', 'https'])])

    class Meta:
        model = User
        fields = PROFILE_FIELDS
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Над чем работаете, чем можете помочь и с кем хотите познакомиться…'}),
            'display_name': forms.TextInput(attrs={'placeholder': 'Как к вам обращаться'}),
            'occupation': forms.TextInput(attrs={'placeholder': 'Например: основатель, Python-разработчик'}),
            'location': forms.TextInput(attrs={'placeholder': 'Необязательно'}),
        }

    def clean_avatar_upload(self):
        upload = self.cleaned_data.get('avatar_upload')
        return prepare_avatar(upload) if upload else None

    def clean(self):
        data = super().clean()
        if data.get('avatar_upload') and data.get('remove_avatar'):
            self.add_error('remove_avatar', 'Выберите одно действие: загрузить новую аватарку или удалить текущую.')
        return data
