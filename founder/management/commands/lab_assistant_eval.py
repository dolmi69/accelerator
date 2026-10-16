"""Repeatable lab cases; paid requests only with an explicit --live flag."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from founder.models import StartupProfile
from founder.services.ai_costs import billing_scope
from founder.services.lab_conversation import discussion_reply, route_message
from founder.services.lab_bruno import request_policy, LabRequestRejected
from founder.services.qwen import QwenError
from founder.services.request_limits import acquire_ai_lease, release_ai_lease
from founder.services.site_generator import generate_site

FIXTURE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Калькулятор калорий</title><style>body{font-family:system-ui;background:#f5f8f5;color:#183b36}main{max-width:600px;margin:40px auto;padding:24px}button{background:#176c58;color:white;padding:12px;border:0;border-radius:8px}</style></head><body><main><h1>Калькулятор калорий</h1><label>Вес, кг <input id="weight" type="number" min="1"></label><button id="calculate">Рассчитать</button><output id="result"></output></main><script>function calories(weight){return weight*30}document.getElementById('calculate').onclick=()=>{const weight=Number(document.getElementById('weight').value);document.getElementById('result').textContent=weight>0?calories(weight)+' ккал':'Введите вес';}</script></body></html>'''

CASES = (
    ('saved_report', 'Что изменилось?', 'discuss'),
    ('question', 'Объясни, как рассчитываются калории на этом сайте. Ничего не меняй.', 'discuss'),
    ('edit', 'Замени заголовок «Калькулятор калорий» на «Моя норма калорий». Сохрани формулу и обработчик.', 'edit'),
    ('design', 'Сделай кнопку расчёта заметнее и удобнее на телефоне. Сохрани название, поля и формулу.', 'edit'),
    ('forbidden', 'Подключи GigaChat с общим ключом BrunoLab.', 'edit'),
)


class Command(BaseCommand):
    help = 'Пять одинаковых проверок лаборатории. --live делает три платных вызова без изменения проектов.'

    def add_arguments(self, parser):
        parser.add_argument('--startup', required=True)
        parser.add_argument('--output', default='.runtime/lab-assistant-eval')
        parser.add_argument('--live', action='store_true')

    def handle(self, *args, **options):
        startup = StartupProfile.objects.select_related('owner').get(pk=options['startup'])
        path = Path(options['output']).resolve()
        path.mkdir(parents=True, exist_ok=True)
        source = SimpleNamespace(pk=uuid4(), id=uuid4(), html=FIXTURE, prompt='Собрать калькулятор', kind='static',
            model='eval-fixture', edit_method='full', source_id=None,
            backend_modules=[], presentation={}, bruno_report={'title':'Готово', 'items':['Добавил расчёт: вес × 30.']})
        source.pk = source.id
        results = []
        for key, prompt, expected_route in CASES:
            row = {'case':key, 'prompt':prompt, 'route':route_message(prompt), 'expected_route':expected_route}
            row['passed'] = row['route'] == expected_route
            if key == 'forbidden':
                try:
                    request_policy(prompt)
                    row['passed'] = False
                except LabRequestRejected:
                    row['refused'] = True
            elif key == 'saved_report':
                reply, result = discussion_reply(startup, source, prompt)
                row.update(answer=reply['answer'], local=result is None)
                row['passed'] &= result is None and 'вес × 30' in reply['answer']
            elif options['live']:
                token = acquire_ai_lease(startup.owner_id, ttl_seconds=600)
                try:
                    with billing_scope(startup.owner, startup, 'eval'):
                        if key == 'question':
                            reply, result = discussion_reply(startup, source, prompt)
                            row['answer'] = reply['answer']
                            row['passed'] &= '30' in reply['answer']
                        else:
                            result = generate_site(prompt, previous_html=FIXTURE, max_tokens=8192, report=True,
                                project_context={'name':'Смета', 'prototype':{'title':'Калькулятор калорий'}, 'modules':[]})
                            (path / f'{key}.html').write_text(result.text, encoding='utf-8')
                            row['report'] = result.report
                            row['passed'] &= 'function calories(weight){return weight*30}' in result.text
                            if key == 'edit':
                                row['passed'] &= '<h1>Моя норма калорий</h1>' in result.text
                    row.update(model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
                except QwenError as exc:
                    row.update(passed=False, error=str(exc))
                finally:
                    release_ai_lease(startup.owner_id, token)
            else:
                row['live_check'] = 'not_run'
            results.append(row)
            self.stdout.write(f'{key}: {"OK" if row["passed"] else "FAIL"}')
        artifact = {'created_at':timezone.now().isoformat(), 'live':options['live'],
                    'fixture_sha256':hashlib.sha256(FIXTURE.encode()).hexdigest(), 'cases':results}
        (path / 'results.json').write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(f'Результаты: {path / "results.json"}')
        if not all(row['passed'] for row in results):
            raise CommandError('Некоторые проверки не прошли; результаты сохранены.')
