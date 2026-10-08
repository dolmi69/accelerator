from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import MaxLengthValidator

from founder.models import EvidenceEntry, LabSiteVersion, ProjectMember, StartupMetrics, StartupProfile, User
from founder.profile_forms import HandleValidationMixin
from founder.services.json_utils import bounded_json_loads


class RegisterForm(HandleValidationMixin, UserCreationForm):
    email = forms.EmailField(label="Email")
    handle = forms.CharField(label="Тег (можно выбрать позже)", max_length=33, required=False,
                             widget=forms.TextInput(attrs={'placeholder': '@your_name'}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "handle", "email", "password1", "password2")


class StartupForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ("problem", "solution", "target_customer"):
            self.fields[field].max_length = 6000
            self.fields[field].validators.append(MaxLengthValidator(6000))
            self.fields[field].widget.attrs["maxlength"] = 6000

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


class LabPromptForm(forms.Form):
    edit_scope = forms.ChoiceField(label="Что дорабатываем", required=False, choices=[("auto", "Автоматически")])
    rebuild = forms.BooleanField(label="Полностью пересобрать сайт (больше расход AI)", required=False)

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source
        if source:
            from founder.services.site_patches import fragments
            self.fields["edit_scope"].choices += [(part.key, part.label) for part in fragments(source.html)]

    kind = forms.ChoiceField(
        label="Какой сайт создаём", choices=LabSiteVersion.Kind.choices,
        initial=LabSiteVersion.Kind.STATIC, required=False,
    )

    def clean_kind(self):
        # The compact UI inherits the backend of the selected version.
        return self.cleaned_data.get("kind") or (self.source.kind if self.source else LabSiteVersion.Kind.STATIC)

    prompt = forms.CharField(
        label="Что создать или изменить",
        max_length=2000,
        widget=forms.Textarea(attrs={
            "rows": 5,
            "maxlength": 2000,
            "placeholder": "Например: сделай адаптивную страницу о здоровом сне с тремя советами и раскрывающимися карточками",
        }),
    )


class LabCustomizeForm(forms.Form):
    title = forms.CharField(label="Название сайта", max_length=100, required=False)
    palette = forms.ChoiceField(label="Цветовая тема", required=False, choices=[("", "Исходная"), ("blue", "Синяя"), ("green", "Зелёная"), ("purple", "Фиолетовая"), ("dark", "Тёмная")])
    font = forms.ChoiceField(label="Шрифт", required=False, choices=[("", "Исходный"), ("system", "Современный"), ("serif", "Классический"), ("mono", "Моноширинный")])
    radius = forms.ChoiceField(label="Форма кнопок", required=False, choices=[("", "Исходная"), ("0", "Прямые углы"), ("8", "Слегка округлые"), ("24", "Округлые")])


class LabBackendModulesForm(forms.Form):
    from founder.services.backend_modules import MODULE_CHOICES
    modules = forms.MultipleChoiceField(label='Готовые модули',choices=MODULE_CHOICES,
        required=False,widget=forms.CheckboxSelectMultiple)


class MetricsForm(forms.ModelForm):
    assessment_notes = forms.CharField(label="Общий вывод и следующий шаг", max_length=3000,
                                      required=False, widget=forms.Textarea(attrs={"rows": 3}))
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
        if not text.strip():
            raise forms.ValidationError("Файл пуст. Добавьте текст или выберите другой файл.")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise forms.ValidationError("В файле обнаружены двоичные данные. Загрузите текстовый файл.")
        if Path(attachment.name).suffix.lower() == ".json":
            try:
                bounded_json_loads(text, max_chars=2 * 1024 * 1024)
            except (ValueError, RecursionError) as exc:
                raise forms.ValidationError("Файл JSON содержит ошибку или слишком глубокую вложенность.") from exc
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


class TeamInviteForm(forms.Form):
    handle = forms.CharField(label="Тег участника", max_length=33,
                             widget=forms.TextInput(attrs={"placeholder": "@founder_tag", "autocomplete": "off"}))
    role = forms.ChoiceField(label="Роль", choices=ProjectMember.Role.choices, initial=ProjectMember.Role.EDITOR)

    def __init__(self, *args, startup, **kwargs):
        self.startup = startup
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        handle = cleaned.get("handle", "").strip().lstrip("@").lower()
        if not handle:
            return cleaned
        user = User.objects.filter(handle=handle, is_active=True).first()
        if user is None:
            self.add_error("handle", "Участник с таким тегом не найден. Тег есть в профиле: @name.")
        elif user.pk == self.startup.owner_id:
            self.add_error("handle", "Это владелец проекта.")
        else:
            cleaned["user"] = user
        return cleaned
