from django import forms

from founder.models import ProjectCard


CARD_FIELDS = ('name', 'tagline', 'summary', 'problem', 'solution', 'audience',
               'business_model', 'traction', 'looking_for', 'stage', 'share_radar')


class ProjectCardForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {'name': 'Название проекта', 'tagline': 'Идея проекта в одном предложении',
            'summary': 'Коротко расскажите, что вы создаёте и для кого.',
            'problem': 'Какую проблему вы решаете?', 'solution': 'Как работает ваше решение?',
            'audience': 'Кому нужен ваш продукт?', 'business_model': 'Как проект будет зарабатывать?',
            'traction': 'Первые проверки, клиенты или другие результаты.',
            'looking_for': 'Команда, партнёры, клиенты или инвестиции.'}
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs['placeholder'] = placeholder

    class Meta:
        model = ProjectCard
        fields = CARD_FIELDS
        widgets = {name: forms.Textarea(attrs={'rows': 1}) for name in
                   ('summary', 'problem', 'solution', 'traction', 'audience', 'business_model', 'looking_for', 'name', 'tagline')}


class CardRefineForm(forms.Form):
    instruction = forms.CharField(
        label='Что изменить вместе с Бруно', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Например: сделай описание короче; мы ищем разработчика, а не инвестиции.'}),
    )
