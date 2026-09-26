import json
from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm

from founder.models import EvidenceEntry, StartupMetrics, StartupProfile, User


class RegisterForm(UserCreationForm):
    email = forms.EmailField(label="Email")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")


class StartupForm(forms.ModelForm):
    class Meta:
        model = StartupProfile
        fields = (
            "name", "one_line_pitch", "problem", "solution",
            "target_customer", "stage", "website",
        )
        labels = {"website": "Сайт (необязательно)"}
        widgets = {
            "problem": forms.Textarea(attrs={"rows": 3}),
            "solution": forms.Textarea(attrs={"rows": 3}),
            "target_customer": forms.Textarea(attrs={"rows": 3}),
            "website": forms.URLInput(attrs={"placeholder": "Можно добавить позже"}),
        }


class MetricsForm(forms.ModelForm):
    product_reason = forms.CharField(label="Что известно о продукте", max_length=500, required=False)
    market_reason = forms.CharField(label="Что известно о рынке", max_length=500, required=False)
    finance_reason = forms.CharField(label="Что известно о финансах", max_length=500, required=False)
    team_reason = forms.CharField(label="Что известно о команде", max_length=500, required=False)
    pitch_reason = forms.CharField(label="Что известно о ясности идеи", max_length=500, required=False)

    class Meta:
        model = StartupMetrics
        fields = ("product", "market", "finance", "team", "pitch", "assessment_notes")
        labels = {
            "product": "Продукт",
            "market": "Рынок",
            "finance": "Финансы",
            "team": "Команда",
            "pitch": "Ясность идеи",
            "assessment_notes": "Общий вывод и следующий шаг",
        }
        widgets = {
            "assessment_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("product", "market", "finance", "team", "pitch"):
            self.fields[name].widget.attrs.update({"min": 0, "max": 100, "step": 1})
            self.fields[name].help_text = "Оценка от 0 до 100"
            self.fields[f"{name}_reason"].widget.attrs.update({
                "placeholder": "Факт, гипотеза или вопрос для уточнения",
            })


class ChatSendForm(forms.Form):
    content = forms.CharField(max_length=4000, required=False, strip=True)
    attachment = forms.FileField(required=False)

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return None
        suffix = Path(attachment.name).suffix.lower()
        if suffix not in {".txt", ".md", ".csv", ".json"}:
            raise forms.ValidationError("Поддерживаются TXT, MD, CSV и JSON.")
        if attachment.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Максимальный размер файла — 2 МБ.")
        return attachment

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("content") and not cleaned.get("attachment"):
            raise forms.ValidationError("Напишите сообщение или добавьте файл.")
        return cleaned

    def extracted_text(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return ""
        raw = attachment.read()
        attachment.seek(0)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError("Файл должен быть в кодировке UTF-8.") from exc
        if Path(attachment.name).suffix.lower() == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError("Файл JSON содержит ошибку.") from exc
        return text[:12000]


class EvidenceForm(forms.ModelForm):
    class Meta:
        model = EvidenceEntry
        fields = ('task', 'axis', 'claim', 'observation', 'observed_on', 'outcome', 'source', 'source_url', 'next_question')
        labels = {'task': 'Связанное задание (необязательно)'}
        widgets = {
            'observation': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Например: показали прототип пяти владельцам магазинов. Двое согласились на пилот, оплат пока нет.'}),
            'observed_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'next_question': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, startup, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.startup = startup
        self.fields['task'].queryset = startup.bruno_tasks.all()
        self.fields['task'].label_from_instance = lambda task: task.title
        if self.instance.pk and not self.instance._state.adding:
            self.fields['task'].disabled = True

    def clean_observed_on(self):
        from django.utils import timezone
        value = self.cleaned_data['observed_on']
        if value > timezone.localdate():
            raise forms.ValidationError('Для планов используйте задания. Укажите дату уже состоявшегося наблюдения.')
        return value
