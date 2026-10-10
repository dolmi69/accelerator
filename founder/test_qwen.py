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


@override_settings(AI_PROVIDER="gigachat", QWEN_CODE_MODEL="Qwen/Qwen3-Coder-Next",
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
