"""Publication privacy, isolated telemetry and version-specific feedback."""
import json
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from founder.models import LabPublication, LabSiteVersion, LabTestEvent, LabTestSession, StartupProfile, User
from founder.services.lab_testing import laboratory_context, test_results
from founder.services.project_cards import get_card, save_card, card_values

HTML = '<!doctype html><html><head><title>Demo</title></head><body><button>Попробовать</button><input type="password"><form><input><button>Отправить</button></form></body></html>'


class LabCommunityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='lab-publisher', email='publisher@example.test')
        self.tester = User.objects.create_user(username='lab-tester', email='tester@example.test')
        self.other = User.objects.create_user(username='lab-stranger', email='stranger@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Prototype')
        self.version = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='PRIVATE PROMPT', model='Qwen')
        self.draft = LabSiteVersion.objects.create(startup=self.startup, html=HTML.replace('Demo', 'PRIVATE DRAFT'), prompt='draft', model='Qwen')
        self.card = get_card(self.startup)
        save_card(self.card, {**card_values(self.card), 'name': 'Prototype', 'summary': 'A public description'}, self.card.revision, publish=True)
        self.client.force_login(self.owner)
        self.client.post(self.url('lab_publish'), {'version': str(self.version.pk)})
        self.client.force_login(self.tester)

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def start(self):
        response = self.client.post(self.url('lab_test_start'), {'version': str(self.version.pk)})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def send(self, session, events):
        return self.client.post(session['events'], json.dumps({'events': events}), content_type='application/json')

    def test_card_exposes_only_selected_version_without_tracker(self):
        page = self.client.get(self.url('card_detail'))
        self.assertContains(page, 'Попробовать прототип')
        self.assertContains(page, self.url('lab_public_preview', self.version.pk))
        self.assertNotContains(page, 'PRIVATE PROMPT')
        response = self.client.get(self.url('lab_public_preview', self.version.pk))
        self.assertEqual(response.content.decode(), HTML)
        self.assertNotIn('bruno-lab', response.content.decode())
        self.assertEqual(self.client.get(self.url('lab_public_preview', self.draft.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url('lab_preview', self.draft.pk)).status_code, 404)
        self.assertFalse(LabTestSession.objects.exists())

    def test_only_owner_can_publish_and_version_must_belong_to_project(self):
        self.assertEqual(self.client.post(self.url('lab_publish'), {'version': str(self.draft.pk)}).status_code, 404)
        foreign = StartupProfile.objects.create(owner=self.other, name='private')
        version = LabSiteVersion.objects.create(startup=foreign, html=HTML, model='Qwen', prompt='secret')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url('lab_publish'), {'version': str(version.pk)}).status_code, 404)
        with self.assertRaises(ValidationError):
            LabPublication(startup=self.startup, version=version).full_clean()
        self.assertContains(self.client.get(self.url('card_edit')), 'Добавить в карточку')

    def test_capture_is_opt_in_and_sandboxed_without_field_values(self):
        page = self.client.get(self.url('lab_trial'))
        self.assertContains(page, 'Начать тест')
        self.assertFalse(LabTestSession.objects.exists())
        session = self.start()
        preview = self.client.get(session['preview'])
        self.assertContains(preview, 'bruno-lab')
        self.assertIn("sandbox allow-scripts", preview['Content-Security-Policy'])
        self.assertIn("connect-src 'none'", preview['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', preview['Content-Security-Policy'])
        self.assertEqual(preview['Cache-Control'], 'no-store')
        events = [{'sequence': 1, 'kind': 'ready'}, {'sequence': 2, 'kind': 'click', 'target': 'button:nth-of-type(1)', 'label': 'Попробовать'}, {'sequence': 3, 'kind': 'scroll', 'depth': 75}]
        self.assertEqual(self.send(session, events).status_code, 200)
        self.assertEqual(self.send(session, events).status_code, 200)
        self.assertEqual(LabTestEvent.objects.count(), 3)
        self.assertEqual(self.send(session, [{'sequence': 4, 'kind': 'click', 'value': 'SECRET'}]).status_code, 400)
        self.assertEqual(LabTestEvent.objects.count(), 3)

    def test_private_sessions_cannot_be_read_or_forged_by_other_accounts(self):
        session = self.start()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(session['preview']).status_code, 404)
        self.assertEqual(self.send(session, [{'sequence': 1, 'kind': 'ready'}]).status_code, 404)
        self.assertEqual(self.client.post(session['finish'], '{}', content_type='application/json').status_code, 404)
        self.assertEqual(self.client.get(self.url('lab')).status_code, 404)
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url('lab_trial')).status_code, 302)
        self.assertEqual(anonymous.post(session['events'], '{}', content_type='application/json').status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.tester)
        self.assertEqual(csrf.post(self.url('lab_test_start'), {'version': str(self.version.pk)}).status_code, 403)
        self.assertEqual(csrf.post(session['events'], '{}', content_type='application/json').status_code, 403)

    def test_finish_and_results_exclude_owner_and_feed_bruno(self):
        session = self.start()
        self.send(session, [{'sequence': 1, 'kind': 'click', 'target': 'button', 'label': 'Попробовать'}, {'sequence': 2, 'kind': 'form', 'target': 'form'}])
        body = json.dumps({'rating': 4, 'feedback': 'Кнопка понятна, но не хватает объяснения.', 'duration': 300})
        self.assertEqual(self.client.post(session['finish'], body, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.post(session['finish'], body, content_type='application/json').status_code, 200)
        stored = LabTestSession.objects.get(pk=session['session'])
        self.assertLess(stored.duration_seconds, 300)
        self.assertEqual(self.send(session, [{'sequence': 3, 'kind': 'ready'}]).status_code, 409)
        self.client.force_login(self.owner)
        own = self.start()
        self.send(own, [{'sequence': 1, 'kind': 'click'}])
        report = test_results(self.version)
        self.assertEqual((report['total'], report['testers'], report['completed'], report['clicks'], report['forms']), (1, 1, 1, 1, 1))
        self.assertEqual(report['average_rating'], 4)
        self.assertNotContains(self.client.get(self.url('lab') + f'?version={self.draft.pk}'), 'Кнопка понятна')
        page = self.client.get(self.url('lab') + f'?version={self.version.pk}')
        self.assertContains(page, 'Кнопка понятна')
        self.assertContains(self.client.get(self.url('lab')), f'?version={self.version.pk}&amp;section=globalization#lab-results')
        context, sources = laboratory_context(self.startup)
        self.assertIn('Кнопка понятна', context)
        self.assertIn('не заявки или продажи', context)
        self.assertNotIn(self.tester.email, context)
        self.assertIn(f'lab:{self.version.pk}', sources)
        from founder.services.workbench import evidence_context
        self.assertIn('Тестирование опубликованного прототипа', evidence_context(self.startup)[0])

    def test_switching_versions_or_withdrawing_card_revokes_live_testing(self):
        session = self.start()
        self.client.force_login(self.owner)
        self.client.post(self.url('lab_publish'), {'version': str(self.draft.pk)})
        self.client.force_login(self.tester)
        self.assertEqual(self.client.get(session['preview']).status_code, 404)
        self.assertEqual(self.send(session, [{'sequence': 1, 'kind': 'click'}]).status_code, 404)
        self.assertEqual(self.client.post(self.url('lab_test_start'), {'version': str(self.version.pk)}).status_code, 409)
        self.client.force_login(self.owner)
        self.client.post(self.url('card_unpublish'))
        self.client.force_login(self.tester)
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 404)
        self.assertEqual(self.client.get(self.url('lab_public_preview', self.draft.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('lab_test_start'), {'version': str(self.draft.pk)}).status_code, 404)

    def test_events_are_bounded_and_timed_out_tests_cannot_add_more(self):
        session = self.start()
        invalids = [[], [{'sequence': 201, 'kind': 'click'}], [{'sequence': 1, 'kind': 'input'}], [{'sequence': 1, 'kind': 'click', 'label': 'x'*81}], [{'sequence': 1, 'kind': 'scroll', 'depth': 101}], [{'sequence': True, 'kind': 'ready'}]]
        for events in invalids:
            self.assertEqual(self.send(session, events).status_code, 400)
        self.assertFalse(LabTestEvent.objects.exists())
        LabTestSession.objects.filter(pk=session['session']).update(created_at=timezone.now()-timedelta(minutes=31))
        self.assertEqual(self.send(session, [{'sequence': 1, 'kind': 'click'}]).status_code, 409)
        self.assertEqual(self.client.get(session['preview']).status_code, 404)
        self.assertEqual(self.client.post(session['finish'], json.dumps({'rating': 5, 'feedback': '', 'duration': 1}), content_type='application/json').status_code, 200)

    def test_hiding_prototype_keeps_feedback_but_removes_public_access(self):
        session = self.start()
        self.client.force_login(self.owner)
        self.client.post(self.url('lab_publish'), {'action': 'hide'})
        self.assertTrue(LabTestSession.objects.filter(pk=session['session']).exists())
        self.client.force_login(self.tester)
        self.assertNotContains(self.client.get(self.url('card_detail')), 'Попробовать прототип')
        self.assertEqual(self.client.get(session['preview']).status_code, 404)

    def test_private_prototype_is_hidden_and_revokes_existing_tester_access(self):
        session = self.start()
        self.client.force_login(self.owner)
        response = self.client.post(self.url('lab_publish'), {'version': self.version.pk, 'visibility': 'private'})
        self.assertEqual(response.url, self.url('lab') + f'?version={self.version.pk}')
        self.assertEqual(LabPublication.objects.get(startup=self.startup).visibility, 'private')
        self.assertContains(self.client.get(self.url('card_detail')), self.url('lab_public_preview', self.version.pk))
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 200)
        self.client.force_login(self.tester)
        self.assertNotContains(self.client.get(self.url('card_detail')), 'Попробовать прототип')
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 404)
        self.assertEqual(self.client.get(self.url('lab_public_preview', self.version.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('lab_test_start'), {'version': self.version.pk}).status_code, 404)
        self.assertEqual(self.client.get(session['preview']).status_code, 404)
        self.assertEqual(self.send(session, [{'sequence': 1, 'kind': 'ready'}]).status_code, 404)
        self.assertEqual(self.client.post(session['finish'], '{}', content_type='application/json').status_code, 404)
        self.assertTrue(LabTestSession.objects.filter(pk=session['session']).exists())
        self.client.force_login(self.owner)
        self.client.post(self.url('lab_publish'), {'version': self.version.pk, 'visibility': 'public'})
        self.client.force_login(self.tester)
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 200)

    def test_private_attachment_works_on_draft_card_and_invalid_visibility_cannot_publish(self):
        self.client.force_login(self.owner)
        self.client.post(self.url('card_unpublish'))
        self.client.post(self.url('lab_publish'), {'version': self.draft.pk, 'visibility': 'private'})
        self.assertContains(self.client.get(self.url('card_edit')), self.url('lab_public_preview', self.draft.pk))
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 200)
        self.assertEqual(self.client.get(self.url('lab_public_preview', self.draft.pk)).status_code, 200)
        response = self.client.post(self.url('lab_publish'), {'version': self.version.pk, 'visibility': 'everyone-with-link'})
        self.assertEqual(response.status_code, 400)
        stored = LabPublication.objects.get(startup=self.startup)
        self.assertEqual((stored.version_id, stored.visibility), (self.draft.pk, 'private'))
        self.client.force_login(self.tester)
        self.assertEqual(self.client.get(self.url('lab_trial')).status_code, 404)
