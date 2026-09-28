from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from founder.models import DirectConversation, DirectMessage, StartupProfile, User, UserBlock
from founder.profile_forms import UserProfileForm
from founder.services.project_cards import card_values, get_card, save_card
from founder.services.user_profiles import save_user_profile


def image_upload(name='photo.png', size=(160, 90), image_format='PNG'):
    output = BytesIO()
    image = Image.new('RGB', size, '#16877d')
    exif = Image.Exif(); exif[0x010e] = 'PRIVATE METADATA'
    image.save(output, image_format, exif=exif)
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


class ProfileTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.temp.name)
        self.media.enable(); self.addCleanup(self.media.disable)
        self.owner = User.objects.create_user('login_unchanged', email='private@example.test', password='StrongPass987!', handle='founder_one')
        self.other = User.objects.create_user('other_login', email='hidden@example.test', handle='founder_two')
        self.client.force_login(self.owner)
        self.url = reverse('profile_edit')

    def data(self, **kwargs):
        return {'handle': 'founder_one', 'display_name': 'Эльдар', 'bio': 'Создаю полезные сервисы.',
                'occupation': 'Основатель и разработчик', 'location': 'Москва', 'profile_website': 'https://example.test', **kwargs}

    def test_profile_changes_do_not_change_login_password_email_or_privileges(self):
        response = self.client.post(self.url, self.data(handle='@New_Tag', username='hacker', email='hacker@example.test', is_staff='1', is_superuser='1', password='hacked'))
        self.assertRedirects(response, reverse('user_profile', args=['new_tag']))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.username, 'login_unchanged')
        self.assertEqual(self.owner.email, 'private@example.test')
        self.assertTrue(self.owner.check_password('StrongPass987!'))
        self.assertFalse(self.owner.is_staff)
        self.assertFalse(self.owner.is_superuser)
        self.assertEqual(self.owner.display_name, 'Эльдар')
        self.assertContains(self.client.get(self.owner.get_absolute_url()), '@new_tag')
        self.assertRedirects(self.client.get(reverse('user_profile', args=['NEW_TAG'])), self.owner.get_absolute_url())

    def test_tags_are_unique_normalized_and_validated(self):
        for handle in ('@FOUNDER_TWO', 'я_основатель', 'two words', 'xy', '8founder', 'a'*33, ''):
            response = self.client.post(self.url, self.data(handle=handle))
            self.assertEqual(response.status_code, 400, handle)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.handle, 'founder_one')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.owner.pk).update(handle='founder_two')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.owner.pk).update(handle='FOUNDER_TWO')

    def test_tag_availability_excludes_own_handle(self):
        url = reverse('handle_available')
        self.assertTrue(self.client.get(url, {'handle':'@FOUNDER_ONE'}).json()['available'])
        self.assertFalse(self.client.get(url, {'handle':'@FOUNDER_TWO'}).json()['available'])
        self.assertFalse(self.client.get(url, {'handle':'invalid handle'}).json()['available'])
        self.assertTrue(self.client.get(url, {'handle':'available_handle'}).json()['available'])

    def test_avatar_upload_is_resized_reencoded_and_metadata_removed(self):
        response = self.client.post(self.url, self.data(avatar_upload=image_upload()))
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.avatar.name.startswith(f'avatars/{self.owner.pk}/'))
        with self.owner.avatar.open('rb') as stored, Image.open(stored) as image:
            self.assertEqual(image.size, (512,512))
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(dict(image.getexif()), {})
        avatar_response = self.client.get(self.owner.avatar_url)
        self.assertEqual(avatar_response['Content-Type'], 'image/jpeg')
        self.assertEqual(avatar_response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(avatar_response['Cache-Control'], 'private, no-cache')
        avatar_response.close()
        self.client.logout()
        self.assertEqual(self.client.get(reverse('user_avatar', args=[self.owner.pk])).status_code, 302)

    def test_avatar_replace_and_remove_cleanup_after_commit(self):
        self.client.post(self.url, self.data(avatar_upload=image_upload()))
        self.owner.refresh_from_db(); first = Path(self.owner.avatar.path)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.data(avatar_upload=image_upload('second.png')))
        self.owner.refresh_from_db(); second = Path(self.owner.avatar.path)
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.data(remove_avatar='on'))
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.avatar); self.assertFalse(second.exists())

    def test_bad_avatar_does_not_change_profile(self):
        invalid = SimpleUploadedFile('avatar.png', b'<svg><script>alert(1)</script></svg>', content_type='image/png')
        response = self.client.post(self.url, self.data(display_name='should not save', avatar_upload=invalid))
        self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db(); self.assertEqual(self.owner.display_name, '')
        oversized = SimpleUploadedFile('large.jpg', b'X'*(5*1024*1024+1))
        self.assertEqual(self.client.post(self.url, self.data(avatar_upload=oversized)).status_code, 400)
        self.assertEqual(self.client.post(self.url, self.data(avatar_upload=image_upload(), remove_avatar='on')).status_code, 400)
        self.assertEqual(list(Path(self.temp.name).rglob('*.jpg')), [])

    def test_excessive_dimensions_rejected_before_decoding(self):
        output = BytesIO(); Image.new('1', (4097,4097)).save(output, 'PNG')
        upload = SimpleUploadedFile('huge.png', output.getvalue())
        self.assertEqual(self.client.post(self.url, self.data(avatar_upload=upload)).status_code, 400)

    def test_handle_race_cleans_new_file_and_preserves_previous_profile(self):
        form = UserProfileForm(data=self.data(handle='available'), files={'avatar_upload':image_upload()}, instance=self.owner)
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.filter(pk=self.other.pk).update(handle='available')
        with self.assertRaises(IntegrityError):
            save_user_profile(self.owner.pk, form.cleaned_data)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.handle, 'founder_one')
        self.assertFalse(self.owner.avatar)
        self.assertEqual(list(Path(self.temp.name).rglob('*.jpg')), [])

    def test_profile_only_exposes_published_projects_and_public_fields(self):
        project = StartupProfile.objects.create(owner=self.owner, name='Secret startup', one_line_pitch='PRIVATE IDEA')
        card = get_card(project)
        self.client.force_login(self.other)
        page = self.client.get(self.owner.get_absolute_url())
        self.assertNotContains(page, 'Secret startup'); self.assertNotContains(page, 'PRIVATE IDEA')
        self.assertNotContains(page, 'private@example.test')
        save_card(card, {**card_values(card), 'name':'Public startup', 'summary':'Published summary'}, card.revision, publish=True)
        page = self.client.get(self.owner.get_absolute_url())
        self.assertContains(page, 'Public startup'); self.assertNotContains(page, 'Secret startup')
        self.assertNotContains(page, 'private@example.test')

    def test_can_message_profile_without_a_project_and_pair_is_reused(self):
        url = reverse('profile_message', args=[self.other.pk])
        for _ in range(2): self.assertEqual(self.client.post(url).status_code, 302)
        thread = DirectConversation.objects.get()
        self.assertIsNone(thread.source_card)
        self.assertFalse(DirectMessage.objects.exists())
        self.assertEqual(self.client.post(reverse('profile_message', args=[self.owner.pk])).status_code, 403)
        UserBlock.objects.create(user=self.other, blocked=self.owner)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        User.objects.filter(pk=self.other.pk).update(is_active=False)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_member_search_and_inactive_profiles(self):
        User.objects.filter(pk=self.other.pk).update(display_name='Мария', occupation='Дизайнер')
        for query in ('@founder_two','Мария','Дизайнер'):
            response = self.client.get(reverse('member_directory'), {'q':query})
            self.assertContains(response, '@founder_two')
            self.assertNotContains(response, 'hidden@example.test')
        User.objects.filter(pk=self.other.pk).update(is_active=False)
        self.assertNotContains(self.client.get(reverse('member_directory')), '@founder_two')
        self.assertEqual(self.client.get(self.other.get_absolute_url()).status_code, 404)

    def test_profile_escapes_html_and_rejects_unsafe_links(self):
        self.assertEqual(self.client.post(self.url, self.data(profile_website='javascript:alert(1)')).status_code, 400)
        self.client.post(self.url, self.data(bio='<script>alert(1)</script>'))
        response = self.client.get(self.owner.get_absolute_url())
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_profile_endpoints_require_login(self):
        self.client.logout()
        for url in (self.url, self.owner.get_absolute_url(), reverse('my_profile'), reverse('member_directory'), reverse('handle_available')):
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_registration_can_choose_tag_or_get_login_suggestion(self):
        self.client.logout()
        payload={'username':'newfounder', 'email':'new@example.test', 'password1':'StrongPass987!', 'password2':'StrongPass987!'}
        self.assertEqual(self.client.post(reverse('register'), payload).status_code, 302)
        self.assertEqual(User.objects.get(username='newfounder').handle, 'newfounder')
        self.client.logout()
        payload.update(username='different_login',email='next@example.test',handle='@Nice_Tag')
        self.assertEqual(self.client.post(reverse('register'),payload).status_code,302)
        self.assertEqual(User.objects.get(username='different_login').handle,'nice_tag')
