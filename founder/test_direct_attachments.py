from datetime import timedelta
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from founder.models import DirectAttachment, DirectConversation, DirectMessage, User, UserBlock
from founder.services.direct_attachments import clean_name
from founder.services.messaging import history, send_message


def photo(name='photo.jpg', size=(1600, 1200), image_format='JPEG', gps=True):
    image = Image.new('RGB', size, (30, 120, 110))
    output = BytesIO()
    kwargs = {}
    if gps:
        exif = Image.Exif()
        exif[0x010F] = 'Secret Camera'      # Make
        exif[0x8825] = {2: (55.0, 45.0, 0.0)}  # GPS latitude
        kwargs['exif'] = exif
    image.save(output, format=image_format, **kwargs)
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/jpeg')


class AttachmentTestMixin:
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.alice = User.objects.create_user('file_alice', email='file_alice@example.test', password='pass-12345')
        self.bob = User.objects.create_user('file_bob', email='file_bob@example.test', password='pass-12345')
        self.eve = User.objects.create_user('file_eve', email='file_eve@example.test', password='pass-12345')
        self.thread = DirectConversation.objects.create(user_low=self.alice, user_high=self.bob)
        self.upload_url = reverse('direct_attachment_upload', args=[self.thread.pk])

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def upload(self, user, file):
        client = Client()
        client.force_login(user)
        return client.post(self.upload_url, {'file': file}, HTTP_ACCEPT='application/json')


class DirectAttachmentTests(AttachmentTestMixin, TestCase):
    def test_photo_is_reencoded_without_metadata_and_gets_preview(self):
        response = self.upload(self.alice, photo())
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual((data['kind'], data['width'], data['height']), ('image', 1600, 1200))
        self.assertTrue(data['preview_url'])
        attachment = DirectAttachment.objects.get(pk=data['id'])
        with Image.open(attachment.file.path) as saved:
            self.assertEqual(dict(saved.getexif()), {})
        with Image.open(attachment.preview.path) as preview:
            self.assertLessEqual(max(preview.size), 960)
        self.assertNotIn('photo', attachment.file.name)  # Disk names never reuse the sender's name.

    def test_documents_are_always_downloaded(self):
        data = self.upload(self.alice, SimpleUploadedFile('План <v2>.pdf', b'%PDF-1.4 test', 'application/pdf')).json()
        self.assertEqual(data['kind'], 'file')
        self.assertIsNone(data['preview_url'])
        message, _ = send_message(self.alice.pk, self.thread.pk, uuid4(), '', [data['id']])
        self.client.force_login(self.bob)
        response = self.client.get(data['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 test')

    def test_rejects_bad_types_broken_photos_and_empty_files(self):
        for file in (SimpleUploadedFile('virus.exe', b'MZ...'), SimpleUploadedFile('page.html', b'<script>'),
                     SimpleUploadedFile('broken.jpg', b'not an image'), SimpleUploadedFile('empty.pdf', b'')):
            response = self.upload(self.alice, file)
            self.assertEqual(response.status_code, 400, file.name)
            self.assertIn('error', response.json())
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self.upload_url, {}, HTTP_ACCEPT='application/json').status_code, 400)
        self.assertFalse(DirectAttachment.objects.exists())

    @override_settings(MAX_UPLOAD_BYTES=1000)
    def test_large_upload_is_stopped(self):
        response = self.upload(self.alice, SimpleUploadedFile('big.pdf', b'x' * 5000))
        self.assertEqual(response.status_code, 413)
        self.assertFalse(DirectAttachment.objects.exists())

    def test_only_participants_and_not_when_blocked(self):
        self.assertEqual(self.upload(self.eve, photo()).status_code, 404)
        UserBlock.objects.create(user=self.bob, blocked=self.alice)
        self.assertEqual(self.upload(self.alice, photo()).status_code, 403)
        anonymous = Client().post(self.upload_url, {'file': photo()})
        self.assertEqual(anonymous.status_code, 302)

    def test_unsent_file_is_private_until_message_is_sent(self):
        data = self.upload(self.alice, photo()).json()
        bob, eve = Client(), Client()
        bob.force_login(self.bob)
        eve.force_login(self.eve)
        self.assertEqual(bob.get(data['url']).status_code, 404)
        send_message(self.alice.pk, self.thread.pk, uuid4(), 'Смотри', [data['id']])
        response = bob.get(data['preview_url'])
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'image/webp'))
        inline = bob.get(data['url'])
        self.assertEqual(inline['Content-Type'], 'image/jpeg')
        self.assertIn('sandbox', inline['Content-Security-Policy'])
        self.assertTrue(bob.get(data['url'] + '?download=1')['Content-Disposition'].startswith('attachment'))
        self.assertEqual(eve.get(data['url']).status_code, 404)

    def test_send_links_files_and_rejects_reuse_foreign_and_too_many(self):
        first = self.upload(self.alice, photo()).json()['id']
        second = self.upload(self.alice, SimpleUploadedFile('notes.txt', b'hello')).json()['id']
        client_id = uuid4()
        message, created = send_message(self.alice.pk, self.thread.pk, client_id, '', [first, second])
        self.assertTrue(created)
        self.assertEqual(message.content, '')
        self.assertEqual(set(message.attachments.values_list('pk', flat=True)),
                         {DirectAttachment.objects.get(pk=first).pk, DirectAttachment.objects.get(pk=second).pk})
        again, created = send_message(self.alice.pk, self.thread.pk, client_id, '', [first, second])
        self.assertEqual((again.pk, created), (message.pk, False))  # Safe socket retry.
        with self.assertRaises(ValidationError):
            send_message(self.alice.pk, self.thread.pk, uuid4(), 'ещё раз', [first])
        bobs = self.upload(self.bob, photo()).json()['id']
        with self.assertRaises(ValidationError):
            send_message(self.alice.pk, self.thread.pk, uuid4(), 'чужой файл', [bobs])
        with self.assertRaises(ValidationError):
            send_message(self.alice.pk, self.thread.pk, uuid4(), 'много', [str(uuid4()) for _ in range(11)])
        with self.assertRaises(ValidationError):
            send_message(self.alice.pk, self.thread.pk, uuid4(), '   ', [])
        result = history(self.bob.pk, self.thread.pk)
        names = [item['name'] for item in result['messages'][-1]['attachments']]
        self.assertEqual(names, ['photo.jpg', 'notes.txt'])

    def test_remove_unsent_file_deletes_it_from_disk(self):
        data = self.upload(self.alice, photo()).json()
        attachment = DirectAttachment.objects.get(pk=data['id'])
        paths = [Path(attachment.file.path), Path(attachment.preview.path)]
        self.assertTrue(all(path.exists() for path in paths))
        self.client.force_login(self.bob)
        delete_url = reverse('direct_attachment_delete', args=[data['id']])
        self.assertEqual(self.client.post(delete_url).status_code, 404)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(delete_url).status_code, 204)
        self.assertFalse(any(path.exists() for path in paths))
        sent = self.upload(self.alice, photo()).json()['id']
        send_message(self.alice.pk, self.thread.pk, uuid4(), '', [sent])
        self.assertEqual(self.client.post(reverse('direct_attachment_delete', args=[sent])).status_code, 404)

    def test_stale_unsent_files_are_cleaned_and_pending_is_capped(self):
        old = self.upload(self.alice, SimpleUploadedFile('old.txt', b'old')).json()['id']
        DirectAttachment.objects.filter(pk=old).update(created_at=timezone.now() - timedelta(days=2))
        self.upload(self.alice, SimpleUploadedFile('new.txt', b'new'))
        self.assertFalse(DirectAttachment.objects.filter(pk=old).exists())
        for index in range(19):
            self.upload(self.alice, SimpleUploadedFile(f'{index}.txt', b'x'))
        response = self.upload(self.alice, SimpleUploadedFile('extra.txt', b'x'))
        self.assertEqual(response.status_code, 400)

    def test_inbox_shows_attachment_only_message_and_composer(self):
        data = self.upload(self.alice, photo()).json()
        send_message(self.alice.pk, self.thread.pk, uuid4(), '', [data['id']])
        self.client.force_login(self.bob)
        page = self.client.get(reverse('conversation', args=[self.thread.pk]))
        self.assertContains(page, '📎 Вложение')
        self.assertContains(page, 'data-attach-button')
        self.assertContains(page, f'data-upload-url="{self.upload_url}"')

    def test_clean_name(self):
        self.assertEqual(clean_name('../../etc/passwd'), 'passwd')
        self.assertEqual(clean_name('C:\\Users\\me\\отчёт.pdf'), 'отчёт.pdf')
        self.assertEqual(clean_name('bad\x00name\u202e.pdf'), 'badname.pdf')
        self.assertEqual(clean_name('...'), 'file')
        self.assertLessEqual(len(clean_name('a' * 400 + '.docx')), 255)


class DirectAttachmentSocketTests(AttachmentTestMixin, TransactionTestCase):
    def test_message_with_files_is_delivered_over_socket(self):
        data = self.upload(self.alice, photo()).json()
        client = Client()
        client.force_login(self.alice)
        cookie = f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'.encode()

        async def run():
            from config.asgi import application
            socket = WebsocketCommunicator(application, '/ws/messages/',
                                           headers=[(b'origin', b'http://localhost'), (b'cookie', cookie)])
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await socket.receive_json_from())['type'], 'ready')
            await socket.send_json_to({'type': 'send', 'conversation': str(self.thread.pk),
                                       'client_id': str(uuid4()), 'content': '', 'attachments': [data['id']]})
            events = [await socket.receive_json_from(), await socket.receive_json_from()]
            ack = next(event for event in events if event['type'] == 'ack')
            self.assertEqual(ack['message']['attachments'][0]['id'], data['id'])
            self.assertEqual(ack['message']['attachments'][0]['kind'], 'image')
            await socket.send_json_to({'type': 'send', 'conversation': str(self.thread.pk),
                                       'client_id': str(uuid4()), 'content': 'x', 'attachments': 'nope'})
            self.assertEqual((await socket.receive_json_from())['type'], 'error')
            await socket.disconnect()
        async_to_sync(run)()
        self.assertEqual(DirectMessage.objects.get().attachments.count(), 1)
