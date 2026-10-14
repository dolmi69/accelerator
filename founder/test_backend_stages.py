import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.test import TestCase, override_settings
from founder.services.backend_stages import generate_staged
from founder.services.qwen import CodeResult,QwenOutputError
from founder.test_generated_backend import HTML,APP,REPORT

@override_settings(LAB_BACKEND_STAGED_GENERATION=True)
class BackendStageTests(TestCase):
    def answer(self,files,identifier):
        return CodeResult(json.dumps({'files':files,'report':REPORT}),'test',10,20,request_id=identifier)

    def test_two_calls_merge_server_and_interface(self):
        with patch('founder.services.backend_stages.generate_code',side_effect=[self.answer({'app.py':APP},'first'),self.answer({'index.html':HTML},'second')]) as api:
            result=generate_staged('Создай сайт')
        self.assertEqual(result.backend_files,{'app.py':APP,'index.html':HTML})
        self.assertEqual(result.request_ids,('first','second'))
        self.assertEqual((result.input_tokens,result.output_tokens),(20,40))
        second=json.loads(api.call_args_list[1].args[1][0]['content'])
        self.assertEqual(second['server_files']['app.py'],APP)

    def test_completed_server_survives_filter_and_same_prompt_resumes(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(BASE_DIR=Path(directory)):
            with patch('founder.services.backend_stages.generate_code',side_effect=[self.answer({'app.py':APP},'first'),QwenOutputError('content_filter')]):
                with self.assertRaisesMessage(QwenOutputError,'Серверный этап сохранён'):
                    generate_staged('Создай сайт',checkpoint=(1,'private-project'))
            checkpoints=list(Path(directory).rglob('*.json'))
            self.assertEqual(len(checkpoints),1)
            if os.name != "nt":
                self.assertEqual(checkpoints[0].stat().st_mode & 0o777,0o600)
            with patch('founder.services.backend_stages.generate_code',return_value=self.answer({'index.html':HTML},'second')) as api:
                result=generate_staged('Создай сайт',checkpoint=(1,'private-project'))
            self.assertEqual(api.call_count,1)
            self.assertEqual(result.request_ids,('first','second'))
            self.assertFalse(list(Path(directory).rglob('*.json')))

    def test_filtered_server_is_never_checkpointed(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(BASE_DIR=Path(directory)):
            with patch('founder.services.backend_stages.generate_code',side_effect=QwenOutputError('content_filter')):
                with self.assertRaises(QwenOutputError):
                    generate_staged('Создай сайт',checkpoint=(1,'private-project'))
            self.assertFalse(list(Path(directory).rglob('*.json')))

    def test_checkpoint_scoped_to_user_project_and_prompt(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(BASE_DIR=Path(directory)):
            for checkpoint,prompt in [((1,'one'),'create'),((2,'one'),'create'),((1,'two'),'create'),((1,'one'),'different')]:
                with patch('founder.services.backend_stages.generate_code',side_effect=[self.answer({'app.py':APP},None),QwenOutputError('filter')]) as api:
                    with self.assertRaises(QwenOutputError):
                        generate_staged(prompt,checkpoint=checkpoint)
                self.assertEqual(api.call_count,2)
            self.assertEqual(len(list(Path(directory).rglob('*.json'))),4)

    def test_interface_cannot_replace_completed_server(self):
        with patch('founder.services.backend_stages.generate_code',side_effect=[self.answer({'app.py':APP},None),self.answer({'app.py':APP,'index.html':HTML},None)]):
            with self.assertRaisesMessage(QwenOutputError,'переписать сервер'):
                generate_staged('Создай сайт')

    def test_checkpoint_round_trip_with_non_utf8_os_default(self):
        original_fdopen = os.fdopen
        original_read = Path.read_text
        def windows_fdopen(fd, mode, *args, **kwargs):
            kwargs.setdefault('encoding', 'cp1252')
            return original_fdopen(fd, mode, *args, **kwargs)
        def windows_read(path, *args, **kwargs):
            kwargs.setdefault('encoding', 'cp1252')
            return original_read(path, *args, **kwargs)
        app = APP + '\n# Русское название: парикмахерская\n'
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)), patch('os.fdopen',side_effect=windows_fdopen), patch.object(Path,'read_text',windows_read):
            with patch('founder.services.backend_stages.generate_code',side_effect=[self.answer({'app.py':app},None),QwenOutputError('filter')]):
                with self.assertRaisesMessage(QwenOutputError,'Серверный этап сохранён'):
                    generate_staged('Создай сайт',checkpoint=(1,'encoding-test'))
            with patch('founder.services.backend_stages.generate_code',return_value=self.answer({'index.html':HTML},None)) as api:
                result=generate_staged('Создай сайт',checkpoint=(1,'encoding-test'))
            self.assertEqual(api.call_count,1)
            self.assertEqual(result.backend_files['app.py'],app)
            self.assertIn(REPORT['completed'][0],result.report['completed'])
