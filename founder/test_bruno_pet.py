from django.test import TestCase
from django.urls import reverse

from founder.models import ChatSession, StartupProfile, User


class BrunoPetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pet-owner', email='pet@example.com')
        self.other = User.objects.create_user(username='pet-other', email='other@example.com')
        self.project = StartupProfile.objects.create(owner=self.user, name='My project')
        self.client.force_login(self.user)

    def test_remembers_visited_project_and_resumes_cofounder_chat(self):
        StartupProfile.objects.create(owner=self.user, name='Newer project')
        chat = ChatSession.objects.create(startup=self.project, mode=ChatSession.Mode.COFOUNDER)
        ChatSession.objects.create(startup=self.project, mode=ChatSession.Mode.PITCH)
        self.client.get(reverse('dashboard', args=[self.project.pk]))
        response = self.client.get(reverse('home'))
        pet = response.context['bruno_pet']
        self.assertEqual(pet['project'], self.project)
        self.assertEqual(pet['chat'], chat)
        self.assertContains(response, reverse('chat_detail', args=[self.project.pk, chat.pk]))
        self.assertEqual(ChatSession.objects.count(), 2)

    def test_stale_foreign_project_is_never_exposed(self):
        foreign = StartupProfile.objects.create(owner=self.other, name='Private project')
        session = self.client.session
        session['bruno_last_project'] = str(foreign.pk)
        session.save()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['bruno_pet']['project'], self.project)
        self.assertNotContains(response, 'Private project')

    def test_new_user_reaches_project_list_and_anonymous_has_no_pet(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('startup_create'))
        self.assertContains(response, 'Перейти к моим проектам')
        self.assertRedirects(self.client.get(reverse('home')), reverse('startup_create'))
        self.assertEqual(ChatSession.objects.count(), 0)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('login')), 'data-bruno-pet')

    def test_guest_home_is_welcome_screen_with_register_and_login(self):
        self.client.logout()
        response = self.client.get(reverse('home'))
        self.assertTemplateUsed(response, 'founder/welcome.html')
        self.assertContains(response, 'Здравствуйте. Я Бруно.')
        self.assertContains(response, f'href="{reverse("register")}"')
        self.assertContains(response, f'href="{reverse("login")}"')
        self.assertContains(response, 'data-pose="walk"')
        self.assertNotContains(response, 'data-bruno-pet')

    def test_signed_in_pages_carry_intro_except_lab_and_guest_pages(self):
        page = self.client.get(reverse('dashboard', args=[self.project.pk]))
        self.assertContains(page, 'data-intro')
        self.assertContains(page, 'intro.js')
        self.assertContains(page, 'Здравствуйте, ')
        self.assertNotContains(self.client.get(reverse('lab', args=[self.project.pk])), 'data-intro')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('login')), 'data-intro')

    def test_login_and_register_show_bruno_watching_the_form(self):
        self.client.logout()
        for name in ('login', 'register'):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertContains(page, 'data-bruno-watcher')
                self.assertRegex(page.content.decode(), r'<form\b[^>]*\sdata-bruno-watch(?:\s|>)')
