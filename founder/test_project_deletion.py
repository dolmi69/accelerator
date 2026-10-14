"""Deletion is owner-only, confirmed, transactional and scoped to one project."""
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from founder.models import (BrunoTask, ChatAttachment, ChatMessage, ChatSession,
                            CoinTransaction, LabAIUsage, LabSiteVersion,
                            ProjectCard, ProjectMember, StartupProfile, TestBounty, User)
from founder.services.django_builder import delete_runtime
from founder.services.project_deletion import delete_project


@override_settings(AI_PROVIDER='demo')
class ProjectDeletionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('delete_owner', email='delete_owner@example.test', handle='delete_owner')
        self.mate = User.objects.create_user('delete_mate', email='delete_mate@example.test', handle='delete_mate')
        self.stranger = User.objects.create_user('delete_stranger', email='delete_stranger@example.test', handle='delete_stranger')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Тихое место')
        self.other = StartupProfile.objects.create(owner=self.owner, name='Соседний проект')
        self.member = ProjectMember.objects.create(startup=self.startup, user=self.mate, status='active')
        self.url = reverse('startup_delete', args=[self.startup.pk])
        self.team_url = reverse('team', args=[self.startup.pk])
        self.client.force_login(self.owner)

    def test_confirmation_and_get_never_delete(self):
        self.assertContains(self.client.get(self.team_url), self.url)
        self.assertContains(self.client.get(self.url), 'confirmation_name')
        for data in ({}, {'confirmation_name': 'Соседний проект'}):
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(StartupProfile.objects.filter(pk=self.startup.pk).exists())
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_members_invitees_and_strangers_cannot_delete(self):
        for user, role, status in [(self.mate, 'editor', 'active'), (self.mate, 'viewer', 'active'),
                                   (self.mate, 'editor', 'invited'), (self.stranger, None, None)]:
            if user == self.mate:
                self.member.role, self.member.status = role, status
                self.member.save()
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                response = method(self.url, {'confirmation_name': self.startup.name})
                self.assertEqual(response.status_code, 404)
            if status == 'active':
                self.assertNotContains(self.client.get(self.team_url), self.url)
        self.assertTrue(StartupProfile.objects.filter(pk=self.startup.pk).exists())

    def test_csrf_and_login_are_required(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(self.url, {'confirmation_name': self.startup.name}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {'confirmation_name': self.startup.name}).status_code, 302)
        self.assertTrue(StartupProfile.objects.filter(pk=self.startup.pk).exists())

    def test_confirmed_deletion_removes_project_data_and_files_only(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        message = ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, author=self.mate, content='Вклад')
        version = LabSiteVersion.objects.create(startup=self.startup, html='<html></html>', prompt='Тест')
        ProjectCard.objects.create(startup=self.startup, name='Тихое место', tagline='Место для работы')
        BrunoTask.objects.create(startup=self.startup, axis='market', title='Проверка рынка')
        usage = LabAIUsage.objects.create(startup=self.startup, version=version, user=self.owner,
                                         model='test', input_bound=1, output_limit=1,
                                         input_price=1, output_price=1, accounted_micro_rub=20)
        startup_id = self.startup.pk
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            attachment = ChatAttachment.objects.create(message=message, original_name='test.txt',
                size_bytes=5, content_type='text/plain', file=SimpleUploadedFile('test.txt', b'hello'))
            path = Path(attachment.file.path)
            self.assertTrue(path.exists())
            with patch('founder.services.project_deletion.delete_runtime') as cleanup:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(self.url, {'confirmation_name': self.startup.name})
                    self.assertTrue(path.exists(), 'Files must wait for transaction commit')
                cleanup.assert_called_once_with(startup_id, self.owner.pk)
            self.assertFalse(path.exists())
        self.assertRedirects(response, reverse('home'))
        self.assertFalse(StartupProfile.objects.filter(pk=startup_id).exists())
        for model in (ProjectMember, ChatSession, ChatMessage, ChatAttachment, LabSiteVersion, BrunoTask, ProjectCard):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertTrue(StartupProfile.objects.filter(pk=self.other.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.mate.pk).exists())
        usage.refresh_from_db()
        self.assertIsNone(usage.startup_id)
        self.assertIsNone(usage.version_id)
        self.assertEqual(usage.accounted_micro_rub, 20)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_unused_rewards_return_to_each_funder(self):
        self.owner.refresh_from_db()
        self.mate.refresh_from_db()
        original = {self.owner.pk: self.owner.coins, self.mate.pk: self.mate.coins}
        for user, remaining in ((self.owner, 25), (self.mate, 17)):
            TestBounty.objects.create(startup=self.startup, funder=user, reward=5, remaining=remaining)
        with patch('founder.services.project_deletion.delete_runtime'):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.url, {'confirmation_name': self.startup.name})
        for user, refund in ((self.owner, 25), (self.mate, 17)):
            user.refresh_from_db()
            self.assertEqual(user.coins, original[user.pk] + refund)
            record = CoinTransaction.objects.get(user=user, kind='refund')
            self.assertEqual(record.amount, refund)
            self.assertIsNone(record.startup_id)
        self.assertFalse(TestBounty.objects.exists())

    def test_refunds_and_deletion_roll_back_together(self):
        from founder.services.coins import close_bounty
        first = TestBounty.objects.create(startup=self.startup, funder=self.owner, reward=5, remaining=10)
        TestBounty.objects.create(startup=self.startup, funder=self.mate, reward=5, remaining=20)
        self.owner.refresh_from_db()
        coins = self.owner.coins
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Temporary database failure')
            return close_bounty(*args)

        with patch('founder.services.project_deletion.close_bounty', side_effect=fail_second), \
             patch('founder.services.project_deletion.delete_runtime') as cleanup:
            with self.assertRaises(RuntimeError), self.captureOnCommitCallbacks(execute=True):
                delete_project(self.startup)
            cleanup.assert_not_called()
        self.owner.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(self.owner.coins, coins)
        self.assertEqual(first.remaining, 10)
        self.assertIsNone(first.closed_at)
        self.assertTrue(StartupProfile.objects.filter(pk=self.startup.pk).exists())
        self.assertFalse(CoinTransaction.objects.filter(kind='refund').exists())

    def test_member_removal_preserves_contributions_and_releases_open_tasks(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        message = ChatMessage.objects.create(session=session, role='user', author=self.mate, content='Моя идея')
        todo = BrunoTask.objects.create(startup=self.startup, axis='market', title='Задача', assignee=self.mate)
        done = BrunoTask.objects.create(startup=self.startup, axis='market', title='Готово', assignee=self.mate, status='done')
        confirmation = reverse('team_member_remove', args=[self.startup.pk, self.member.pk])
        self.assertContains(self.client.get(self.team_url), confirmation)
        self.assertContains(self.client.get(confirmation), 'Удалить участника')
        self.assertTrue(ProjectMember.objects.filter(pk=self.member.pk).exists())
        self.client.force_login(self.mate)
        self.assertEqual(self.client.get(confirmation).status_code, 404)
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(reverse('team_member_update', args=[self.startup.pk, self.member.pk]),
                                             {'action': 'remove'}), self.team_url)
        todo.refresh_from_db()
        done.refresh_from_db()
        self.assertIsNone(todo.assignee_id)
        self.assertEqual(done.assignee_id, self.mate.pk)
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk, author=self.mate).exists())
        self.assertTrue(User.objects.filter(pk=self.mate.pk).exists())
        self.client.force_login(self.mate)
        self.assertEqual(self.client.get(self.team_url).status_code, 404)


class DeletedRuntimeTests(TestCase):
    def test_cleanup_is_scoped_and_clears_only_matching_active_selection(self):
        with TemporaryDirectory() as base, override_settings(BASE_DIR=Path(base)):
            root = Path(base) / '.runtime' / 'lab-backends'
            deleted, other = uuid4(), uuid4()
            directory = root / str(deleted)
            directory.mkdir(parents=True)
            (directory / 'db.sqlite3').write_bytes(b'private data')
            other_dir = root / str(other)
            other_dir.mkdir()
            active = root / '.active-12.json'
            other_active = root / '.active-13.json'
            state = {'project_id': str(deleted), 'token': 'test', 'revision': 1}
            active.write_text(json.dumps(state))
            other_active.write_text(json.dumps(state))
            with patch('founder.services.django_builder._stop') as stop:
                delete_runtime(deleted, 12)
            stop.assert_called_once_with(directory, deleted)
            self.assertFalse(directory.exists())
            self.assertFalse(active.exists())
            self.assertTrue(other_dir.exists())
            self.assertTrue(other_active.exists())

    def test_cleanup_keeps_selection_for_another_project(self):
        with TemporaryDirectory() as base, override_settings(BASE_DIR=Path(base)):
            root = Path(base) / '.runtime' / 'lab-backends'
            root.mkdir(parents=True)
            active = root / '.active-12.json'
            active.write_text(json.dumps({'project_id': str(uuid4()), 'token': 'test', 'revision': 1}))
            delete_runtime(uuid4(), 12)
            self.assertTrue(active.exists())
