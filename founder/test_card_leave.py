from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import StartupProfile, User
from founder.services.project_cards import card_values, get_card


@override_settings(AI_PROVIDER='demo')
class CardLeaveMarkupTests(TestCase):
    """Разметка редактора карточки для предупреждения о несохранённых изменениях."""

    def setUp(self):
        self.owner = User.objects.create_user('carol', email='carol@example.test')
        self.project = StartupProfile.objects.create(owner=self.owner, name='Clinic',
                                                    one_line_pitch='Планирование для клиник')
        get_card(self.project)
        self.client.force_login(self.owner)
        self.url = reverse('card_edit', args=[self.project.pk])

    def test_editor_renders_leave_dialog_and_scripts_in_order(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="card-leave-dialog"', html)
        self.assertIn('data-leave-stay', html)
        self.assertIn('data-leave-go', html)
        self.assertIn('Уйти без сохранения', html)
        self.assertIn('card-leave.js', html)
        # card-leave.js должен грузиться раньше cards.js, который использует window.CardLeave.
        self.assertLess(html.index('card-leave.js'), html.index('cards.js'))

    def test_generated_page_is_marked_dirty_for_the_guard(self):
        # После «Оценить и собрать с Бруно» страница помечена как несохранённая.
        # Без ИИ в тестовой среде генерация отказывает, поэтому подменяем её ответом Бруно.
        card = get_card(self.project)
        with patch('founder.community_views.generate_card', return_value={'tagline': 'Новая идея'}):
            html = self.client.post(self.url, {
                **card_values(card), 'action': 'generate', 'revision': card.revision,
            }).content.decode()
        self.assertIn('data-generated="1"', html)

    def test_saved_page_is_not_marked_dirty(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-generated="0"', html)
