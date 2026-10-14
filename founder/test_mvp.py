from decimal import Decimal
import json
from unittest.mock import patch
from django.db import IntegrityError,transaction
from django.test import TestCase,override_settings
from django.urls import reverse
from founder.models import User,StartupProfile,ProjectMember,FeatureCandidate,KanoResponse,BrunoTask,EvidenceEntry
from founder.services.mvp import rice_score,ice_score,kano_category,kano_summary,board,transfer_features
from founder.services.feature_ai import suggest_features
from founder.services.workbench import generate_tasks
from founder.services.ai import AIResponseFormatError

class MVPTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='mvp-owner',email='mvp-owner@example.test')
        self.viewer=User.objects.create_user(username='mvp-viewer',email='mvp-viewer@example.test')
        self.other=User.objects.create_user(username='mvp-other',email='mvp-other@example.test')
        self.startup=StartupProfile.objects.create(owner=self.owner,name='Мой MVP')
        ProjectMember.objects.create(startup=self.startup,user=self.viewer,role='viewer',status='active')
        self.client.force_login(self.owner)
    def feature(self,title='Запись на время',**kwargs):
        return FeatureCandidate.objects.create(startup=self.startup,title=title,**kwargs)
    def test_rice_and_ice_exact_and_zero_not_missing(self):
        feature=self.feature(reach=100,rice_impact=Decimal(2),confidence=Decimal(80),effort=Decimal(4),ice_impact=8,ease=6)
        feature.refresh_from_db()
        self.assertEqual(rice_score(feature),Decimal(40))
        self.assertEqual(ice_score(feature),Decimal(384))
        feature.confidence=Decimal(0)
        self.assertEqual(rice_score(feature),0)
        feature.confidence=None
        self.assertIsNone(rice_score(feature))
        self.assertIsNone(ice_score(feature))
        feature.confidence=80
        feature.effort=4
        self.assertEqual(rice_score(feature),40)
        self.assertEqual(ice_score(feature),384)
        feature.effort=Decimal(0)
        self.assertIsNone(rice_score(feature))
    def test_all_25_kano_pairs(self):
        expected=['QAAAO','RIIIM','RIIIM','RIIIM','RRRRQ']
        for i in range(1,6):
            for j in range(1,6):self.assertEqual(kano_category(i,j),expected[i-1][j-1])
        for invalid in (0,6,-1,True,'1'):
            with self.assertRaises(ValueError):kano_category(invalid,1)

    def test_saved_rice_impact_is_selected_when_reopening_editor(self):
        feature=self.feature()
        url=reverse('feature_edit',args=[self.startup.pk,feature.pk])
        labels={'0.25':'0,25 — минимальное','0.5':'0,5 — небольшое',
                '1':'1 — среднее','2':'2 — сильное','3':'3 — очень сильное'}
        for value,label in labels.items():
            with self.subTest(value=value):
                feature.rice_impact=Decimal(value)
                feature.save()
                page=self.client.get(url)
                self.assertContains(page,f'<option value="{value}" selected>{label}</option>',html=True)
                selected=page.context['form']['rice_impact'].value()
                self.assertEqual(self.client.post(url,{'title':feature.title,'rice_impact':selected}).status_code,302)
                feature.refresh_from_db()
                self.assertEqual(feature.rice_impact,Decimal(value))
    def test_kano_ties_and_questionable_not_treated_as_must(self):
        f=self.feature()
        KanoResponse.objects.create(feature=f,respondent='one',present=2,absent=5)
        KanoResponse.objects.create(feature=f,respondent='two',present=1,absent=3)
        KanoResponse.objects.create(feature=f,respondent='three',present=1,absent=1)
        summary=kano_summary(list(f.kano_responses.all()))
        self.assertEqual((summary['category'],summary['valid'],summary['questionable']),('mixed',2,1))
        self.assertFalse(board(self.startup)[0].in_mvp)
    def test_moscow_manual_override_ties_and_method_selection(self):
        for name,value in [('one',100),('two',100),('three',20),('four',1)]:
            self.feature(name,reach=value,rice_impact=1,confidence=100,effort=1,ice_impact=10 if name=='four' else 1,ease=5)
        rows=board(self.startup)
        self.assertEqual(rows[0].effective_priority,rows[1].effective_priority)
        self.assertEqual(rows[0].effective_priority,'must')
        self.assertEqual(rows[-1].effective_priority,'wont')
        rows[0].priority='could';rows[0].save()
        self.assertEqual(board(self.startup)[0].effective_priority,'could')
        self.assertEqual(board(self.startup,'ice')[0].title,'four')
    def test_kano_must_included_and_explicit_wont_excluded(self):
        f=self.feature(priority='could')
        KanoResponse.objects.create(feature=f,respondent='one',present=2,absent=5)
        self.assertTrue(board(self.startup)[0].in_mvp)
        f.priority='wont';f.save()
        self.assertFalse(board(self.startup)[0].in_mvp)
    def test_transfer_multiple_functions_is_idempotent_and_preserves_normal_tasks(self):
        normal=BrunoTask.objects.create(startup=self.startup,axis='product',title='Проверка',instructions='Поговорить',success_criterion='Ответ')
        a=self.feature('Каталог',priority='must');b=self.feature('Запись',priority='must')
        self.assertEqual(transfer_features(self.startup,[a.pk,b.pk]),2)
        self.assertEqual(transfer_features(self.startup,[a.pk,b.pk]),0)
        self.assertEqual(self.startup.bruno_tasks.filter(origin='mvp').count(),2)
        self.assertTrue(self.startup.bruno_tasks.filter(pk=normal.pk).exists())
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():BrunoTask.objects.create(startup=self.startup,axis='product',title='Другой',instructions='Проверить',success_criterion='Есть')
    def test_transfer_rejects_other_project_and_non_mvp_atomically(self):
        a=self.feature('one',priority='must');b=self.feature('two',priority='wont')
        with self.assertRaises(ValueError):transfer_features(self.startup,[a.pk,b.pk])
        self.assertFalse(self.startup.bruno_tasks.exists())
        foreign=StartupProfile.objects.create(owner=self.other,name='Чужой')
        f=FeatureCandidate.objects.create(startup=foreign,title='Чужая',priority='must')
        response=self.client.post(reverse('features_transfer',args=[self.startup.pk]),{'features':[str(f.pk)]})
        self.assertEqual(response.status_code,302)
        self.assertFalse(self.startup.bruno_tasks.exists())
    def test_create_scores_validation_and_optional_estimates(self):
        url=reverse('feature_create',args=[self.startup.pk])
        self.assertEqual(self.client.post(url,{'title':'Первая функция'}).status_code,302)
        f=self.startup.feature_candidates.get()
        self.assertIsNone(f.reach)
        self.assertEqual(self.client.post(url,{'title':'Плохие оценки','effort':'0','confidence':'101','rice_impact':'8','ease':'11'}).status_code,400)
        self.assertEqual(self.startup.feature_candidates.count(),1)
        self.assertContains(self.client.get(reverse('tasks',args=[self.startup.pk])),'Функции MVP')
        self.assertContains(self.client.get(reverse('feature_edit',args=[self.startup.pk,f.pk])),'Первая функция')
    def test_viewer_can_read_but_not_mutate_and_foreign_ids_are_404(self):
        f=self.feature(priority='must')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('tasks',args=[self.startup.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('feature_kano',args=[self.startup.pk,f.pk])).status_code,200)
        for name,args in [('feature_create',[self.startup.pk]),('features_suggest',[self.startup.pk]),('feature_edit',[self.startup.pk,f.pk]),('features_transfer',[self.startup.pk]),('feature_survey_prepare',[self.startup.pk,f.pk]),('feature_kano',[self.startup.pk,f.pk])]:
            self.assertEqual(self.client.post(reverse(name,args=args),{}).status_code,404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('tasks',args=[self.startup.pk])).status_code,404)
    def test_survey_answer_evidence_correction_dedup_and_snapshot(self):
        f=self.feature()
        self.client.post(reverse('feature_survey_prepare',args=[self.startup.pk,f.pk]),{})
        f.refresh_from_db();self.assertIsNotNone(f.kano_task_id)
        url=reverse('feature_kano',args=[self.startup.pk,f.pk])
        data={'respondent':'R-01','present':2,'absent':5,'observed_on':'2026-01-01','source':'Личное интервью','notes':'Нужно для работы'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        response=f.kano_responses.get()
        self.assertTrue(response.question_present)
        self.assertEqual(f.kano_task.status,'todo')
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertEqual(EvidenceEntry.objects.filter(startup=self.startup).count(),1)
        response_url=reverse('feature_kano_edit',args=[self.startup.pk,f.pk,response.pk])
        self.assertEqual(self.client.post(response_url,{**data,'present':1,'absent':3}).status_code,302)
        f.refresh_from_db()
        self.assertIn('Привлекательная',f.kano_evidence.observation)
        self.assertEqual(f.kano_responses.count(),1)
        self.assertEqual(EvidenceEntry.objects.filter(startup=self.startup).count(),1)
        changed=self.client.post(reverse('feature_edit',args=[self.startup.pk,f.pk]),{'title':f.title,'question_present':'Новый вопрос','question_absent':'Другой'})
        self.assertEqual(changed.status_code,302)
        f.refresh_from_db();self.assertEqual(f.question_present,response.question_present)
    @override_settings(AI_PROVIDER='gigachat')
    def test_bruno_cannot_invent_numeric_estimates_or_respondents(self):
        item={'title':'Каталог','description':'Найти инструмент','question_present':'Как вам наличие каталога?','question_absent':'Как вам отсутствие каталога?'}
        with patch('founder.services.feature_ai.complete_text',return_value=json.dumps({'features':[item]})):
            response=self.client.post(reverse('features_suggest',args=[self.startup.pk]),{})
        self.assertEqual(response.status_code,302)
        f=self.startup.feature_candidates.get()
        self.assertEqual(f.source,'bruno');self.assertIsNone(f.reach);self.assertIsNone(f.confidence)
        with patch('founder.services.feature_ai.complete_text',return_value=json.dumps({'features':[{**item,'reach':500}]})):
            with self.assertRaises(AIResponseFormatError):suggest_features(self.startup)
        self.assertFalse(KanoResponse.objects.exists())

    @override_settings(AI_PROVIDER='gigachat')
    def test_ai_survey_creates_one_task_and_stops_changing_questions_after_answers(self):
        f=self.feature()
        url=reverse('feature_survey_prepare',args=[self.startup.pk,f.pk])
        pair={'question_present':'Как вы отнесётесь к выбору времени?',
              'question_absent':'Как вы отнесётесь к отсутствию выбора времени?'}
        with patch('founder.services.feature_ai.complete_text',return_value=json.dumps(pair)) as ai:
            self.client.post(url,{'with_ai':'1'})
            self.client.post(url,{})
            self.assertEqual(ai.call_count,1)
        f.refresh_from_db()
        self.assertIn(pair['question_present'],f.kano_task.instructions)
        self.assertEqual(self.startup.bruno_tasks.filter(origin='kano').count(),1)
        self.assertFalse(f.kano_responses.exists())
        self.client.post(reverse('feature_kano',args=[self.startup.pk,f.pk]),{
            'respondent':'01','present':3,'absent':5,'observed_on':'2026-01-01','source':'Интервью'})
        with patch('founder.services.feature_ai.complete_text') as ai:
            self.client.post(url,{'with_ai':'1'})
            ai.assert_not_called()
        f.refresh_from_db()
        self.assertEqual(f.question_present,pair['question_present'])

    @override_settings(AI_PROVIDER='demo')
    def test_mvp_tasks_do_not_block_existing_bruno_tasks_or_assignment(self):
        f=self.feature(priority='must')
        transfer_features(self.startup,[f.pk])
        self.assertEqual(len(generate_tasks(self.startup)),3)
        f.refresh_from_db()
        self.client.post(reverse('task_assign',args=[self.startup.pk,f.task_id]),{'assignee':self.owner.pk})
        f.task.refresh_from_db()
        self.assertEqual(f.task.assignee_id,self.owner.pk)

    def test_invalid_research_and_cross_project_response_do_not_write_evidence(self):
        f=self.feature()
        url=reverse('feature_kano',args=[self.startup.pk,f.pk])
        self.assertEqual(self.client.post(url,{'respondent':'01','present':6,'absent':5,
            'observed_on':'2099-01-01','source':''}).status_code,400)
        self.assertFalse(EvidenceEntry.objects.exists())
        foreign=StartupProfile.objects.create(owner=self.other,name='Чужой')
        other_feature=FeatureCandidate.objects.create(startup=foreign,title='Другое')
        answer=KanoResponse.objects.create(feature=other_feature,respondent='01',present=1,absent=3)
        self.assertEqual(self.client.post(reverse('feature_kano_edit',args=[self.startup.pk,f.pk,answer.pk]),{}).status_code,404)
