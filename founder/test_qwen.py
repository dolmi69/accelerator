import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from openai import OpenAI

from founder.services.qwen import CodeResult, QwenError, QwenOutputError, generate_code
from founder.services.site_generator import generate_site
from founder.services.site_interactions import enhance_interactions
from founder.models import LabAIUsage

HTML = '<!doctype html><html lang="ru"><head><title>Demo</title></head><body>Привет</body></html>'


def completion(text=HTML, *, finish="stop", usage=True):
    data = {"id": "test", "object": "chat.completion", "created": 1,
            "model": "Qwen/Qwen3-Coder-Next",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": text}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 42, "completion_tokens": 80, "total_tokens": 122}
    return data


@override_settings(OFOX_CODE_STREAM=False, LAB_CODE_PROVIDER="cloudru", OFOX_CODE_API="chat_completions", AI_PROVIDER="gigachat", QWEN_CODE_MODEL="Qwen/Qwen3-Coder-Next",
                   QWEN_CODE_MAX_TOKENS=8192, QWEN_CODE_TIMEOUT=90, LAB_PATCH_MAX_TOKENS=2048,
                   QWEN_PRICE_MODEL="Qwen/Qwen3-Coder-Next",
                   QWEN_INPUT_RUB_PER_MILLION="122", QWEN_OUTPUT_RUB_PER_MILLION="244")
class QwenTests(TestCase):
    def setUp(self):
        self.key = patch.dict(os.environ, {"CLOUDRU_API_KEY": "test-cloudru-secret"})
        self.key.start()
        self.addCleanup(self.key.stop)

    def use_transport(self, handler):
        def factory(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        mocked = patch("founder.services.qwen.OpenAI", side_effect=factory)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_real_sdk_request_targets_qwen_and_keeps_bruno_provider(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://foundation-models.api.cloud.ru/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-cloudru-secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "Qwen/Qwen3-Coder-Next")
            self.assertEqual(body["max_tokens"], 4096)
            self.assertEqual(body["temperature"], 0.6)
            self.assertEqual(body["messages"][-1]["content"], "Сайт о сне")
            self.assertFalse(body["stream"])
            return httpx.Response(200, json=completion())
        self.use_transport(handler)
        result = generate_site("Сайт о сне", max_tokens=4096)
        self.assertEqual(result.text, HTML)
        self.assertEqual((result.input_tokens, result.output_tokens), (42, 80))
        self.assertEqual(settings.AI_PROVIDER, "gigachat")
        self.assertEqual(len(requests), 1)

    @override_settings(LAB_CODE_PROVIDER="ofox", OFOX_CODE_MODEL="openai/gpt-6-luna",
                       OFOX_PRICE_MODEL="openai/gpt-6-luna", OFOX_CODE_REASONING_EFFORT="medium",
                       OFOX_INPUT_USD_PER_MILLION="0.10", OFOX_OUTPUT_USD_PER_MILLION="0.50",
                       OFOX_USD_RUB_RATE="100")
    def test_ofox_luna_request_has_separate_key_reasoning_and_correct_cost(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://api.ofox.ai/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-ofox-secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "openai/gpt-6-luna")
            self.assertEqual(body["max_completion_tokens"], 4096)
            self.assertEqual(body["reasoning_effort"], "medium")
            self.assertNotIn("temperature", body)
            self.assertNotIn("max_tokens", body)
            return httpx.Response(200, json=completion())
        self.use_transport(handler)
        with patch.dict(os.environ, {"OFOX_API_KEY": "test-ofox-secret", "CLOUDRU_API_KEY": ""}):
            result = generate_code('system', [{"role": "user", "content": "site"}], max_tokens=4096)
        self.assertEqual(result.model, "openai/gpt-6-luna")
        self.assertEqual(len(requests), 1)
        row = LabAIUsage.objects.latest('created_at')
        self.assertEqual(row.accounted_micro_rub, 4420)
        self.assertEqual(row.status, 'success')
        self.assertEqual(settings.AI_PROVIDER, 'gigachat')

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses',
                       OFOX_CODE_MODEL='anthropic/claude-sonnet-5.5', OFOX_PRICE_MODEL='anthropic/claude-sonnet-5.5',
                       OFOX_INPUT_USD_PER_MILLION='2', OFOX_OUTPUT_USD_PER_MILLION='10', OFOX_USD_RUB_RATE='100')
    def test_ofox_sonnet_responses_payload_and_pricing(self):
        requests = []

        def handler(request):
            requests.append(request)
            body = json.loads(request.content)
            self.assertEqual(str(request.url), 'https://api.ofox.ai/v1/responses')
            self.assertEqual(body['model'], 'anthropic/claude-sonnet-5.5')
            self.assertEqual(body['max_output_tokens'], 4096)
            self.assertEqual(body['temperature'], 0.3)
            self.assertNotIn('reasoning', body)
            return httpx.Response(200, json={'id':'resp_sonnet','object':'response','created_at':1,
                'model':'anthropic/claude-sonnet-5.5','status':'completed',
                'output':[{'id':'msg_sonnet','type':'message','role':'assistant','status':'completed',
                           'content':[{'type':'output_text','text':HTML,'annotations':[]}]}],
                'usage':{'input_tokens':42,'output_tokens':80,'total_tokens':122}})

        self.use_transport(handler)
        with patch.dict(os.environ, {'OFOX_API_KEY':'test-ofox-secret'}):
            result = generate_code('system', [{'role':'user','content':'site'}], max_tokens=4096)
        self.assertEqual(result.model, 'anthropic/claude-sonnet-5.5')
        self.assertEqual(result.text, HTML)
        self.assertEqual(len(requests), 1)
        self.assertEqual(LabAIUsage.objects.latest('created_at').accounted_micro_rub, 88400)

    @override_settings(LAB_CODE_PROVIDER="ofox")
    def test_ofox_never_uses_cloudru_key_if_its_own_key_is_missing(self):
        with patch.dict(os.environ, {"OFOX_API_KEY": ""}), patch('founder.services.qwen.OpenAI') as client:
            with self.assertRaisesMessage(QwenError, 'OFOX_API_KEY'):
                generate_code('system', [{"role": "user", "content": "site"}])
            client.assert_not_called()

    @override_settings(QWEN_CODE_MAX_TOKENS=81920, LAB_CREATE_MAX_TOKENS=81920,
                       QWEN_CODE_TIMEOUT=1500, LAB_MAX_REQUEST_RUB='50')
    def test_first_design_uses_larger_limit_and_hard_cap_still_applies(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=completion())
        self.use_transport(handler)
        generate_site('Магазин парфюмерии')
        self.assertEqual(requests[0]['max_tokens'], 81920)
        self.assertEqual(requests[0]['temperature'], 0.6)
        self.assertIn('парфюмерии', requests[0]['messages'][0]['content'])
        with override_settings(QWEN_CODE_MAX_TOKENS=4096):
            generate_site('Другой сайт')
        self.assertEqual(requests[1]['max_tokens'], 4096)

    @override_settings(QWEN_CODE_MAX_TOKENS=81920, LAB_PATCH_MAX_TOKENS=10240,
                       LAB_PATCH_MAX_RESPONSE_CHARS=120000, LAB_PATCH_MAX_FIND_CHARS=30000,
                       LAB_PATCH_MAX_REPLACEMENT_CHARS=60000)
    def test_larger_edit_survives_old_character_caps_and_preserves_other_content(self):
        replacement = '<section id="notes">' + 'x' * 28000 + '</section>'
        response = {'changes': [{'target': 'full', 'find': 'Привет', 'replace': replacement}],
                    'report': {'summary': 'Добавил раздел.', 'completed': ['Добавлен раздел notes.'], 'not_done': []}}
        def handler(request):
            self.assertEqual(json.loads(request.content)['max_tokens'], 10240)
            return httpx.Response(200, json=completion(json.dumps(response, ensure_ascii=False)))
        self.use_transport(handler)
        result = generate_site('Добавь раздел', previous_html=HTML, report=True)
        self.assertEqual(result.text, HTML.replace('Привет', replacement))
        self.assertEqual(result.report['completed'], ['Добавлен раздел notes.'])

    def test_invalid_temperature_cannot_reserve_budget_or_contact_provider(self):
        with patch('founder.services.qwen.OpenAI') as provider:
            for value in [True, -0.1, 1.1, float('nan'), float('inf'), '0.6']:
                with self.subTest(value=value), self.assertRaises(QwenError):
                    generate_code('system', [{'role': 'user', 'content': 'site'}], temperature=value)
            provider.assert_not_called()
        self.assertFalse(LabAIUsage.objects.exists())

    def test_sandbox_interactions_are_self_contained_idempotent_and_do_not_change_ordinary_pages(self):
        self.assertEqual(enhance_interactions(HTML), HTML)
        source = HTML.replace('Привет', '<a href="#">Детали</a><button onclick="alert(\'Пример\')">Купить</button>')
        result = enhance_interactions(source)
        self.assertIn('data-lab-interactions="1"', result)
        self.assertIn('textContent=', result)
        self.assertNotIn('innerHTML', result)
        self.assertIn('stopImmediatePropagation', result)
        self.assertEqual(enhance_interactions(result), result)
        self.assertTrue(result.endswith('</html>'))
        self.assertLess(result.index('window.alert=notify'), result.index('onclick='))

    def test_paid_calls_are_not_retried_and_provider_payload_is_not_exposed(self):
        for status in (400, 401, 402, 403, 404, 429, 503):
            with self.subTest(status=status):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={"error": {"message": "PRIVATE test-cloudru-secret"}})
                with patch("founder.services.qwen.OpenAI", side_effect=lambda **kw: OpenAI(
                    **kw, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                )):
                    with self.assertLogs("founder.services.qwen", "WARNING") as log:
                        with self.assertRaises(QwenError) as error:
                            generate_site("Тест")
                self.assertEqual(len(calls), 1)
                self.assertNotIn("PRIVATE", str(error.exception) + str(log.output))
                self.assertNotIn("test-cloudru-secret", str(error.exception) + str(log.output))

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses',
                       OFOX_CODE_MODEL='openai/gpt-6-luna', OFOX_PRICE_MODEL='openai/gpt-6-luna')
    def test_ofox_gateway_failures_keep_usage_unknown_and_are_not_retried(self):
        for status in (502, 503, 504):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder, \
                 override_settings(BASE_DIR=Path(folder)), patch.dict(os.environ, {'OFOX_API_KEY': 'test-ofox-secret'}):
                calls = []

                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={'error': {'message': 'PRIVATE test-ofox-secret'}})

                self.use_transport(handler)
                with self.assertLogs('founder.services.qwen', 'WARNING') as log:
                    with self.assertRaises(QwenError) as error:
                        generate_code('system', [{'role': 'user', 'content': 'site'}])
                self.assertIn(f'HTTP {status}', str(error.exception))
                self.assertEqual(len(calls), 1)
                row = LabAIUsage.objects.latest('created_at')
                self.assertEqual(row.status, 'unknown')
                self.assertIsNone(row.input_tokens)
                self.assertIsNone(row.output_tokens)
                trace = (Path(folder) / '.runtime' / 'lab-ai-diagnostics' / f'{row.pk}.json').read_text()
                self.assertEqual(json.loads(trace)['http_status'], status)
                public = str(error.exception) + str(log.output) + trace
                self.assertNotIn('PRIVATE', public)
                self.assertNotIn('test-ofox-secret', public)

    def test_timeout_has_no_automatic_retry(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private request", request=request)
        self.use_transport(handler)
        with self.assertRaisesMessage(QwenError, "автоматического повтора нет"):
            generate_site("Тест")
        self.assertEqual(len(calls), 1)
        record = LabAIUsage.objects.get()
        self.assertEqual(record.status, "unknown")
        self.assertGreater(record.accounted_micro_rub, 0)

    def test_truncated_or_malformed_responses_are_not_treated_as_complete(self):
        for data in (completion(finish="length"), completion(text=""), completion(finish="tool_calls"),
                     {"choices": []}, completion(text="не HTML")):
            with self.subTest(data=data):
                with patch("founder.services.qwen.OpenAI", side_effect=lambda **kw: OpenAI(
                    **kw, http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))),
                )):
                    with self.assertRaises(QwenOutputError):
                        generate_site("Тест")
                record = LabAIUsage.objects.latest("created_at")
                if "usage" in data:
                    self.assertEqual(record.status, "output_error")
                    self.assertEqual(record.output_tokens, 80)

    def test_compatible_gateway_missing_finish_reason_still_validates_document(self):
        self.use_transport(lambda request: httpx.Response(200, json=completion(finish=None)))
        result = generate_site('Создай сайт')
        self.assertIn('</html>', result.text)
        self.use_transport(lambda request: httpx.Response(200, json=completion(text='', finish=None, usage=False)))
        with self.assertRaisesMessage(QwenOutputError, 'пустой ответ без статуса завершения'):
            generate_site('Создай сайт')

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_MODEL='openai/gpt-6-luna',
                       OFOX_PRICE_MODEL='openai/gpt-6-luna', OFOX_PROVIDER_TYPE='openai')
    def test_ofox_channel_is_sent_as_header_without_changing_model(self):
        def handler(request):
            self.assertEqual(request.headers['X-OfoxAI-Provider-Type'], 'openai')
            self.assertEqual(json.loads(request.content)['model'], 'openai/gpt-6-luna')
            return httpx.Response(200, json=completion())
        self.use_transport(handler)
        with patch.dict(os.environ, {'OFOX_API_KEY':'test-ofox-key'}):
            generate_code('system', [{'role':'user', 'content':'test'}], max_tokens=2048)

    def test_filtered_code_is_never_applied_and_diagnostics_exclude_raw_data(self):
        data = completion(text=HTML, finish='content_filter')
        data['choices'][0]['content_filter_results'] = {
            'protected_material_code': {'filtered':True, 'detected':True, 'citation':'PRIVATE'},
            'private_field': {'filtered':True, 'text':'PRIVATE'}}
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=data)
            self.use_transport(handler)
            with self.assertRaisesMessage(QwenOutputError, 'Фильтр провайдера'):
                generate_code('PRIVATE instruction', [{'role':'user', 'content':'PRIVATE prompt'}], max_tokens=2048)
            self.assertEqual(len(calls), 1)
            record = json.loads(next((Path(folder)/'.runtime/lab-ai-diagnostics').glob('*.json')).read_text())
            self.assertEqual(record['filters']['output'], {'protected_material_code':{'filtered':True,'detected':True}})
            self.assertNotIn('PRIVATE', json.dumps(record))
            self.assertNotIn(HTML, json.dumps(record))
            self.assertEqual(LabAIUsage.objects.get().status, 'output_error')

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses', OFOX_PROVIDER_TYPE='',
                       OFOX_CODE_MODEL='openai/gpt-6-luna', OFOX_PRICE_MODEL='openai/gpt-6-luna')
    def test_responses_protocol_keeps_model_source_limits_and_usage(self):
        def handler(request):
            self.assertEqual(str(request.url), 'https://api.ofox.ai/v1/responses')
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'openai/gpt-6-luna')
            self.assertEqual(body['instructions'], 'system')
            self.assertEqual(body['reasoning'], {'effort':'low'})
            self.assertFalse(body['store'])
            self.assertEqual(body['max_output_tokens'], 2048)
            self.assertEqual(body['input'][0]['content'][0]['text'], HTML)
            self.assertEqual(body['input'][0]['content'][0]['type'], 'output_text')
            self.assertEqual(body['input'][0]['status'], 'completed')
            self.assertTrue(body['input'][0]['id'].startswith('msg_'))
            return httpx.Response(200, json={'id':'resp_test','object':'response','created_at':1,
                'model':'openai/gpt-6-luna','status':'completed',
                'output':[{'id':'msg_test','type':'message','role':'assistant','status':'completed',
                           'content':[{'type':'output_text','text':HTML,'annotations':[]}]}],
                'usage':{'input_tokens':42,'output_tokens':80,'total_tokens':122}})
        self.use_transport(handler)
        with patch.dict(os.environ, {'OFOX_API_KEY':'test-ofox-key'}):
            result = generate_code('system', [{'role':'assistant','content':HTML}, {'role':'user','content':'Измени заголовок'}], max_tokens=2048, reasoning_effort='low')
        self.assertEqual(result.text, HTML)
        self.assertEqual((result.input_tokens,result.output_tokens), (42,80))
        self.assertEqual(LabAIUsage.objects.get().status, 'success')

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses', OFOX_PROVIDER_TYPE='',
                       OFOX_CODE_MODEL='openai/gpt-6-luna', OFOX_PRICE_MODEL='openai/gpt-6-luna')
    def test_responses_incomplete_and_refused_outputs_are_not_applied(self):
        for reason in ('max_output_tokens','content_filter'):
            with self.subTest(reason=reason):
                self.use_transport(lambda request: httpx.Response(200, json={
                    'id':'resp_test','object':'response','created_at':1,'model':'openai/gpt-6-luna',
                    'status':'incomplete','incomplete_details':{'reason':reason},
                    'output':[{'id':'msg_test','type':'message','role':'assistant','status':'incomplete',
                               'content':[{'type':'output_text','text':HTML,'annotations':[]}]}],
                    'usage':{'input_tokens':42,'output_tokens':80,'total_tokens':122}}))
                with patch.dict(os.environ, {'OFOX_API_KEY':'test-ofox-key'}), self.assertRaises(QwenOutputError):
                    generate_code('system', [{'role':'user','content':'test'}], max_tokens=2048)
        self.assertFalse(LabAIUsage.objects.filter(status='success').exists())

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses', OFOX_PROVIDER_TYPE='',
                       OFOX_CODE_MODEL='openai/gpt-6-luna', OFOX_PRICE_MODEL='openai/gpt-6-luna')
    def test_model_refusal_is_distinct_from_upstream_filter_and_trace_has_no_text(self):
        data = {'id':'resp_trace', 'object':'response', 'created_at':1,
                'model':'openai/gpt-6-luna', 'status':'completed',
                'output':[{'id':'msg_trace','type':'message','role':'assistant','status':'completed',
                           'content':[{'type':'refusal','refusal':'PRIVATE refusal explanation'}]}],
                'usage':{'input_tokens':42,'output_tokens':80,'total_tokens':122}}
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)):
            self.use_transport(lambda request: httpx.Response(200, json=data, headers={'x-request-id':'req_trace'}))
            with patch.dict(os.environ, {'OFOX_API_KEY':'test-ofox-key'}), self.assertRaisesMessage(QwenOutputError, 'отказалась выполнить запрос'):
                generate_code('system', [{'role':'user','content':'test'}], max_tokens=2048)
            trace = json.loads(next((Path(folder)/'.runtime/lab-ai-diagnostics').glob('*.json')).read_text())
            self.assertEqual(trace['finish_reason'], 'refusal')
            self.assertEqual(trace['response_status'], 'completed')
            self.assertTrue(trace['refusal'])
            self.assertEqual(trace['provider_request_id'], 'req_trace')
            self.assertEqual(trace['provider_response_id'], 'resp_trace')
            self.assertNotIn('PRIVATE', json.dumps(trace))
        self.assertEqual(LabAIUsage.objects.get().status, 'output_error')

    def test_missing_usage_is_unknown_not_zero(self):
        self.use_transport(lambda request: httpx.Response(200, json=completion(usage=False)))
        result = generate_site("Тест")
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)

    def test_local_validation_prevents_spending(self):
        with patch("founder.services.qwen.OpenAI") as client:
            for prompt, previous in [("", ""), ("a" * 12001, ""), ("ok", "a" * 120001)]:
                with self.assertRaises(QwenError):
                    generate_site(prompt, previous_html=previous)
            with self.assertRaises(QwenError):
                generate_site("ok", max_tokens=8193)
            with self.assertRaises(QwenError):
                generate_code("system", [{"role": [], "content": "hi"}])
            with patch.dict(os.environ, {"CLOUDRU_API_KEY": ""}):
                with self.assertRaisesMessage(QwenError, "API-ключ"):
                    generate_site("ok")
            client.assert_not_called()

    def test_revisions_send_fragments_and_return_only_replacements(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            change = {"changes": [{"target": "full", "find": "Привет", "replace": "Здравствуйте"}]}
            return httpx.Response(200, json=completion(json.dumps(change, ensure_ascii=False)))
        self.use_transport(handler)
        result = generate_site("Измени приветствие", previous_html=HTML)
        self.assertEqual(result.text, HTML.replace("Привет", "Здравствуйте"))
        self.assertEqual(result.edit_method, "patch")
        self.assertEqual(requests[0]["max_tokens"], 2048)
        self.assertEqual(requests[0]["temperature"], 0.2)
        self.assertEqual(len(requests[0]["messages"]), 2)
        payload = json.loads(requests[0]["messages"][-1]["content"])
        self.assertEqual(payload["fragments"], [{"id":"full", "html":HTML}])

    def test_command_saves_new_artifact_and_usage_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            source = Path(directory) / "source.html"
            source.write_text(HTML, encoding="utf-8")
            output = io.StringIO()
            with patch("founder.management.commands.qwen_site.generate_site",
                       return_value=CodeResult(HTML, "Qwen/Qwen3-Coder-Next", 10, 20)) as generate:
                call_command("qwen_site", prompt="Правка", input=source, stdout=output)
            generate.assert_called_once_with("Правка", previous_html=HTML, max_tokens=None)
            self.assertEqual(source.read_text(encoding="utf-8"), HTML)
            saved = list((Path(directory) / ".runtime/qwen-sites").glob("*/index.html"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(encoding="utf-8"), HTML)
            usage = json.loads(saved[0].with_name("usage.json").read_text(encoding="utf-8"))
            self.assertEqual(usage["input_tokens"], 10)
            self.assertNotIn("text", usage)
            if os.name != "nt":  # в Windows нет прав доступа POSIX
                self.assertEqual(saved[0].stat().st_mode & 0o777, 0o600)

    def test_failed_generation_does_not_create_a_site(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            with patch("founder.management.commands.qwen_site.generate_site", side_effect=QwenOutputError("Обрезано")):
                with self.assertRaises(CommandError):
                    call_command("qwen_site", prompt="Тест")
            self.assertFalse((Path(directory) / ".runtime/qwen-sites").exists())
