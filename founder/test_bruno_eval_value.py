"""Проверки сценариев bruno_eval «Клиент и ценность» на подставной модели.

Настоящий прогон идёт на GigaChat (python manage.py bruno_eval -s value -s value_unknown);
здесь проверяется, что сам прогон считает замечания правильно.
"""
import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from founder.models import StartupProfile, ValueCanvas

SEGMENTS = {
    'seg1_title': 'Молодые родители в Казани', 'seg1_description': 'Ребёнок до трёх лет, вещи быстро малы',
    'seg1_context': 'Семьи с маленькими детьми', 'seg1_holder': 'Мама, платит семья', 'seg1_grade': 'A',
    'seg1_reason': 'Проблема каждый сезон',
    'seg2_title': 'Бабушки и дедушки', 'seg2_description': 'Покупают подарки внукам', 'seg2_context': 'Пенсионеры',
    'seg2_holder': 'Бабушка', 'seg2_grade': 'C', 'seg2_reason': 'Покупают редко',
    'seg3_title': 'Детские сады', 'seg3_description': 'Нужна сменная одежда', 'seg3_context': 'Частные сады',
    'seg3_holder': 'Заведующая', 'seg3_grade': 'X', 'seg3_reason': 'Непонятно, кто платит',
}
BRAINSTORM = {
    'sit1_doing': 'Разбирает шкаф после зимы', 'sit1_want': 'Освободить место', 'sit1_promise': 'Заберём малое',
    'sit2_doing': 'Готовится к прогулке', 'sit2_want': 'Не покупать коляску на полгода', 'sit2_promise': 'Коляска на сезон',
    'sit3_doing': 'Считает траты на ребёнка', 'sit3_want': 'Тратить меньше', 'sit3_promise': 'Одна подписка вместо покупок',
    'jtbd_functional_question': 'Во что одеть ребёнка?', 'jtbd_functional_value': 'Набор по размеру',
    'jtbd_social_question': 'Не стыдно ли в б/у?', 'jtbd_social_value': 'Вещи проверены и чистые',
    'jtbd_emotional_question': 'Я хорошая мама?', 'jtbd_emotional_value': 'Спокойствие за ребёнка',
}
VALUE = {
    'vp_audience': 'родителям малышей', 'vp_problem': 'вещами, из которых ребёнок вырос',
    'vp_method': 'подписки на прокат одежды', 'vp_result': 'нужный размер без лишних трат',
    'vp_analogy1': 'Каршеринг, но для детских вещей', 'vp_analogy2': 'Библиотека, но для колясок',
    'vp_analogy3': 'Гардероб, который растёт вместе с ребёнком',
    'vp_role': 'мама ребёнка до трёх лет', 'vp_want': 'одевать ребёнка по размеру',
    'vp_because': 'вещи малы через пару месяцев, а новые дорогие', 'vp_harm': 'деньги, время',
    'vp_harm_text': 'Каждый сезон покупает заново', 'jtbd_functional': 'Одеть ребёнка по сезону',
    'jtbd_social': 'Выглядеть заботливой мамой', 'jtbd_emotional': 'Не переживать из-за трат', 'vp_question': '',
}
UNKNOWN_VALUE = {
    'vp_audience': 'всем людям со смартфоном', 'vp_problem': 'усталостью от уведомлений',
    'vp_method': 'неизвестно', 'vp_result': 'неизвестно', 'vp_role': 'неизвестно',
    'vp_want': 'сосредоточиться на работе', 'vp_because': 'уведомления отвлекают', 'vp_harm': 'время',
    'vp_question': 'Кто именно ваш первый клиент: в какой ситуации он устаёт от уведомлений?',
}


def fake(value):
    def complete(prompt, payload, **kwargs):
        if 'Шаг «Сегментация' in prompt:
            return json.dumps(SEGMENTS, ensure_ascii=False)
        if 'Шаг «Брейншторм' in prompt:
            return json.dumps(BRAINSTORM, ensure_ascii=False)
        return json.dumps(value, ensure_ascii=False)
    return patch('founder.services.value_canvas.complete_text', side_effect=complete)


@override_settings(AI_PROVIDER='gigachat')
class ValueEvalTests(TestCase):
    def run_eval(self, *scenarios):
        out = Path(tempfile.mkdtemp()) / 'report.md'
        stdout = StringIO()
        args = [item for name in scenarios for item in ('-s', name)]
        call_command('bruno_eval', *args, '--out', str(out), stdout=stdout)
        return out.read_text(encoding='utf-8'), stdout.getvalue()

    def test_good_flat_answers_give_no_remarks_and_roll_back(self):
        with fake(VALUE):
            report, stdout = self.run_eval('value')
        self.assertIn('value: замечаний 0', stdout)
        self.assertIn('ответ: плоские ключи', report)
        self.assertIn('Формула: Наш продукт помогает родителям малышей', report)
        self.assertIn('JTBD Эмоциональная: Не переживать из-за трат', report)
        self.assertFalse(StartupProfile.objects.filter(name='КрохаШкаф').exists())  # Rolled back.
        self.assertFalse(ValueCanvas.objects.exists())

    def test_unknown_picture_scenario_expects_unknown_and_one_question(self):
        with fake(UNKNOWN_VALUE):
            report, stdout = self.run_eval('value_unknown')
        self.assertIn('value_unknown: замечаний 0', stdout)
        self.assertIn('помогает [неизвестно]', report)
        self.assertIn('Вопрос Бруно: ', report)
        self.assertNotIn('Сегменты ABCDX', report)  # This scenario skips segments.

    def test_remarks_for_nested_or_thin_answers(self):
        thin = {'result': {**VALUE, 'vp_analogy2': '', 'vp_analogy3': '', 'jtbd_social': 'Экономия 5000 рублей',
                           'vp_harm': 'усилия', 'vp_audience': 'всем родителям'}}
        with fake(thin):
            report, stdout = self.run_eval('value')
        self.assertIn('вложено (result)', report)
        self.assertIn('сравнений «X для Y» 1 < 2', report)
        self.assertIn('задач JTBD 2 < 3', report)  # The invented number dropped one job.
        self.assertIn('проблема не бьёт по «деньги»', report)
        self.assertIn('сегмент «все»: Наш продукт помогает всем родителям', report)  # Too broad for a segment.
        self.assertNotIn('value: замечаний 0', stdout)

    def test_model_question_on_ty_is_replaced_when_customer_is_everyone(self):
        wrong = {**UNKNOWN_VALUE, 'vp_audience': 'офисным сотрудникам', 'vp_question': 'Кто твой клиент? И сколько их?'}
        with fake(wrong):
            report, stdout = self.run_eval('value_unknown')
        # «офисным сотрудникам» нет в картине: Python всё равно пишет [неизвестно], а вопрос берёт свой.
        self.assertIn('помогает [неизвестно]', report)
        self.assertIn('«Все» — не сегмент', report)
        self.assertNotIn('твой', report)
        self.assertIn('value_unknown: замечаний 0', stdout)
