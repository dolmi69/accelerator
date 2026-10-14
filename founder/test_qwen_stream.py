"""Real SDK SSE parsing; terminal status, billing and incomplete streams."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from openai import OpenAI
from django.test import TestCase, override_settings
from founder.models import LabAIUsage
from founder.services.qwen import generate_code, QwenOutputError, QwenError, _consume_responses_stream

@override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_API='responses', OFOX_CODE_STREAM=True,
    OFOX_PROVIDER_TYPE='', OFOX_CODE_MODEL='openai/gpt-6.1-sol', OFOX_PRICE_MODEL='openai/gpt-6.1-sol',
    OFOX_INPUT_USD_PER_MILLION='2', OFOX_OUTPUT_USD_PER_MILLION='10', OFOX_USD_RUB_RATE='100',
    OFOX_CODE_REASONING_EFFORT='medium', QWEN_CODE_MAX_TOKENS=8192)
class StreamTests(TestCase):
    def run_stream(self, *, status='completed', terminal=True, text='hello', reason=None, fail=False):
        final={'id':'resp_test','object':'response','created_at':1,'model':'openai/gpt-6.1-sol',
            'status':status,'output':[], 'usage':{'input_tokens':42,'output_tokens':80,'total_tokens':122},
            'incomplete_details':{'reason':reason} if reason else None}
        events=[{'type':'response.created','response':{**final,'status':'in_progress'}},
            {'type':'response.output_text.delta','delta':text,'item_id':'msg_test','output_index':0,'content_index':0,'sequence_number':1,'logprobs':[]}]
        if fail:
            events.append({'type':'error','code':'other','message':'secret-provider-error','param':None})
        elif terminal:
            events.append({'type':'response.'+status,'response':final})
        payload=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events)
        def handler(request):
            body=json.loads(request.content)
            self.assertTrue(body['stream'])
            self.assertEqual(body['model'],'openai/gpt-6.1-sol')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=payload)
        def factory(**kwargs):
            self.assertEqual(kwargs['max_retries'],0)
            return OpenAI(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with patch('founder.services.qwen.OpenAI',side_effect=factory),patch.dict(os.environ,{'OFOX_API_KEY':'test-stream-key'}):
            return generate_code('system',[{'role':'user','content':'say hello'}],max_tokens=1024)

    def test_stream_terminal_usage_and_delta_only_final(self):
        result=self.run_stream()
        self.assertEqual(result.text,'hello')
        self.assertEqual((result.input_tokens,result.output_tokens),(42,80))
        self.assertEqual(LabAIUsage.objects.get().status,'success')

    def test_missing_terminal_rejects_partial_even_when_text_looks_complete(self):
        with self.assertRaisesMessage(QwenError,'оборвался'):
            self.run_stream(terminal=False,text='{"files":{}}')
        self.assertEqual(LabAIUsage.objects.get().status,'unknown')

    def test_incomplete_counts_paid_tokens_but_rejects_code(self):
        with self.assertRaisesMessage(QwenOutputError,'обрезан'):
            self.run_stream(status='incomplete',reason='max_output_tokens')
        self.assertEqual(LabAIUsage.objects.get().output_tokens,80)
        self.assertEqual(LabAIUsage.objects.get().status,'output_error')

    def test_provider_stream_error_never_exposes_message(self):
        with self.assertRaises(QwenError) as error:
            self.run_stream(fail=True)
        self.assertNotIn('secret-provider-error',str(error.exception))
        self.assertEqual(LabAIUsage.objects.get().status,'unknown')

    def test_completed_final_text_not_duplicated_with_deltas(self):
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __iter__(self):
                yield SimpleNamespace(type='response.output_text.delta',delta='hello')
                yield SimpleNamespace(type='response.completed',response=SimpleNamespace(status='completed',output_text='hello'))
        self.assertEqual(_consume_responses_stream(Stream(),{'events':0}).output_text,'hello')
