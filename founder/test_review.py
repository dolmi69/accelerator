import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import BrunoTask, ProjectReview, StartupProfile, User
from founder.services.ai import AIResponseFormatError
from founder.services.review import create_review, parse_review, step_to_task


def model_review(**overrides):
    data = {
        "stage": "validation",
        "stage_reason": "Есть лист ожидания на 60 человек, оплат пока нет.",
        "summary": "Подписка на кофе в кофейнях у дома.  Спрос на интерес есть, на оплату не проверен.",
        "strengths": ["Договорились с четырьмя кофейнями"],
        "risks": [{"axis": "finance", "title": "Маржа на чашку", "why": "Платим 90 ₽, а подписчик пьёт больше, чем ожидаем."}],
        "focus": "Получить 20 оплат подписки за месяц",
        "steps": [
            {"week": 2, "axis": "market", "title": "Предпродажи", "action": "Открыть оплату для листа ожидания.", "done_when": "Есть 20 оплат или отказы с причинами."},
            {"week": 1, "axis": "finance", "title": "Посчитать маржу", "action": "Свести цену и выплаты кофейням.", "done_when": "Есть расчёт на одного подписчика."},
            {"week": 3, "axis": "product", "title": "Ручной сервис", "action": "Выдавать кофе по QR из бота.", "done_when": "Десять человек получили кофе."},
        ],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class ParseReviewTests(TestCase):
    def test_valid_review_is_normalised_and_steps_sorted_by_week(self):
        data = parse_review(model_review())
        self.assertEqual([step["week"] for step in data["steps"]], [1, 2, 3])
        self.assertNotIn("  ", data["summary"])

    def test_incomplete_or_invalid_review_is_rejected(self):
        for raw in ("не json", model_review(stage="unicorn"), model_review(steps=[]),
                    model_review(risks=[{"axis": "magic", "title": "x", "why": "y"}])):
            with self.subTest(raw=raw[:30]), self.assertRaises(AIResponseFormatError):
                parse_review(raw)


class ReviewFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reviewer", email="reviewer@example.test")
        self.startup = StartupProfile.objects.create(
            owner=self.user, name="КофеПойнт", stage="validation",
            problem="Офисные сотрудники переплачивают за кофе", solution="Подписка на кофе",
        )
        self.client.force_login(self.user)

    @override_settings(AI_PROVIDER="gigachat")
    def test_review_retries_format_error_once_and_saves(self):
        with patch("founder.services.review.complete_text", side_effect=["{}", model_review()]) as call:
            review = create_review(self.startup)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(review.data["focus"], "Получить 20 оплат подписки за месяц")

    @override_settings(AI_PROVIDER="demo")
    def test_page_generate_and_step_to_task(self):
        page = self.client.get(reverse("review", args=[self.startup.pk]))
        self.assertContains(page, "Получить разбор")
        response = self.client.post(reverse("review_generate", args=[self.startup.pk]))
        self.assertRedirects(response, reverse("review", args=[self.startup.pk]))
        review = ProjectReview.objects.get()
        page = self.client.get(reverse("review", args=[self.startup.pk]))
        self.assertContains(page, review.data["steps"][0]["title"])
        url = reverse("review_step_task", args=[self.startup.pk, review.pk, 0])
        self.assertRedirects(self.client.post(url), reverse("tasks", args=[self.startup.pk]))
        task = BrunoTask.objects.get()
        self.assertEqual(task.title, review.data["steps"][0]["title"])
        # Второе задание по тому же направлению не создаётся.
        self.client.post(url)
        self.assertEqual(BrunoTask.objects.count(), 1)
        self.assertIsNone(step_to_task(review, 0))

    @override_settings(AI_PROVIDER="demo")
    def test_foreign_review_is_not_accessible(self):
        other = User.objects.create_user(username="other", email="other@example.test")
        foreign = StartupProfile.objects.create(owner=other, name="Чужой")
        review = create_review(foreign)
        self.assertEqual(self.client.get(reverse("review", args=[foreign.pk])).status_code, 404)
        url = reverse("review_step_task", args=[foreign.pk, review.pk, 0])
        self.assertEqual(self.client.post(url).status_code, 404)
        url = reverse("review_step_task", args=[self.startup.pk, review.pk, 0])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertFalse(BrunoTask.objects.exists())


class TolerantReviewParsingTests(TestCase):
    def test_weaker_model_shapes_are_normalised(self):
        raw = json.dumps({
            "stage": "Проверка", "stage_reason": "Лист ожидания без оплат.", "summary": "Подписка на кофе.",
            "strengths": [], "focus": "Первые оплаты",
            "risks": [{"risk": "Маржа отрицательная", "why": "Платим кофейням больше, чем берём."}],
            "steps": [
                {"step": "Week 2: Открыть предоплату для листа ожидания.", "done_when": "Есть 10 оплат."},
                {"step": "Неделя 1: Посчитать цену и затраты на подписчика.", "done_when": "Есть расчёт."},
                {"title": "Найти разработчика", "action": "Опубликовать вакансию.", "done_when": "Три отклика."},
            ],
        }, ensure_ascii=False)
        data = parse_review(raw)
        self.assertEqual(data["stage"], "validation")
        self.assertEqual(data["risks"][0], {"axis": "finance", "title": "Маржа отрицательная",
                                            "why": "Платим кофейням больше, чем берём."})
        self.assertEqual([(step["week"], step["axis"]) for step in data["steps"]],
                         [(1, "finance"), (2, "finance"), (3, "team")])
        self.assertEqual(data["steps"][1]["action"], "Открыть предоплату для листа ожидания.")
