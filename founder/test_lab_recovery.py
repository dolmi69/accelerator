"""Completed bad outputs are corrected within bounds; real failures stay honest."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from founder.services.backend_edits import edit_backend
from founder.services.lab_bruno import LabRequestRejected
from founder.services.lab_recovery import validation_scope
from founder.services.site_generator import generate_site
from founder.services.qwen import CodeResult, QwenError, QwenOutputError
from founder.services import lab_models
from founder.test_backend_edits import FILES, REPORT

HTML='<!doctype html><html><head><title>Demo</title><script>const stable = 1;</script></head><body><button>Выбрать</button></body></html>'


@override_settings(LAB_CODE_PROVIDER='ofox',LAB_MODEL_ROUTING=True,LAB_EDIT_REPAIR_ATTEMPTS=3,LAB_RAW_AI_TEST_MODE=True)
class RecoveryTests(SimpleTestCase):
    def setUp(self):
        for name in ['backend_edits','site_generator']:
            double=patch('founder.services.'+name+'.reject_output')
            double.start()
            self.addCleanup(double.stop)
        self.source=SimpleNamespace(kind='generated',backend_files=FILES)

    def result(self, data, identifier):
        return CodeResult(json.dumps(data,ensure_ascii=False),lab_models.LUNA,10,20,request_id=identifier)

    def css_patch(self, old='red', new='blue'):
        return {'changes':[{'file':'assets/site.css','op':'replace','old':old,'new':new}],'report':REPORT}

    def edit(self):
        return edit_backend('Сделай кнопку синей',source=self.source,context={'edit_files':['assets/site.css']})

    def test_bad_literal_is_corrected_and_all_costs_are_counted(self):
        with lab_models.model_scope(lab_models.LUNA), patch('founder.services.backend_edits.generate_code',side_effect=[
            self.result(self.css_patch('missing'),'bad'),self.result(self.css_patch(),'good')]) as api:
            result=self.edit()
        self.assertEqual(api.call_count,2)
        self.assertIn('не найден',json.loads(api.call_args.args[1][0]['content'])['repair']['validation_error'])
        self.assertEqual(result.backend_files['assets/site.css'],'button { color: blue; }')
        self.assertEqual(result.backend_files['app.py'],FILES['app.py'])
        self.assertEqual(result.request_ids,('bad','good'))
        self.assertEqual((result.input_tokens,result.output_tokens),(20,40))
        self.assertEqual(result.report['recovery']['code_attempts'],2)

    def test_last_attempt_can_replace_only_selected_file_without_sol(self):
        seen=[]
        answers=[self.result(self.css_patch('missing'),'bad1'), self.result(self.css_patch('missing'),'bad2'),
            self.result({'files':{'assets/site.css':'button { color: blue; }'},'delete_files':[],'report':REPORT},'fixed')]
        def answer(*args,**kwargs):
            seen.append(lab_models.active_model())
            return answers.pop(0)
        with lab_models.model_scope(lab_models.LUNA), patch('founder.services.backend_edits.generate_code',side_effect=answer) as api:
            result=self.edit()
        self.assertEqual(api.call_count,3)
        self.assertEqual(seen,[lab_models.LUNA]*3)
        self.assertEqual(result.report['recovery']['code_attempts'],3)
        for name in FILES:
            if name != 'assets/site.css': self.assertEqual(result.backend_files[name],FILES[name])
        self.assertIn('полный исправленный код',api.call_args.args[0])

    def test_wrong_scope_is_replanned_independently_before_app_change(self):
        server={'changes':[{'file':'app.py','op':'replace','old':"data['name']",'new':"data['name'].strip()"}],'report':REPORT}
        answers=[self.result(server,'wrong-scope'),self.result({'edit_files':['app.py']},'scope'),self.result(server,'fixed')]
        with patch('founder.services.backend_edits.generate_code',side_effect=answers) as api:
            result=edit_backend('Обрезай пробелы в названии на сервере',source=self.source,context={'edit_files':['assets/site.css']})
        self.assertEqual(api.call_count,3)
        self.assertEqual(result.report['edit_plan']['planned_files'],['app.py'])
        self.assertEqual(result.backend_files['index.html'],FILES['index.html'])
        self.assertEqual(result.request_ids,('wrong-scope','scope','fixed'))

    def test_invalid_initial_scope_plan_is_repaired_before_any_edit(self):
        with patch('founder.services.backend_edits.generate_code',side_effect=[
            self.result({'edit_files':[]},'bad-plan'),self.result({'edit_files':['assets/site.css']},'plan'),
            self.result(self.css_patch(),'fixed')]) as api:
            result=edit_backend('Цвет кнопки',source=self.source)
        self.assertEqual(api.call_count,3)
        self.assertEqual(result.request_ids,('bad-plan','plan','fixed'))
        self.assertEqual(result.report['recovery']['model_calls'],3)

    def test_retry_limit_preserves_source_and_explains_failure(self):
        with patch('founder.services.backend_edits.generate_code',return_value=self.result(self.css_patch('missing'),'bad')) as api:
            with self.assertRaisesMessage(QwenOutputError,'после 3 попыток'):
                self.edit()
        self.assertEqual(api.call_count,3)
        self.assertEqual(self.source.backend_files,FILES)

    def test_final_full_file_response_cannot_expand_scope_itself(self):
        final={'files':{'app.py':FILES['app.py']+'\n# unwanted','assets/site.css':'button { color:blue; }'},'report':REPORT}
        with patch('founder.services.backend_edits.generate_code',side_effect=[self.result(self.css_patch('missing'),'a'),
            self.result(self.css_patch('missing'),'b'),self.result(final,'c')]) as api:
            with self.assertRaisesMessage(QwenOutputError,'вышло за выбранные файлы'):
                self.edit()
        self.assertEqual(api.call_count,3)
        self.assertEqual(self.source.backend_files,FILES)

    def test_filtered_or_unknown_billing_errors_do_not_replay(self):
        for error in [QwenOutputError('content_filter'),QwenError('Запрос мог быть оплачен'),QwenError('Бюджет исчерпан')]:
            with self.subTest(error=error),patch('founder.services.backend_edits.generate_code',side_effect=error) as api:
                with self.assertRaises(QwenError):self.edit()
                api.assert_called_once()

    def test_secret_rejection_never_resends_secret_to_model(self):
        with patch.dict(os.environ,{'OFOX_API_KEY':'private-test-key'}),patch('founder.services.backend_edits.generate_code',
                return_value=self.result(self.css_patch('red','blue /* private-test-key */'),'bad')) as api:
            with self.assertRaises(LabRequestRejected):self.edit()
        api.assert_called_once()
        self.assertNotIn('private-test-key',api.call_args.args[1][0]['content'])

    def test_static_patch_is_repaired_and_other_code_stays_identical(self):
        def response(old,new,identifier):
            return self.result({'changes':[{'target':'full','find':old,'replace':new}],'report':REPORT},identifier)
        with patch('founder.services.site_generator.generate_code',side_effect=[response('missing','Далее','a'),response('Выбрать','Далее','b')]) as api:
            result=generate_site('Название кнопки Далее',previous_html=HTML,report=True)
        self.assertEqual(api.call_count,2)
        self.assertEqual(result.text,HTML.replace('Выбрать','Далее'))
        self.assertEqual(result.request_ids,('a','b'))
        self.assertEqual((result.input_tokens,result.output_tokens),(20,40))

    def test_post_generation_contract_failure_is_also_corrected(self):
        answers=[self.result({'changes':[{'target':'full','find':'Выбрать','replace':'Try'}],'report':REPORT},'a'),
                 self.result({'changes':[{'target':'full','find':'Выбрать','replace':'Добавить'}],'report':REPORT},'b')]
        def validate(result):
            if 'Добавить' not in result.text:raise QwenOutputError('Кнопка не получила нужное название')
        with validation_scope(validate),patch('founder.services.site_generator.generate_code',side_effect=answers) as api:
            result=generate_site('Назови кнопку Добавить',previous_html=HTML,report=True)
        self.assertEqual(api.call_count,2)
        self.assertIn('Добавить',result.text)

    def test_static_filtered_output_stops_without_retry(self):
        with patch('founder.services.site_generator.generate_code',side_effect=QwenOutputError('content_filter')) as api:
            with self.assertRaises(QwenOutputError):generate_site('Правка',previous_html=HTML)
        api.assert_called_once()
