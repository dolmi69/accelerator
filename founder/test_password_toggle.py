from django.test import TestCase
from django.urls import reverse


class PasswordToggleTests(TestCase):
    """Кнопка показа пароля подключена на страницах входа и регистрации."""

    def assert_toggle_assets(self, html):
        self.assertIn('password-toggle.js', html)
        self.assertIn('password-toggle.css', html)

    def test_login_page_loads_password_toggle(self):
        html = self.client.get(reverse('login')).content.decode()
        self.assert_toggle_assets(html)
        self.assertIn('type="password"', html)

    def test_register_page_loads_password_toggle_for_both_password_fields(self):
        html = self.client.get(reverse('register')).content.decode()
        self.assert_toggle_assets(html)
        # password1 и password2 из UserCreationForm
        self.assertIn('name="password1"', html)
        self.assertIn('name="password2"', html)

    def test_other_pages_do_not_load_password_toggle(self):
        html = self.client.get(reverse('login')).content.decode()
        # скрипт только на страницах входа и регистрации, не в общем base.html
        base_html = self.client.get('/').content.decode()
        self.assertNotIn('password-toggle.js', base_html)
        self.assertIn('password-toggle.js', html)
