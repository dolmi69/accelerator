"""Meaningful spend boundaries: local edits, exact patches, cache and failed calls."""
import json
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import LabAIUsage, LabSiteVersion, LabSpendBucket, StartupProfile, User, ChatSession, ChatMessage
from founder.services.ai_costs import CostLimitError, billing_scope, reserve, settle, usage_summary
from founder.services.qwen import CodeResult, QwenError
from founder.services.site_generator import generate_site
from founder.services.site_patches import fragments, patch_messages, apply_patch_response
from founder.services.site_editor import customize, simple_command
from founder.services.memory import conversation_context, remember_user_message

MODEL = 'Qwen/Qwen3-Coder-Next'
HTML = '<!doctype html><html><head><title>Demo</title><style>body{color:#123456}</style></head><body><h1>Demo</h1><section id="offer"><button>Buy</button></section><script>const x=1;</script></body></html>'


@override_settings(QWEN_PRICE_MODEL=MODEL, QWEN_INPUT_RUB_PER_MILLION='122', QWEN_OUTPUT_RUB_PER_MILLION='244', LAB_MAX_REQUEST_RUB='10', LAB_GLOBAL_DAILY_RUB='200', LAB_GLOBAL_MONTHLY_RUB='2000', LAB_USER_DAILY_RUB='20', LAB_USER_MONTHLY_RUB='100')
class CostTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cost_user', email='cost@example.test')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Demo')
        self.client.force_login(self.user)

    def reserve(self):
        with billing_scope(self.user, self.startup, 'patch'):
            return reserve(MODEL, [{'role':'user', 'content':'hello'}], 2048)

    def test_snapshot_actual_charge_and_single_refund(self):
        usage = self.reserve()
        self.assertEqual(LabSpendBucket.objects.count(), 4)
        initial = usage.accounted_micro_rub
        self.assertGreater(initial, 100*122 + 20*244)
        settle(usage, input_tokens=100, output_tokens=20, status='success')
        settle(usage, input_tokens=1, output_tokens=1, status='success')
        usage.refresh_from_db()
        self.assertEqual(usage.accounted_micro_rub, 17080)
        self.assertEqual(set(LabSpendBucket.objects.values_list('micro_rub', flat=True)), {17080})
        self.assertEqual(usage.user_id, self.user.pk)
        self.assertEqual(usage_summary(self.user)['input_tokens'], 100)

    def test_unknown_usage_retains_reserve(self):
        usage = self.reserve()
        initial = usage.accounted_micro_rub
        settle(usage, input_tokens=12)
        usage.refresh_from_db()
        self.assertEqual(usage.status, 'unknown')
        self.assertIsNone(usage.output_tokens)
        self.assertEqual(usage.accounted_micro_rub, initial)
        self.assertEqual(usage_summary(self.user)['uncertain'], 1)

    def test_each_budget_blocks_before_provider_and_rolls_back(self):
        for setting in ['LAB_MAX_REQUEST_RUB','LAB_USER_DAILY_RUB','LAB_USER_MONTHLY_RUB','LAB_GLOBAL_DAILY_RUB','LAB_GLOBAL_MONTHLY_RUB']:
            with self.subTest(setting=setting), override_settings(**{setting:'0'}):
                with self.assertRaises(CostLimitError): self.reserve()
                self.assertEqual(LabSpendBucket.objects.count(), 0)
                self.assertEqual(LabAIUsage.objects.count(), 0)

    def test_model_change_requires_own_prices_and_big_context_is_blocked(self):
        with self.assertRaises(CostLimitError): reserve('different-model', [{'content':'x'}], 20)
        with override_settings(QWEN_INPUT_BYTE_LIMIT=100):
            with self.assertRaises(CostLimitError): self.reserve()
        for value in ['NaN', '-1', 'Infinity', '0', 'bad']:
            with override_settings(QWEN_INPUT_RUB_PER_MILLION=value):
                with self.assertRaises(CostLimitError): self.reserve()
        self.assertFalse(LabAIUsage.objects.exists())

    def test_outstanding_requests_still_count_towards_budget(self):
        with override_settings(LAB_USER_DAILY_RUB='1'):
            usage = self.reserve()
            self.assertEqual(usage.status, 'reserved')
            with self.assertRaises(CostLimitError): self.reserve()
            self.assertEqual(LabAIUsage.objects.count(), 1)

    def test_automatic_color_and_explicit_controls_use_zero_ai_even_when_budget_zero(self):
        source = LabSiteVersion.objects.create(startup=self.startup, prompt='Initial', html=HTML, model=MODEL)
        with override_settings(LAB_USER_DAILY_RUB='0', LAB_REQUESTS_PER_DAY=0), patch('founder.lab_views.generate_site') as paid:
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]), {'source_version':source.pk, 'prompt':'поменяй цветовую гамму на синюю'})
            self.assertEqual(response.status_code, 302)
            latest = self.startup.lab_versions.first()
            self.assertEqual((latest.input_tokens,latest.output_tokens), (0,0))
            self.assertEqual(latest.edit_method, 'local')
            response = self.client.post(reverse('lab_customize', args=[self.startup.pk]), {'source_version':latest.pk, 'title':'<New>', 'font':'serif', 'radius':'8'})
            self.assertEqual(response.status_code, 302)
            latest = self.startup.lab_versions.first()
            self.assertIn('<title>&lt;New&gt;</title>', latest.html)
            self.assertNotIn('<New>', latest.html)
            self.assertIn('Georgia,serif', latest.html)
            self.assertEqual(source.html, HTML)
            paid.assert_not_called()
        self.assertEqual(LabAIUsage.objects.count(), 0)
        self.assertFalse(simple_command('поменяй цветовую гамму на синюю и добавь оплату'))

    def test_identical_requests_reuse_saved_result_even_after_quota_exhausted(self):
        route=reverse('lab_generate', args=[self.startup.pk])
        with override_settings(LAB_REQUESTS_PER_DAY=1), patch('founder.lab_views.generate_site', return_value=CodeResult(HTML,MODEL,10,20)) as paid:
            self.assertEqual(self.client.post(route, {'prompt':'Landing'}).status_code, 302)
            response=self.client.post(route, {'prompt':'Landing'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.startup.lab_versions.count(), 1)
            self.assertEqual(paid.call_count, 1)
            self.assertEqual(self.client.post(route, {'prompt':'Other'}).status_code, 429)

    def test_free_controls_and_cache_do_not_cross_ownership(self):
        source = LabSiteVersion.objects.create(startup=self.startup, prompt='Initial', html=HTML, model=MODEL)
        other = User.objects.create_user(username='foreign_cost', email='other@cost.test')
        self.client.force_login(other)
        response=self.client.post(reverse('lab_customize', args=[self.startup.pk]), {'source_version':source.pk,'palette':'blue'})
        self.assertEqual(response.status_code,404)
        self.assertEqual(self.startup.lab_versions.count(),1)

    def test_fragment_context_excludes_unselected_document_and_patch_is_exact(self):
        source = HTML.replace('<h1>Demo</h1>', '<h1>'+('long text ' * 6000)+'</h1>')
        region = next(part for part in fragments(source) if source[part.start:part.end].startswith('<section id="offer"'))
        payload, regions = patch_messages('Change button', source, region.key)
        self.assertLess(len(json.dumps(payload)), len(source)//10)
        output = apply_patch_response(source, regions, json.dumps({'changes':[{'target':region.key,'find':'Buy','replace':'Try'}]}))
        self.assertEqual(output, source.replace('Buy','Try'))
        with self.assertRaises(QwenError): patch_messages('Change', source)
        for changes in [[], [{'target':'missing','find':'Buy','replace':'Try'}],
                        [{'target':region.key,'find':'missing','replace':'Try'}],
                        [{'target':region.key,'find':'Buy','replace':'Try'},{'target':region.key,'find':'<button>Buy</button>','replace':'OK'}],
                        [{'target':region.key,'find':'Buy','replace':'Buy'}]]:
            with self.assertRaises(QwenError): apply_patch_response(source,regions,json.dumps({'changes':changes}))
        with self.assertRaises(QwenError): apply_patch_response(source,regions,'```json\n{}\n```')

    def test_patch_one_call_and_lower_output_limit_and_invalid_result_no_fallback(self):
        change={'changes':[{'target':'full','find':'Buy','replace':'Try'}]}
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult(json.dumps(change),MODEL,12,24)) as paid:
            result=generate_site('Change button',previous_html=HTML, max_tokens=6144)
            self.assertEqual(result.text,HTML.replace('Buy','Try'))
            self.assertEqual(paid.call_count,1)
            self.assertEqual(paid.call_args.kwargs['max_tokens'],2048)
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult(HTML,MODEL,12,24)) as paid:
            with self.assertRaises(QwenError): generate_site('Change', previous_html=HTML)
            self.assertEqual(paid.call_count,1)
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult(HTML,MODEL,12,24)) as paid:
            generate_site('Redesign',previous_html=HTML,rebuild=True)
            self.assertEqual(paid.call_args.kwargs['max_tokens'],6144)

    def test_theme_is_idempotent_and_revalidates_css(self):
        first=customize(HTML, {'palette':'blue','font':'system'})
        self.assertEqual(first,customize(first, {'palette':'blue','font':'system'}))
        cleared=customize(first,{})
        self.assertEqual(cleared,HTML)
        self.assertEqual(customize(HTML,{'palette':'red;expression(...)','font':'<script>'}),HTML)

    def test_repeated_labels_are_accepted_only_when_complete_patch_is_unambiguous(self):
        html = HTML.replace('<button>Buy</button>', '<button>Buy</button>').replace('const x=1;', "const label='Buy';")
        regions = fragments(html)[:1]
        change = {'target':'full','find':'Buy','replace':'Try'}
        with self.assertRaises(QwenError):
            apply_patch_response(html, regions, json.dumps({'changes':[change]}))
        data={'changes':[change, {'target':'full','find':"'Buy'",'replace':"'Try'"}]}
        result=apply_patch_response(html,regions,'```json\n'+json.dumps(data)+'\n```')
        self.assertEqual(result,html.replace('Buy','Try'))

    @override_settings(BRUNO_HISTORY_CHAR_LIMIT=12000)
    def test_chat_history_budget_preserves_latest_and_retrieves_omitted_user_claims(self):
        session=ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        old=ChatMessage.objects.create(session=session,role='user',content='Выручка за март 100000 рублей. '+('старый текст '*800))
        remember_user_message(old)
        for number in range(17):
            ChatMessage.objects.create(session=session,role='assistant',content=f'{number} '+('long '*2000))
        latest=ChatMessage.objects.create(session=session,role='user',content='Выручка за март 50000 рублей.')
        context,memories=conversation_context(session,latest)
        self.assertEqual(context[-1]['content'],latest.content)
        self.assertLessEqual(sum(len(item['content']) for item in context),12000)
        self.assertIn(old.pk,[item.source_message_id for item in memories])


@override_settings(QWEN_PRICE_MODEL=MODEL, QWEN_INPUT_RUB_PER_MILLION='122', QWEN_OUTPUT_RUB_PER_MILLION='244', LAB_MAX_REQUEST_RUB='10', LAB_GLOBAL_DAILY_RUB='1', LAB_GLOBAL_MONTHLY_RUB='2000')
class ConcurrentCostTests(TransactionTestCase):
    def test_simultaneous_reservations_cannot_exceed_global_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        barrier = Barrier(2)
        def attempt():
            try:
                barrier.wait(timeout=5)
                try:
                    return str(reserve(MODEL, [{'content':'hello'}], 2048).pk)
                except CostLimitError:
                    return None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertLessEqual(sum(result is not None for result in outcomes), 1)
        self.assertLessEqual(LabAIUsage.objects.count(), 1)
        self.assertTrue(all(value <= 1000000 for value in LabSpendBucket.objects.filter(key__contains=':day:').values_list('micro_rub',flat=True)))
