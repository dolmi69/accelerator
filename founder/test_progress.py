import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatMessage, ChatSession, StartupMetrics, StartupProfile, User
from founder.services.achievements import award_achievements
from founder.services.ai import system_prompt
from founder.services.metrics import AXES
from founder.services.radar_assessment import _assessment_context, _verified_evidence


@override_settings(AI_PROVIDER="demo")
class ProjectProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="progress", email="progress@example.com")
        cls.other = User.objects.create_user(username="private", email="private@example.com")
        cls.startup = StartupProfile.objects.create(owner=cls.user, name="Первый проект", problem="Ручная запись")
        cls.second = StartupProfile.objects.create(owner=cls.user, name="Второй проект")
        cls.private = StartupProfile.objects.create(owner=cls.other, name="Закрытый проект")

    def setUp(self):
        self.client.force_login(self.user)

    def snapshot(self, startup=None, score=30, source="ai", quote="Есть прототип", reason="Первые данные"):
        return StartupMetrics.objects.create(
            startup=startup or self.startup, source=source,
            **{key: score for key, _ in AXES},
            assessment_details={key: reason for key, _ in AXES},
            assessment_evidence={key: {"status": "stated", "quote": quote} for key, _ in AXES},
        )

    def test_saved_projects_carousel_shows_each_latest_radar_and_no_other_owner(self):
        self.snapshot(score=10)
        self.snapshot(score=75)
        self.snapshot(startup=self.private, score=99)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Первый проект")
        self.assertContains(page, "Второй проект")
        self.assertContains(page, "75/100")
        self.assertNotContains(page, "10/100")
        self.assertNotContains(page, "Закрытый проект")
        self.assertContains(page, 'data-carousel-prev')
        self.assertContains(page, 'data-carousel-next')
        self.assertContains(page, "Без оценки")
        self.assertEqual(len(page.context["project_cards"]), 2)

    def test_citations_reject_invented_quotes_assistant_and_other_project_sources(self):
        session = ChatSession.objects.create(startup=self.startup, mode="cofounder")
        question = ChatMessage.objects.create(session=session, role="assistant", content="А может, миллион продаж?")
        message = ChatMessage.objects.create(session=session, role="user", content="Есть MVP, планируем подписку.")
        _, sources = _assessment_context(self.startup, include_sources=True)
        answer = {key: {"evidence": {"status": "stated", "source_id": f"message:{message.id}", "quote": "Есть MVP"}}
                  for key, _ in AXES}
        answer["market"]["evidence"]["quote"] = "Есть миллион продаж"
        answer["finance"]["evidence"]["source_id"] = f"message:{question.id}"
        answer["team"]["evidence"]["source_id"] = "message:another-project"
        answer["pitch"]["evidence"] = {"status": "missing", "source_id": "", "quote": ""}
        evidence = _verified_evidence(json.dumps(answer), sources)
        self.assertEqual(evidence["product"]["quote"], "Есть MVP")
        self.assertEqual(evidence["product"]["session_id"], str(session.id))
        for key in ("market", "finance", "team"):
            self.assertEqual(evidence[key], {"status": "unlinked"})
        self.assertEqual(evidence["pitch"], {"status": "missing"})
        answer["product"]["evidence"]["source_id"] = f"[message:{message.id}]"
        evidence = _verified_evidence(json.dumps(answer), sources)
        self.assertEqual(evidence["product"]["source_id"], f"message:{message.id}")
        self.assertEqual(evidence["product"]["quote"], "Есть MVP")

    @override_settings(AI_PROVIDER="gigachat")
    def test_ai_assessment_saves_verified_sources_and_earns_badges_once(self):
        session = ChatSession.objects.create(startup=self.startup, mode="cofounder")
        message = ChatMessage.objects.create(session=session, role="user", content="Есть MVP для клиник, подписка, два разработчика.")
        answer = {key: {"score": 30, "reason": "Первые сведения", "evidence": {
            "status": "stated", "quote": message.content, "source_id": f"message:{message.id}",
        }} for key, _ in AXES}
        answer["summary"] = "Сведения со слов основателя."
        with patch("founder.services.radar_assessment.complete_text", return_value=json.dumps(answer)):
            page = self.client.post(reverse("metrics_assess", args=[self.startup.id]), follow=True)
        snapshot = self.startup.metric_snapshots.get()
        self.assertEqual(snapshot.assessment_evidence["team"]["quote"], message.content)
        self.assertContains(page, f"#message-{message.id}")
        self.assertContains(page, "Со слов основателя")
        award_achievements(self.startup, snapshot)
        self.assertEqual(self.startup.achievements.count(), 2)

    def test_refinement_keeps_axis_and_requires_owner_and_post(self):
        self.snapshot(reason="Цена ещё не определена")
        url = reverse("chat_refine", args=[self.startup.id, "finance"])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)
        session = self.startup.chat_sessions.get()
        self.assertEqual(session.focus_axis, "finance")
        self.assertIn("Финансы", system_prompt(session, []))
        self.assertIn("Цена ещё не определена", session.messages.get().content)
        self.assertEqual(self.client.post(url).url, response.url)
        self.assertEqual(self.startup.chat_sessions.count(), 1)
        self.assertEqual(self.client.post(reverse("chat_refine", args=[self.startup.id, "unknown"])).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_history_compares_stored_versions_and_does_not_change_current_radar(self):
        first = self.snapshot(score=20, reason="Есть идея")
        selected = self.snapshot(score=40, reason="Появился MVP")
        latest = self.snapshot(score=60)
        page = self.client.get(reverse("dashboard", args=[self.startup.id]), {"snapshot": selected.id})
        self.assertEqual(page.context["latest"].id, latest.id)
        self.assertEqual(page.context["history_previous"].id, first.id)
        self.assertEqual(page.context["history_rows"][0]["delta"], 20)
        self.assertContains(page, "Появился MVP")
        foreign = self.snapshot(startup=self.private)
        self.assertEqual(self.client.get(reverse("dashboard", args=[self.startup.id]), {"snapshot": foreign.id}).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard", args=[self.startup.id]), {"snapshot": "bad"}).status_code, 404)
        for i in range(7):
            self.snapshot(score=i)
        page = self.client.get(reverse("dashboard", args=[self.startup.id]), {"history_page": 2})
        self.assertEqual(page.context["history_page"].paginator.count, 10)
        self.assertEqual(len(page.context["history_page"]), 2)

    def test_manual_edit_invalidates_changed_citation_and_cannot_fake_progress_badge(self):
        self.snapshot()
        data = {key: 30 for key, _ in AXES}
        data.update({f"{key}_reason": "Первые данные" for key, _ in AXES})
        data.update(product=100, product_reason="Уточнённое описание")
        self.client.post(reverse("metrics_create", args=[self.startup.id]), data)
        snapshot = self.startup.metric_snapshots.first()
        self.assertEqual(snapshot.assessment_evidence["product"], {"status": "manual"})
        self.assertEqual(snapshot.assessment_evidence["market"]["quote"], "Есть прототип")
        self.assertTrue(self.startup.achievements.filter(code="refined").exists())
        self.assertFalse(self.startup.achievements.filter(code="progress").exists())

    def test_progress_requires_new_evidence_and_an_ai_score_increase(self):
        self.snapshot(score=20)
        same = self.snapshot(score=40)
        award_achievements(self.startup, same)
        self.assertFalse(self.startup.achievements.filter(code="progress").exists())
        improved = self.snapshot(score=55, quote="Получили первые три продажи")
        award_achievements(self.startup, improved)
        self.assertTrue(self.startup.achievements.filter(code="progress").exists())
