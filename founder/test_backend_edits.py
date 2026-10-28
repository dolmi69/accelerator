"""File-scoped edits are atomic and cannot trigger implicit full regeneration."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from founder.services.backend_edits import apply_changes, edit_backend, validate_plan
from founder.services.generated_backend import generate_backend
from founder.services.qwen import CodeResult, QwenOutputError
from founder.services import lab_models
from founder.test_generated_backend import FILES, REPORT

FILES = {**FILES,'assets/site.css':'button { color: red; }','assets/site.js':'const stable = true;'}


@override_settings(LAB_CODE_PROVIDER='ofox', LAB_MODEL_ROUTING=True)
class ScopedBackendTests(SimpleTestCase):
    def setUp(self):
        mock=patch('founder.services.backend_edits.reject_output')
        mock.start()
        self.addCleanup(mock.stop)

    def answer(self, changes):
        return CodeResult(json.dumps({'changes':changes,'report':REPORT}),lab_models.LUNA,10,20,request_id='patch-id')

    def test_style_only_one_call_and_every_other_file_is_identical(self):
        source = SimpleNamespace(kind='generated', backend_files=FILES)
        edit = [{'file':'assets/site.css','op':'replace','old':'color: red','new':'color: blue'}]
        with patch('founder.services.backend_edits.generate_code',return_value=self.answer(edit)) as api, \
                patch('founder.services.backend_stages.generate_staged') as full:
            result = generate_backend('Сделай кнопки синими',source=source,context={'edit_files':['assets/site.css']})
        api.assert_called_once()
        full.assert_not_called()
        self.assertEqual(result.backend_files['assets/site.css'],'button { color: blue; }')
        for path in FILES:
            if path != 'assets/site.css':
                self.assertEqual(result.backend_files[path],FILES[path])
        self.assertEqual(result.edit_method,'patch')
        self.assertEqual(result.report['edit_plan']['scope'],'frontend')
        self.assertEqual(result.report['edit_plan']['changed_files'],['assets/site.css'])
        self.assertEqual(result.request_ids,('patch-id',))

    def test_server_only_keeps_all_frontend_bytes(self):
        edit = [{'file':'app.py','op':'replace','old':'CREATE TABLE IF NOT EXISTS items (name TEXT)',
                 'new':'CREATE TABLE IF NOT EXISTS items (name TEXT CHECK(length(name) <= 100))'}]
        updated = apply_changes(FILES,['app.py'],edit)
        self.assertNotEqual(updated['app.py'],FILES['app.py'])
        self.assertEqual({p:v for p,v in updated.items() if p!='app.py'}, {p:v for p,v in FILES.items() if p!='app.py'})

    def test_related_layers_are_applied_together(self):
        edit = [{'file':'app.py','op':'replace','old':"data['name']",'new':"data['name'].strip()"},
                {'file':'index.html','op':'replace','old':'<h1>Записи</h1>','new':'<h1>Короткие записи</h1>'}]
        updated = apply_changes(FILES,['app.py','index.html'],edit)
        self.assertIn('.strip()',updated['app.py'])
        self.assertIn('Короткие записи',updated['index.html'])
        self.assertEqual(updated['assets/site.js'],FILES['assets/site.js'])

    def test_unplanned_file_cannot_be_changed_and_no_full_retry(self):
        edit = [{'file':'app.py','op':'replace','old':"data['name']",'new':"data['name'].strip()"}]
        with patch('founder.services.backend_edits.generate_code',return_value=self.answer(edit)) as api, \
                patch('founder.services.backend_stages.generate_staged') as full:
            with self.assertRaises(QwenOutputError):
                generate_backend('Сделай кнопки синими',source=SimpleNamespace(kind='generated',backend_files=FILES),context={'edit_files':['assets/site.css']})
        api.assert_called_once()
        full.assert_not_called()
        self.assertEqual(FILES['assets/site.css'],'button { color: red; }')

    def test_missing_plan_uses_luna_then_original_code_model(self):
        seen=[]
        def reply(instruction,messages,**kwargs):
            seen.append(lab_models.active_model())
            if len(seen)==1:
                return CodeResult(json.dumps({'edit_files':['assets/site.css']}),lab_models.LUNA,2,3,request_id='plan-id')
            return self.answer([{'file':'assets/site.css','op':'replace','old':'red','new':'blue'}])
        with lab_models.model_scope(lab_models.SOL), patch('founder.services.backend_edits.generate_code',side_effect=reply):
            result=edit_backend('Цвет',source=SimpleNamespace(backend_files=FILES))
        self.assertEqual(seen,[lab_models.LUNA,lab_models.SOL])
        self.assertEqual(result.request_ids,('plan-id','patch-id'))
        self.assertEqual((result.input_tokens,result.output_tokens),(12,23))

    def test_ambiguous_missing_noop_and_invalid_patch_leave_source_unchanged(self):
        for edit in [[], [{'file':'app.py','op':'replace','old':'not found','new':'x'}],
                     [{'file':'app.py','op':'replace','old':'import','new':'from'}],
                     [{'file':'app.py','op':'delete'}],
                     [{'file':'app.py','op':'replace','old':FILES['app.py'],'new':'invalid python ('}],
                     [{'file':'assets/site.css','op':'replace','old':'red','new':'red'}]]:
            with self.subTest(edit=edit), self.assertRaises(QwenOutputError):
                apply_changes(FILES,list(FILES),edit)
        self.assertIn('def application(',FILES['app.py'])

    def test_create_delete_asset_and_reject_missing_references(self):
        updated=apply_changes(FILES,['assets/site.js','assets/new.js'],[
            {'file':'assets/new.js','op':'create','content':'const next = true;'},
            {'file':'assets/site.js','op':'delete'}])
        self.assertNotIn('assets/site.js',updated)
        self.assertIn('assets/new.js',updated)
        linked={**FILES,'index.html':FILES['index.html'].replace('</head>','<script src="assets/site.js"></script></head>')}
        with self.assertRaises(QwenOutputError):
            apply_changes(linked,['assets/site.js'],[{'file':'assets/site.js','op':'delete'}])

    def test_invalid_plan_or_filtered_reply_never_rebuilds(self):
        for plan in [[],['../.env'],['app.py','app.py'],['assets/new.txt']]:
            with self.subTest(plan=plan), self.assertRaises(QwenOutputError):
                validate_plan(plan)
        with patch('founder.services.backend_edits.generate_code',side_effect=QwenOutputError('filtered')) as api, \
                patch('founder.services.backend_stages.generate_staged') as full:
            with self.assertRaises(QwenOutputError):
                generate_backend('CSS',source=SimpleNamespace(kind='generated',backend_files=FILES),context={'edit_files':['assets/site.css']})
        api.assert_called_once()
        full.assert_not_called()

    def test_explicit_rebuild_still_uses_full_creation_pipeline(self):
        source=SimpleNamespace(kind='generated',backend_files=FILES)
        with override_settings(LAB_BACKEND_STAGED_GENERATION=True), patch('founder.services.backend_stages.generate_staged') as full, patch('founder.services.backend_edits.edit_backend') as partial:
            generate_backend('Полностью пересобери',source=source,rebuild=True)
        full.assert_called_once()
        partial.assert_not_called()

    def test_adding_server_to_static_site_does_not_redraw_existing_html(self):
        source=SimpleNamespace(kind='static',html=FILES['index.html'])
        result=self.answer([{'file':'app.py','op':'create','content':FILES['app.py']}])
        with patch('founder.services.backend_edits.generate_code',return_value=result), patch('founder.services.backend_stages.generate_staged') as full:
            updated=generate_backend('Добавь серверный API без изменения интерфейса',source=source,context={'edit_files':['app.py']})
        full.assert_not_called()
        self.assertEqual(updated.text,source.html)
        self.assertEqual(updated.backend_files['app.py'],FILES['app.py'])
        self.assertEqual(updated.report['edit_plan']['scope'],'backend')


@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_UNRESTRICTED_TEST_MODE=True, LAB_CODE_PROVIDER='ofox', LAB_MODEL_ROUTING=True)
class ScopedLabFlowTests(TestCase):
    def setUp(self):
        from founder.models import User, StartupProfile, LabSiteVersion
        self.user=User.objects.create_user(username='scoped-owner')
        self.startup=StartupProfile.objects.create(owner=self.user,name='Scoped')
        self.source=LabSiteVersion.objects.create(startup=self.startup,kind='generated',backend_files=FILES,html=FILES['index.html'],model='test')
        self.client.force_login(self.user)

    def submit(self, changes):
        from django.urls import reverse
        decision={'action':'edit','kind':'generated','answer':'','suggestion':'','task':'Поменяй цвет кнопки на синий',
                  'complexity':'small','complexity_reason':'','edit_files':['assets/site.css']}
        result=CodeResult(json.dumps({'changes':changes,'report':REPORT}),lab_models.LUNA,10,20)
        with patch('founder.services.lab_intent.decide',return_value=(decision,CodeResult('',lab_models.LUNA,2,3))), \
             patch('founder.services.backend_edits.generate_code',return_value=result) as code, \
             patch('founder.services.backend_stages.generate_staged',side_effect=AssertionError('Full rebuild forbidden')):
            response=self.client.post(reverse('lab_generate',args=[self.startup.pk]),{
                'source_version':self.source.pk,'automatic':'1','prompt':'Поменяй цвет кнопки на синий'})
        code.assert_called_once()
        return response

    def test_raw_mode_cannot_force_full_backend_rebuild(self):
        response=self.submit([{'file':'assets/site.css','op':'replace','old':'red','new':'blue'}])
        self.assertEqual(response.status_code,302)
        version=self.startup.lab_versions.first()
        self.assertEqual(version.edit_method,'patch')
        self.assertEqual(version.backend_files['app.py'],FILES['app.py'])
        self.assertEqual(version.backend_files['index.html'],FILES['index.html'])
        self.assertEqual(version.backend_files['assets/site.js'],FILES['assets/site.js'])
        self.assertEqual(version.bruno_report['checks']['edit_plan']['changed_files'],['assets/site.css'])

    def test_wrong_layer_rejected_without_saving_version(self):
        response=self.submit([{'file':'app.py','op':'replace','old':"data['name']",'new':"data['name'].strip()"}])
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.startup.lab_versions.count(),1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.backend_files,FILES)
