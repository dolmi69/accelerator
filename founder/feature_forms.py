from decimal import Decimal
from django import forms
from django.utils import timezone
from founder.models import FeatureCandidate,KanoResponse

class RiceImpactField(forms.TypedChoiceField):
    def prepare_value(self, value):
        # DecimalField reads 2 as Decimal('2.00'); option values use '2'.
        if isinstance(value, Decimal):
            return format(value.normalize(), 'f')
        return super().prepare_value(value)


class FeatureForm(forms.ModelForm):
    rice_impact=RiceImpactField(label='Влияние RICE',required=False,coerce=Decimal,empty_value=None,
        choices=[('','Не оценено'),('0.25','0,25 — минимальное'),('0.5','0,5 — небольшое'),('1','1 — среднее'),('2','2 — сильное'),('3','3 — очень сильное')])
    class Meta:
        model=FeatureCandidate
        fields=['title','description','reach','rice_impact','confidence','effort','ice_impact','ease','estimate_basis','priority','question_present','question_absent']
        labels={'title':'Название функции','description':'Для кого и какую задачу решает',
            'reach':'Охват за 30 дней, человек','confidence':'Уверенность в оценке, %','effort':'Трудозатраты, человеко-дни',
            'ice_impact':'Влияние ICE, 1–10','ease':'Простота реализации, 1–10','estimate_basis':'На чём основаны оценки',
            'priority':'MoSCoW — ваш выбор','question_present':'Если функция есть','question_absent':'Если функции нет'}
        widgets={'description':forms.Textarea(attrs={'rows':2}),
            'priority':forms.Select(),'question_present':forms.Textarea(attrs={'rows':2}),'question_absent':forms.Textarea(attrs={'rows':2})}
    def __init__(self,*args,startup=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.startup=startup
        self.fields['priority'].choices=[('','Предложить по рейтингу'),*FeatureCandidate.Priority.choices]
        for field in self.fields.values():
            field.widget.attrs['aria-label']=field.label
        if self.instance.pk and self.instance.kano_responses.exists():
            for name in ('question_present','question_absent'):
                self.fields[name].disabled=True
                self.fields[name].help_text='После начала опроса формулировка сохраняется, чтобы ответы оставались сравнимыми.'
    def clean_title(self):
        title=' '.join(self.cleaned_data['title'].split())
        existing=self.startup.feature_candidates.exclude(pk=self.instance.pk).values_list('title',flat=True)
        if any(title.casefold()==name.casefold() for name in existing):
            raise forms.ValidationError('Такая функция уже есть в проекте.')
        return title

class KanoForm(forms.ModelForm):
    class Meta:
        model=KanoResponse
        fields=['respondent','present','absent','observed_on','source','notes']
        labels={'respondent':'Анонимный код респондента','present':'Ответ: функция есть','absent':'Ответ: функции нет',
            'observed_on':'Дата разговора','source':'Источник ответа','notes':'Заметка (необязательно)'}
        widgets={'observed_on':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),
            'respondent':forms.TextInput(attrs={'placeholder':'Например, респондент-01'}),
            'source':forms.TextInput(attrs={'placeholder':'Например, личное интервью или анкета'}),
            'notes':forms.Textarea(attrs={'rows':2})}
    def __init__(self,*args,feature=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.feature=feature
        for field in self.fields.values():
            field.widget.attrs['aria-label']=field.label
    def clean_observed_on(self):
        date=self.cleaned_data['observed_on']
        if date>timezone.localdate():raise forms.ValidationError('Нельзя указать будущую дату наблюдения.')
        return date
    def clean_respondent(self):
        value=' '.join(self.cleaned_data['respondent'].split())
        if self.feature.kano_responses.exclude(pk=self.instance.pk).filter(respondent__iexact=value).exists():
            raise forms.ValidationError('Ответ этого человека уже сохранён. Используйте «Исправить» у существующего ответа.')
        return value
