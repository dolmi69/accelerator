from django import forms

from founder.models import ProjectCard


CARD_FIELDS = ('name', 'tagline', 'summary', 'problem', 'solution', 'audience',
               'business_model', 'traction', 'looking_for', 'stage', 'website', 'share_radar')


class ProjectCardForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = ProjectCard
        fields = CARD_FIELDS
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in
                   ('summary', 'problem', 'solution', 'traction')}


class CardRefineForm(forms.Form):
    instruction = forms.CharField(
        label='Что изменить вместе с Бруно', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Например: сделай описание короче; мы ищем разработчика, а не инвестиции.'}),
    )
