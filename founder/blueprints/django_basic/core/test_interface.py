"""Custom forms preserve authentication, accessibility and real module data."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Conversation, Message, Profile, Membership, Item


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class InterfaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='alex', password='Forest-73-Lake!')
        Profile.objects.create(user=self.user)
        Membership.objects.create(user=self.user, role='owner')

    def test_custom_signup_renders_bound_errors_and_successfully_registers(self):
        response = self.client.post('/register/', {'username':'alex', 'password1':'short', 'password2':'other'})
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="id_username_errors"')
        self.assertContains(response, 'data-password-toggle="id_password1"')
        response = self.client.post('/register/', {'username':'new_user', 'password1':'Forest-73-Lake!', 'password2':'Forest-73-Lake!'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(get_user_model().objects.filter(username='new_user').exists())

    def test_login_keeps_destination_and_real_csrf_form(self):
        response=self.client.get('/login/?next=/profile/')
        self.assertContains(response,'name="next" value="/profile/"')
        self.assertContains(response,'name="csrfmiddlewaretoken"')
        self.assertContains(response,'autocomplete="current-password"')
        response=self.client.post('/login/',{'username':'alex','password':'Forest-73-Lake!','next':'/profile/'})
        self.assertRedirects(response,'/profile/')

    def test_profile_update_works_with_explicit_field_groups(self):
        self.client.force_login(self.user)
        response=self.client.get('/profile/')
        self.assertContains(response,'profile-editor')
        self.assertContains(response,'accept="image/jpeg,image/png,image/webp"')
        response=self.client.post('/profile/',{'username':'alex','email':'alex@example.com','display_name':'Александр','bio':'<script>alert(1)</script>'})
        self.assertRedirects(response,'/profile/')
        self.user.site_profile.refresh_from_db()
        self.assertEqual(self.user.site_profile.display_name,'Александр')
        self.assertContains(self.client.get(reverse('public_profile',args=[self.user.pk])),'&lt;script&gt;')

    def test_new_conversation_preview_uses_real_last_message_and_is_private(self):
        other=get_user_model().objects.create_user(username='marina')
        outsider=get_user_model().objects.create_user(username='eve')
        chat=Conversation.objects.create(first=self.user,second=other)
        Message.objects.create(conversation=chat,sender=other,content='Ваш заказ готов')
        foreign=Conversation.objects.create(first=other,second=outsider)
        Message.objects.create(conversation=foreign,sender=outsider,content='Чужой секрет')
        self.client.force_login(self.user)
        response=self.client.get('/messages/')
        self.assertContains(response,'Ваш заказ готов')
        self.assertNotContains(response,'Чужой секрет')
        response=self.client.get(reverse('chat',args=[chat.pk]))
        self.assertContains(response,'id="send-form"')
        self.assertContains(response,'data-conversation="'+str(chat.pk)+'"')

    def test_no_fake_modules_in_navigation_when_disabled(self):
        self.client.force_login(self.user)
        with override_settings(SITE={**settings.SITE,'modules':[]}):
            response=self.client.get('/profile/')
        self.assertNotContains(response,'href="/messages/"')
        self.assertNotContains(response,'href="/notifications/"')
        self.assertNotContains(response,'name="email_notifications"')

    def test_catalog_filter_selection_survives_and_checkout_preserves_hidden_nonce(self):
        Item.objects.create(owner=self.user,title='Латте',price=250,published=True)
        response=self.client.get('/catalog/?sort=title&q=Латте')
        self.assertContains(response,'value="title" selected')
        self.assertContains(response,'Латте')
        self.client.force_login(self.user)
        item=Item.objects.get()
        self.client.post(reverse('cart_update',args=[item.pk]),{'quantity':1})
        response=self.client.get('/checkout/')
        self.assertContains(response,'name="nonce"')
        self.assertContains(response,'type="hidden"')
