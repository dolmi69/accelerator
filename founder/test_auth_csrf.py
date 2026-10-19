"""Regression coverage for a form left open while another tab rotates CSRF."""
from django.conf import settings
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from founder.models import User


@override_settings(AI_PROVIDER="demo", ALLOWED_HOSTS=["testserver"],
                   CSRF_TRUSTED_ORIGINS=[], SECURE_SSL_REDIRECT=False)
class AuthenticationCsrfTests(TestCase):
    def setUp(self):
        self.browser = Client(enforce_csrf_checks=True)
        self.origin = {"secure": True, "HTTP_ORIGIN": "https://testserver"}
        self.fields = {"username": "csrf_founder", "email": "csrf@example.test",
                       "password1": "PaperCactus42!", "password2": "PaperCactus42!"}

    def page_token(self):
        response = self.browser.get(reverse("register"), secure=True)
        return response, str(response.context["csrf_token"])

    def test_fresh_form_still_registers_without_javascript(self):
        response, token = self.page_token()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, 'data-auth-form')
        self.assertContains(response, 'founder/js/auth.js')
        result = self.browser.post(reverse("register"),
                                   {**self.fields, "csrfmiddlewaretoken": token}, **self.origin)
        self.assertRedirects(result, reverse("startup_create"), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username="csrf_founder").exists())

    def test_stale_form_is_rejected_but_current_masked_token_restores_registration(self):
        _, stale = self.page_token()
        rotated = _get_new_csrf_string()
        self.browser.cookies[settings.CSRF_COOKIE_NAME] = rotated
        rejected = self.browser.post(reverse("register"),
                                     {**self.fields, "csrfmiddlewaretoken": stale}, **self.origin)
        self.assertContains(rejected, "Обновите форму", status_code=403)
        self.assertContains(rejected, 'href="/register/"', status_code=403)
        self.assertNotContains(rejected, self.fields["password1"], status_code=403)
        self.assertIn("no-store", rejected["Cache-Control"])
        self.assertFalse(User.objects.filter(username="csrf_founder").exists())

        fresh = self.browser.get(reverse("auth_csrf_token"), secure=True)
        self.assertEqual(len(fresh.json()["csrfToken"]), 64)
        self.assertIn("no-store", fresh["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", fresh)
        # Refreshing returns a masked token, never the secret or a rotation.
        self.assertNotEqual(fresh.json()["csrfToken"], rotated)
        self.assertEqual(self.browser.cookies[settings.CSRF_COOKIE_NAME].value, rotated)
        result = self.browser.post(reverse("register"),
                                   {**self.fields, "csrfmiddlewaretoken": fresh.json()["csrfToken"]},
                                   **self.origin)
        self.assertEqual(result.status_code, 302)
        self.assertTrue(User.objects.filter(username="csrf_founder").exists())

    def test_missing_cookie_and_foreign_origin_remain_rejected(self):
        _, token = self.page_token()
        result = self.browser.post(reverse("register"),
                                   {**self.fields, "csrfmiddlewaretoken": token}, secure=True,
                                   HTTP_ORIGIN="https://outsider.example")
        self.assertEqual(result.status_code, 403)
        self.browser.cookies.clear()
        result = self.browser.post(reverse("register"),
                                   {**self.fields, "csrfmiddlewaretoken": token}, **self.origin)
        self.assertEqual(result.status_code, 403)
        self.assertFalse(User.objects.filter(username="csrf_founder").exists())

    def test_login_form_also_refreshes_token(self):
        result = self.browser.get(reverse("login"), secure=True)
        self.assertContains(result, 'data-auth-form')
        self.assertContains(result, 'founder/js/auth.js')
        self.assertIn("no-store", result["Cache-Control"])

    def test_refresh_is_read_only(self):
        self.assertEqual(self.browser.post(reverse("auth_csrf_token")).status_code, 403)
        browser = Client()
        self.assertEqual(browser.post(reverse("auth_csrf_token")).status_code, 405)
