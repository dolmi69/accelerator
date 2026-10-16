"""Visible assignment, exact member filters, canonical MVP tasks and role changes."""
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from founder.models import BrunoTask, FeatureCandidate, ProjectMember, StartupProfile, User


@override_settings(AI_PROVIDER='demo', KANO_SURVEYS_ENABLED=False)
class TaskAssignmentTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('assign-owner', email='assign-owner@example.test')
        self.mate = User.objects.create_user('assign-mate', email='assign-mate@example.test', display_name='Марина')
        self.viewer = User.objects.create_user('assign-viewer', email='assign-viewer@example.test')
        self.pending = User.objects.create_user('assign-pending', email='assign-pending@example.test')
        self.stranger = User.objects.create_user('assign-stranger', email='assign-stranger@example.test')
        self.project = StartupProfile.objects.create(owner=self.owner, name='Team work')
        self.member = ProjectMember.objects.create(startup=self.project, user=self.mate, role='editor', status='active')
        ProjectMember.objects.create(startup=self.project, user=self.viewer, role='viewer', status='active')
        ProjectMember.objects.create(startup=self.project, user=self.pending, role='editor', status='invited')
        self.task = BrunoTask.objects.create(startup=self.project, axis='market', title='Провести интервью')
        self.mine = BrunoTask.objects.create(startup=self.project, axis='finance', title='Экономика', assignee=self.owner)
        self.feature = FeatureCandidate.objects.create(startup=self.project, title='Каталог', reach=90,
            rice_impact=2, confidence=80, effort=3, ice_impact=8, ease=7, priority='could')
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(name, args=[self.project.pk, *args])

    def entries(self, **params):
        return self.client.get(self.url('tasks'), params).context['priority_entries']

    def test_assignment_is_visible_and_can_be_changed_or_cleared(self):
        page = self.client.get(self.url('tasks'))
        self.assertContains(page, 'Назначить исполнителя')
        self.assertContains(page, self.url('task_assign', self.task.pk), count=1)
        url = self.url('task_assign', self.task.pk)
        result = self.client.post(url, {'assignee':self.mate.pk, 'method':'ice', 'work_filter':'unassigned'})
        self.assertEqual(result.url, self.url('tasks') + f'?method=ice&assignee=unassigned#priority-task-{self.task.pk}')
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee, self.mate)
        self.client.post(url, {'assignee':self.owner.pk})
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee, self.owner)
        self.client.post(url, {'assignee':''})
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee)

    def test_unstarted_feature_becomes_one_task_preserving_estimates_and_moscow(self):
        before = self.entries()
        feature_score = next(row['score'] for row in before if row['feature'] == self.feature)
        url = self.url('feature_assign', self.feature.pk)
        self.client.post(url, {'assignee':self.mate.pk})
        self.client.post(url, {'assignee':self.mate.pk})
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.task.assignee_id, self.mate.pk)
        self.assertEqual(self.project.bruno_tasks.filter(origin='mvp').count(), 1)
        self.assertEqual((self.feature.priority, self.feature.reach), ('could', 90))
        entries = [row for row in self.entries() if row['feature'] == self.feature]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['score'], feature_score)
        self.assertTrue(entries[0]['lab_url'])
        self.assertEqual(entries[0]['task'].pk, self.feature.task_id)

    def test_filters_do_not_include_unassigned_features_in_mine_or_other_people(self):
        self.client.post(self.url('task_assign', self.task.pk), {'assignee':self.mate.pk})
        for selected in ('all', 'mine', 'unassigned', str(self.mate.pk)):
            page = self.client.get(self.url('tasks'), {'assignee': selected})
            self.assertEqual({option['value']: option['count'] for option in page.context['task_filter_options']},
                             {'all': 3, 'mine': 1, 'unassigned': 1})
        self.assertEqual([row['task'] for row in self.entries(mine=1)], [self.mine])
        self.assertEqual([row['task'] for row in self.entries(assignee=str(self.mate.pk))], [self.task])
        unassigned = self.entries(assignee='unassigned')
        self.assertEqual(len(unassigned), 1)
        self.assertEqual(unassigned[0]['feature'], self.feature)
        page = self.client.get(self.url('tasks'), {'assignee':self.mate.pk, 'method':'ice'})
        self.assertEqual(page.context['rice_filter_query'], f'method=rice&assignee={self.mate.pk}')
        self.client.force_login(self.mate)
        self.assertEqual([row['task'] for row in self.entries(mine=1)], [self.task])

    def test_foreign_viewer_pending_completed_and_csrf_are_rejected(self):
        url = self.url('task_assign', self.task.pk)
        for person in (self.viewer, self.pending, self.stranger):
            self.assertEqual(self.client.post(url, {'assignee':person.pk}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 405)
        foreign = StartupProfile.objects.create(owner=self.stranger, name='Private')
        task = BrunoTask.objects.create(startup=foreign, axis='market', title='Secret')
        feature = FeatureCandidate.objects.create(startup=foreign, title='Secret feature')
        self.assertEqual(self.client.post(self.url('task_assign', task.pk), {}).status_code, 404)
        self.assertEqual(self.client.post(self.url('feature_assign', feature.pk), {}).status_code, 404)
        self.assertEqual(self.client.get(self.url('tasks'), {'assignee':self.stranger.pk}).status_code, 404)
        self.task.status = 'done'; self.task.save()
        self.assertEqual(self.client.post(url, {'assignee':self.mate.pk}).status_code, 404)
        self.client.force_login(self.viewer)
        page = self.client.get(self.url('tasks'))
        self.assertNotContains(page, 'Кто возьмёт задачу')
        self.assertEqual(self.client.post(self.url('feature_assign', self.feature.pk), {'assignee':self.mate.pk}).status_code, 404)
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner)
        self.assertEqual(client.post(url, {'assignee':self.owner.pk}).status_code, 403)

    def test_demotion_releases_open_tasks_but_retains_finished_history(self):
        self.task.assignee = self.mate; self.task.save()
        done = BrunoTask.objects.create(startup=self.project, axis='market', title='Сделано', assignee=self.mate, status='done')
        self.client.post(self.url('team_member_update', self.member.pk), {'action':'role', 'role':'viewer'})
        self.task.refresh_from_db(); done.refresh_from_db()
        self.assertIsNone(self.task.assignee)
        self.assertEqual(done.assignee, self.mate)
        self.assertEqual(self.client.post(self.url('task_assign', self.task.pk), {'assignee':self.mate.pk}).status_code, 404)

    def test_team_workload_is_local_scoped_and_counts_hidden_kano_correctly(self):
        self.task.assignee = self.mate; self.task.save()
        BrunoTask.objects.create(startup=self.project, origin='kano', axis='product', title='Опрос', assignee=self.mate)
        BrunoTask.objects.create(startup=self.project, axis='team', title='Завершено', status='done', assignee=self.mate)
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('No AI')):
            page = self.client.get(self.url('team'))
        rows = page.context['workload_by_user']
        self.assertEqual((rows[self.mate.pk]['opened'], rows[self.mate.pk]['done']), (1, 1))
        self.assertEqual(rows[self.owner.pk]['opened'], 1)
        self.assertEqual(page.context['unassigned_count'], 1)
        self.assertEqual(rows[self.viewer.pk]['url'], '')
        self.assertContains(page, 'Марина')
        self.assertContains(page, f'assignee={self.mate.pk}')

    def test_clearing_unstarted_feature_does_not_create_task_or_call_ai(self):
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('No AI')):
            self.client.post(self.url('feature_assign', self.feature.pk), {'assignee':''})
        self.feature.refresh_from_db()
        self.assertIsNone(self.feature.task_id)
        self.assertEqual(self.project.bruno_tasks.count(), 2)
