"""Мастерская идей: разбор ответов модели, проверка чисел и источников, круг, страница и лимиты."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import BrunoTask, ChatSession, IdeaRound, ProjectActivity, ProjectCard, StartupProfile, User
from founder.services import ideas
from founder.services.ai import AIResponseFormatError, AIServiceError
from founder.services.web_search import SearchResult


SOURCES = [
    {"title": "Too Good To Go", "url": "https://toogoodtogo.com/", "domain": "toogoodtogo.com", "kind": "world",
     "snippet": "Приложение продаёт непроданную еду из кафе за треть цены.", "published": ""},
    {"title": "Отзывы студентов об общежитии", "url": "https://otzovik.com/obshaga", "domain": "otzovik.com",
     "kind": "pains", "snippet": "На кухне вечно нечего есть, продукты пропадают, соседи берут чужое. Ужин 350 рублей.",
     "published": ""},
]


TITLES = ["Ужин вскладчину по пятницам", "Кулинарный обмен остатками", "Старосты собирают заказ этажа"]


def direction(n, **changes):
    data = {
        f"dir{n}_title": TITLES[n - 1],
        f"dir{n}_technique": ["", "payer", "pain", "niche"][n],
        f"dir{n}_what": "Студенты этажа скидываются на общий ужин.",
        f"dir{n}_customer": "Первокурсник в общежитии, который ест лапшу.",
        f"dir{n}_payer": "Студент платит за ужин раз в неделю.",
        f"dir{n}_substitute": "Сейчас готовят сами или едят лапшу.",
        f"dir{n}_insight": "Продукты пропадают, а общий ужин делает их дешевле [2].",
        f"dir{n}_risk": "Соседи не договорятся о времени.",
        f"dir{n}_test": "Соберите 10 соседей на ужин в пятницу; успех — 6 оплатили заранее.",
        f"dir{n}_axis": "market", f"dir{n}_sources": "2",
    }
    data.update({f"dir{n}_{key}": value for key, value in changes.items()})
    return data


def analysis(**changes):
    data = {**direction(1), **direction(2), **direction(3), "pick": 2, "pick_why": "Её проще проверить за неделю.",
            "warning": "Похожее уже делает Too Good To Go [1], но не в общежитиях."}
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


SEED_JSON = json.dumps({"understanding": "Студенты в общежитии хотят есть дешевле и вкуснее",
                        "core": "Готовить одному дорого и скучно", "world_query": "food sharing students startup",
                        "russia_query": "совместные ужины студентов сервис", "pains_query": "общежитие кухня жалобы",
                        "trends_query": "фудшеринг тренды"}, ensure_ascii=False)
RAW = "\n".join(f"{n}. [Приём {n}] Идея номер {n}: суть идеи для студентов общежития." for n in range(1, 9))


class ParsingTests(TestCase):
    def test_fields_are_read_from_json_or_key_value_lines(self):
        self.assertEqual(ideas.load_fields('{"core": "боль"}', ["core"]), {"core": "боль"})
        lines = "dir1_title\nУжин вскладчину\n\ndir1_axis: market\npick\n2"
        self.assertEqual(ideas.load_fields(lines, ["dir1_title", "dir1_axis", "pick"]),
                         {"dir1_title": "Ужин вскладчину", "dir1_axis": "market", "pick": "2"})
        with self.assertRaises(ValueError):
            ideas.load_fields("просто текст", ["core"])

    def test_raw_ideas_keep_technique_and_text(self):
        found = ideas.parse_raw_ideas("Вот идеи:\n1. [Наоборот] Студенты кормят преподавателей: ужин за отзыв.\n"
                                      "2) **Сначала руками** без приёма\nне идея")
        self.assertEqual(found[0], {"technique": "Наоборот", "text": "Студенты кормят преподавателей: ужин за отзыв."})
        self.assertEqual(len(found), 2)

    def test_invented_numbers_bad_sources_and_informal_you_are_cleaned(self):
        allowed = ideas.allowed_numbers(SOURCES, ["ужины вскладчину"])
        raw = analysis(**{
            "dir1_insight": "Рынок фудшеринга вырос на 45% за год. Продукты пропадают, ты это видел [2].",
            "dir1_payer": "Студент платит 350 рублей за ужин.", "dir1_sources": "2, 7, 1",
            "dir2_what": "Отличная идея для старост. Старосты собирают заказы этажа.",
        })
        data = ideas.parse_analysis(raw, SOURCES, allowed)
        first = data["directions"][0]
        self.assertNotIn("45", first["insight"])
        self.assertIn("вы", first["insight"])
        self.assertNotIn(" ты ", f" {first['insight']} ")
        # Цена есть в источнике — остаётся; номер 7 не существует.
        self.assertIn("350", first["payer"])
        self.assertEqual(first["sources"], [2, 1])
        self.assertEqual(data["directions"][1]["what"], "Старосты собирают заказы этажа.")
        self.assertEqual(data["pick"], 2)

    def test_before_after_substitute_goes_to_critic_and_no_rivals_claim_is_fixed(self):
        self.assertEqual(ideas.weak_fields({"title": "Акт передачи вещей", "test": "Успех — трое оплатили.",
                                            "insight": "Студенты боятся обмана [2].",
                                            "substitute": "Теперь просто дают слово, теперь получают акт."}),
                         ["substitute"])
        self.assertEqual(ideas.weakness("substitute", "Сейчас выбрасывают или продают за копейки."), "")
        self.assertTrue(ideas.weakness("test", "Успех — пять студентов откликнулись и согласились оплатить аренду заранее."))
        self.assertTrue(ideas.weakness("test", "Успех — трое откликнулись на пост."))
        self.assertEqual(ideas.polish("Направление 'Хранитель-студент' проще."), "Направление «Хранитель-студент» проще.")
        advice = ("Выбор «Мебели напрокат» открывает перспективную нишу, свободную от конкурентов в России. "
                  "Однако стоит учесть риск низкого спроса. Начните с объявления в общежитии.")
        self.assertEqual(ideas.drop_fluff(advice), ("Стоит учесть риск низкого спроса. Начните с объявления в общежитии.",
                                                    True))
        self.assertEqual(ideas.drop_fluff("Начните с объявления."), ("Начните с объявления.", False))
        self.assertEqual(ideas.weakness("test", "Успех — пятеро откликнулись и внесли предоплату 300 ₽."), "")
        data = {"directions": [{"rivals": "Похожих сервисов нет, конкуренция отсутствует.", "sources": [1, 2]},
                               {"rivals": "Прямых конкурентов нет.", "sources": [2]}]}
        ideas.fix_rivals(data, SOURCES)
        self.assertIn("toogoodtogo.com", data["directions"][0]["rivals"])
        self.assertNotIn("нет", data["directions"][0]["rivals"])
        # Источник с жалобами — не конкурент: фразу не трогаем.
        self.assertEqual(data["directions"][1]["rivals"], "Прямых конкурентов нет.")

    def test_repeated_or_empty_directions_are_rejected(self):
        allowed = ideas.allowed_numbers(SOURCES, [])
        repeated = analysis(dir2_title="Ужин вскладчину по пятницам этажа", dir3_what="")
        with self.assertRaises(AIResponseFormatError):
            ideas.parse_analysis(repeated, SOURCES, allowed)
        # Модель сама признала, что направление не проверяется без вложений: его нет в круге.
        rejected = ideas.parse_analysis(analysis(dir3_test="Не подходит, так как требует вложений в прототип."),
                                        SOURCES, allowed)
        self.assertEqual([item["title"] for item in rejected["directions"]], TITLES[:2])
        # Направление из прошлого круга тоже не повторяется.
        data = ideas.parse_analysis(analysis(), SOURCES, allowed, previous=["Старосты собирают заказ этажа"])
        self.assertEqual(len(data["directions"]), 2)
        self.assertEqual(data["pick"], 2)


@override_settings(AI_PROVIDER="gigachat", MARKET_SEARCH="exa")
class RoundTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("ideas", email="ideas@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=user, name="Пока без названия")
        # Широкий поиск (Exa + DuckDuckGo) в тестах ведёт себя как подменённый в тесте search: сети нет.
        wide = patch("founder.services.ideas.search_wide", side_effect=lambda *args, **kwargs: ideas.search(*args, **kwargs))
        wide.start()
        self.addCleanup(wide.stop)

    def found(self, query, limit, objective=None):
        return {"food sharing students startup": [SearchResult("Too Good To Go", "https://toogoodtogo.com/",
                                                               SOURCES[0]["snippet"])],
                "общежитие кухня жалобы": [SearchResult("Отзывы студентов об общежитии", "https://otzovik.com/obshaga",
                                                        SOURCES[1]["snippet"])]}.get(query, [])

    def test_round_searches_by_kind_brainstorms_and_saves_directions(self):
        with patch("founder.services.ideas.complete_text", side_effect=[SEED_JSON, RAW, analysis()]) as model, \
                patch("founder.services.ideas.search", side_effect=self.found) as search:
            item = ideas.create_round(self.startup, "  еда   в общаге ", "дёшево")
        self.assertEqual(item.seed, "еда в общаге")
        self.assertEqual([source["kind"] for source in item.sources], ["world", "pains"])
        objectives = {call.args[0]: call.kwargs["objective"] for call in search.call_args_list}
        self.assertIn("worldwide", objectives["food sharing students startup"])
        self.assertEqual(len(item.data["raw_ideas"]), 8)
        self.assertEqual(len(item.data["directions"]), 3)
        self.assertEqual(item.data["understanding"], "Студенты в общежитии хотят есть дешевле и вкуснее")
        # Сырые идеи — смелее (высокая температура), разбор — по строгой схеме.
        self.assertEqual(model.call_args_list[1].kwargs["temperature"], ideas.DIVERGE_TEMPERATURE)
        self.assertIs(model.call_args_list[2].kwargs["json_schema"], ideas.ANALYZE_SCHEMA)
        self.assertIn("[1] (Стартапы в мире) Too Good To Go", model.call_args_list[2].args[1])
        self.assertIn("Пожелание основателя к этому кругу: дёшево", model.call_args_list[0].args[1])

    def test_development_round_knows_parent_and_previous_directions(self):
        with patch("founder.services.ideas.complete_text", side_effect=[SEED_JSON, RAW, analysis()]), \
                patch("founder.services.ideas.search", side_effect=self.found):
            first = ideas.create_round(self.startup, "еда в общаге")
        with patch("founder.services.ideas.complete_text", side_effect=["не json", RAW, analysis(
                dir1_title="Ужин по подписке для этажа", dir2_title="Кулинарный клуб с преподавателем",
                dir3_title="Общий холодильник с замком")]) as model, \
                patch("founder.services.ideas.search", return_value=[]):
            second = ideas.create_round(self.startup, "", "без денег", parent=first, parent_index=1)
        self.assertEqual(second.parent, first)
        self.assertTrue(second.seed.startswith("Кулинарный обмен остатками"))
        head = model.call_args_list[0].args[1]
        self.assertIn("развивает направление из прошлого круга", head)
        self.assertIn("Ты уже предлагал эти направления", head)
        # Разбор зерна не удался: запросы собраны по шаблону, круг всё равно готов.
        self.assertEqual(len(second.queries), 4)

    def test_without_seed_or_project_nothing_is_asked(self):
        with patch("founder.services.ideas.complete_text") as model, self.assertRaises(AIServiceError):
            ideas.create_round(self.startup, "   ")
        model.assert_not_called()

    def test_project_description_is_the_default_seed(self):
        self.startup.one_line_pitch = "Бот подсказывает студентам рецепт из того, что есть"
        self.startup.save()
        with patch("founder.services.ideas.complete_text", side_effect=[SEED_JSON, RAW, analysis()]), \
                patch("founder.services.ideas.search", return_value=[]):
            item = ideas.create_round(self.startup)
        self.assertEqual(item.seed, "Бот подсказывает студентам рецепт из того, что есть")

    def test_critic_rewrites_opinion_tests_and_empty_insights_only(self):
        weak = analysis(dir1_test="Опросить 20 соседей, заплатили бы они за ужин.",
                        dir2_insight="Многие люди хотят есть вкусно, тренд растёт.")
        fixed = json.dumps({"dir1_test": "Позвать 10 соседей на ужин в пятницу с предоплатой; успех — шестеро оплатили.",
                            "dir2_insight": "Многие люди любят ужины."}, ensure_ascii=False)
        with patch("founder.services.ideas.complete_text", side_effect=[SEED_JSON, RAW, weak, fixed]) as model, \
                patch("founder.services.ideas.search", return_value=[]):
            item = ideas.create_round(self.startup, "еда")
        first, second = item.data["directions"][:2]
        self.assertIn("предоплатой", first["test"])
        # Переписанная мысль снова из общих слов: остаётся прежняя, критик её не принял.
        self.assertIn("тренд растёт", second["insight"])
        self.assertEqual(item.data["critic"], 1)
        self.assertEqual(set(model.call_args_list[3].kwargs["json_schema"]["required"]), {"dir1_test", "dir2_insight"})

    def test_empty_warning_is_asked_separately_and_english_titles_go_to_the_critic(self):
        raw = analysis(warning="", dir1_title="FoodSwap Club")
        fixed = json.dumps({"dir1_title": "Обмен продуктами на этаже"}, ensure_ascii=False)
        trap = json.dumps({"warning": "Ты не первый: похожий сервис закрылся из-за пустых полок [2]."},
                          ensure_ascii=False)
        with patch("founder.services.ideas.complete_text", side_effect=[SEED_JSON, RAW, raw, fixed, trap]) as model, \
                patch("founder.services.ideas.search", side_effect=self.found):
            item = ideas.create_round(self.startup, "еда в общаге")
        self.assertEqual(item.data["directions"][0]["title"], "Обмен продуктами на этаже")
        self.assertIn("закрылся из-за пустых полок [2]", item.data["warning"])
        self.assertTrue(item.data["warning"].startswith("Вы не первый"))
        self.assertIs(model.call_args_list[4].kwargs["json_schema"], ideas.TRAP_SCHEMA)
        self.assertIn("(Жалобы и обсуждения)", model.call_args_list[4].args[1])

    def test_broken_analysis_is_retried_once_with_a_reminder(self):
        with patch("founder.services.ideas.complete_text",
                   side_effect=[SEED_JSON, RAW, analysis(dir1_title="", dir2_title=""), analysis()]) as model, \
                patch("founder.services.ideas.search", return_value=[]):
            item = ideas.create_round(self.startup, "еда")
        self.assertEqual(len(item.data["directions"]), 3)
        self.assertIn("Прошлый ответ не прошёл проверку", model.call_args_list[3].args[0])


@override_settings(AI_PROVIDER="demo")
class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ideaspage", email="ideaspage@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Кофе у кампуса")
        self.client.force_login(self.user)

    def test_workshop_and_tasks_are_not_in_the_project_menu(self):
        response = self.client.get(reverse("dashboard", args=[self.startup.pk]))
        self.assertNotContains(response, f'href="{reverse("ideas", args=[self.startup.pk])}"')
        # Работа с заданиями объединена в актуальном разделе «Работа над проектом».
        self.assertNotContains(response, 'aria-label="Задания Бруно"')
        self.assertNotContains(response, 'aria-label="Мастерская идей"')
        self.assertContains(response, 'aria-label="Работа над проектом"')

    def test_project_page_still_works_and_demo_round_is_created(self):
        response = self.client.get(reverse("ideas", args=[self.startup.pk]))
        self.assertContains(response, "Мастерская идей")
        response = self.client.post(reverse("ideas_generate", args=[self.startup.pk]), {"seed": "кофе"}, follow=True)
        item = IdeaRound.objects.get(startup=self.startup)
        self.assertContains(response, "Сначала руками для пяти знакомых")
        self.assertContains(response, "Бруно советует")
        self.assertTrue(ProjectActivity.objects.filter(startup=self.startup, kind="ideas").exists())
        # Развить дальше: новый круг от направления.
        self.client.post(reverse("ideas_generate", args=[self.startup.pk]),
                         {"parent": str(item.pk), "index": "1", "wish": "для родителей"})
        child = IdeaRound.objects.exclude(pk=item.pk).get()
        self.assertEqual((child.parent, child.parent_index, child.wish), (item, 1, "для родителей"))
        bad = self.client.post(reverse("ideas_generate", args=[self.startup.pk]), {"parent": str(item.pk), "index": "9"})
        self.assertEqual(bad.status_code, 404)

    def test_direction_becomes_a_task_once_per_axis(self):
        item = IdeaRound.objects.create(startup=self.startup, seed="кофе", data=ideas.demo_round("кофе"))
        response = self.client.post(reverse("ideas_direction_task", args=[self.startup.pk, item.pk, 0]))
        self.assertRedirects(response, reverse("tasks", args=[self.startup.pk]))
        task = BrunoTask.objects.get(startup=self.startup)
        self.assertEqual(task.title, "Сначала руками для пяти знакомых")
        self.assertIn("Как проверить", task.instructions)
        self.client.post(reverse("ideas_direction_task", args=[self.startup.pk, item.pk, 0]))
        self.assertEqual(BrunoTask.objects.filter(startup=self.startup).count(), 1)

    def test_other_founder_cannot_open_or_generate(self):
        other = User.objects.create_user("otherideas", email="otherideas@example.test", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("ideas", args=[self.startup.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("ideas_generate", args=[self.startup.pk])).status_code, 404)

    @override_settings(AI_REQUESTS_PER_MINUTE=1)
    def test_generation_counts_against_ai_limits(self):
        with patch("founder.services.ideas.create_round", side_effect=AIServiceError("нет")):
            self.assertEqual(self.client.post(reverse("ideas_generate", args=[self.startup.pk])).status_code, 302)
            self.assertEqual(self.client.post(reverse("ideas_generate", args=[self.startup.pk])).status_code, 429)


@override_settings(AI_PROVIDER="demo")
class CreateFromIdeaTests(TestCase):
    """Второй способ создать проект: развить идею с Бруно, одобрить карточку, получить проект."""

    def setUp(self):
        self.user = User.objects.create_user("newidea", email="newidea@example.test", password="x")
        self.client.force_login(self.user)

    def generate(self, **data):
        return self.client.post(reverse("idea_new_generate"), {"seed": "кофе", **data})

    def test_create_page_offers_two_ways(self):
        response = self.client.get(reverse("startup_create"))
        self.assertContains(response, "Заполнить анкету")
        self.assertContains(response, "Развить идею с Бруно")
        self.assertContains(response, "Создать и поговорить с Бруно")
        response = self.client.get(reverse("startup_create") + "?way=idea")
        self.assertContains(response, "С чего начать")
        self.assertContains(response, reverse("idea_new_generate"))
        self.assertNotContains(response, "Создать и поговорить с Бруно")

    def test_round_without_project_belongs_to_its_owner(self):
        response = self.generate()
        item = IdeaRound.objects.get()
        self.assertEqual((item.owner, item.startup), (self.user, None))
        self.assertRedirects(response, f"{reverse('startup_create')}?way=idea&id={item.pk}")
        page = self.client.get(f"{reverse('startup_create')}?way=idea&id={item.pk}")
        self.assertContains(page, "Выбрать эту идею")
        self.assertContains(page, "Бруно советует")
        self.assertContains(page, reverse("idea_new_advice", args=[item.pk, 1]))
        # Развить дальше на странице создания: новый круг, тоже без проекта.
        self.generate(seed="", parent=str(item.pk), index="0", wish="для родителей")
        child = IdeaRound.objects.exclude(pk=item.pk).get()
        self.assertEqual((child.parent, child.owner, child.startup), (item, self.user, None))
        # Чужой основатель не видит круг и не может составить по нему карточку.
        other = User.objects.create_user("stranger", email="stranger@example.test", password="x")
        self.client.force_login(other)
        self.assertNotContains(self.client.get(f"{reverse('startup_create')}?way=idea"), "Сначала руками")
        card = reverse("idea_new_card", args=[item.pk, 0])
        self.assertEqual(self.client.post(card, {"action": "draft"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("idea_new_generate"), {"parent": str(item.pk), "index": "0"}).status_code, 404)

    def test_card_is_drafted_and_project_appears_only_after_approval(self):
        self.generate()
        item = IdeaRound.objects.get()
        card = reverse("idea_new_card", args=[item.pk, 0])
        self.assertRedirects(self.client.post(card, {"action": "draft"}), card)
        self.assertEqual(StartupProfile.objects.count(), 0)
        page = self.client.get(card)
        self.assertContains(page, "Одобрить и создать проект")
        self.assertContains(page, "Сначала руками для пяти знакомых")
        values = {"name": "Кофе руками", "tagline": "Кофе для пяти знакомых", "problem": "Нет времени",
                  "solution": "Варю сам", "audience": "Соседи по этажу", "business_model": "Платят за чашку",
                  "summary": "Проверяем руками", "instruction": ""}
        response = self.client.post(card, {**values, "action": "create"})
        startup = StartupProfile.objects.get(owner=self.user)
        self.assertTrue(ChatSession.objects.filter(startup=startup).exists())
        # После одобрения Бруно сразу оценивает проект по радару и открывает радар.
        self.assertRedirects(response, reverse("dashboard", args=[startup.pk]), fetch_redirect_response=False)
        picture = startup.picture.facts
        self.assertEqual(picture["money"], {"text": "Платят за чашку", "status": "guess"})
        self.assertEqual((startup.name, startup.one_line_pitch, startup.target_customer, startup.stage),
                         ("Кофе руками", "Кофе для пяти знакомых", "Соседи по этажу", "idea"))
        project_card = ProjectCard.objects.get(startup=startup)
        self.assertEqual(project_card.business_model, "Платят за чашку")
        self.assertIsNone(project_card.published_at)
        item.refresh_from_db()
        self.assertEqual(item.startup, startup)
        self.assertTrue(ProjectActivity.objects.filter(startup=startup, kind="ideas").exists())
        # Круг уже в проекте: на странице создания его нет, повторно создать проект из него нельзя.
        self.assertEqual(self.client.post(card, {**values, "action": "create"}).status_code, 404)

    def test_card_without_name_is_not_approved(self):
        self.generate()
        item = IdeaRound.objects.get()
        card = reverse("idea_new_card", args=[item.pk, 0])
        response = self.client.post(card, {"name": "", "action": "create"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StartupProfile.objects.count(), 0)

    @override_settings(AI_PROVIDER="gigachat")
    def test_bruno_refines_the_card_on_request_and_keeps_russian_name(self):
        item = IdeaRound.objects.create(owner=self.user, seed="кофе", data=ideas.demo_round("кофе"))
        card = reverse("idea_new_card", args=[item.pk, 0])
        answer = json.dumps({"name": "CoffeeHands", "tagline": "Кофе для соседей по этажу", "problem": "Утром нет кофе",
                             "solution": "Варим на этаже", "audience": "Первокурсники", "business_model": "Чашка 300 ₽",
                             "summary": "Кофе на этаже"}, ensure_ascii=False)
        values = {**ideas.card_from_direction(item.data["directions"][0]), "name": "Кофе на этаже"}
        with patch("founder.services.ideas.complete_text", return_value=answer) as model:
            self.client.post(card, {**values, "instruction": "клиент — первокурсники", "action": "refine"})
        self.assertIn("Основатель просит поправить карточку: клиент — первокурсники", model.call_args.args[1])
        stored = ideas.stored_card(IdeaRound.objects.get(pk=item.pk), 0)
        # Английское название не берём, остаётся то, что было в карточке; выдуманная цена вырезана,
        # и поле сохраняет прежний текст.
        self.assertEqual(stored["name"], "Кофе на этаже")
        self.assertEqual(stored["audience"], "Первокурсники")
        self.assertEqual(stored["business_model"], values["business_model"])
        self.assertEqual(StartupProfile.objects.count(), 0)

    @override_settings(AI_REQUESTS_PER_MINUTE=1)
    def test_workshop_without_project_counts_against_ai_limits(self):
        with patch("founder.services.ideas.create_round", side_effect=AIServiceError("нет")):
            self.assertEqual(self.generate().status_code, 302)
            self.assertEqual(self.generate().status_code, 429)


class DisplayTests(TestCase):
    def test_source_numbers_are_removed_from_text(self):
        self.assertEqual(ideas.strip_refs("Источник [3]: студенты жалуются на кухню."), "Студенты жалуются на кухню.")
        self.assertEqual(ideas.strip_refs("[1], [6]: аналоги работают."), "Аналоги работают.")
        self.assertEqual(ideas.strip_refs("Живём.ру [2], Flatmate [12] и Homeet (источник [7])."),
                         "Живём.ру, Flatmate и Homeet.")
        self.assertEqual(ideas.strip_refs("Цена 350 рублей [2][5]."), "Цена 350 рублей.")

    def test_only_competitor_sources_are_shown(self):
        item = IdeaRound(seed="еда", sources=SOURCES, data={"directions": [{"sources": [1, 2]}]})
        self.assertEqual([source["domain"] for source in ideas.competitor_sources(item, item.data["directions"][0])],
                         ["toogoodtogo.com"])


@override_settings(AI_PROVIDER="gigachat")
class AdviceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("advice", email="advice@example.test", password="x")
        data = ideas.parse_analysis(analysis(), SOURCES, ideas.allowed_numbers(SOURCES, []))
        self.item = IdeaRound.objects.create(owner=self.user, seed="еда в общаге", data=data, sources=SOURCES)
        self.client.force_login(self.user)

    def test_recommended_idea_is_explained_without_asking_the_model(self):
        with patch("founder.services.ideas.complete_text") as model:
            result = ideas.advise(self.item, 1)
        model.assert_not_called()
        self.assertEqual(result, {"advice": "Её проще проверить за неделю.", "pick": 2})

    def test_bruno_analyses_another_idea_and_may_change_his_advice(self):
        keep = json.dumps({"advice": "Ты получишь соседей, но я бы начал со второй [2]: её проще проверить.",
                           "recommend": 2}, ensure_ascii=False)
        with patch("founder.services.ideas.complete_text", return_value=keep):
            response = self.client.post(reverse("idea_new_advice", args=[self.item.pk, 0]))
        self.assertEqual(response.json(), {"advice": "Вы получите соседей, но я бы начал со второй: её проще проверить.",
                                           "pick": 2})
        switch = json.dumps({"advice": "Эта сильнее: старосты уже собирают деньги этажа.", "recommend": 3},
                            ensure_ascii=False)
        with patch("founder.services.ideas.complete_text", return_value=switch):
            result = self.client.post(reverse("idea_new_advice", args=[self.item.pk, 2])).json()
        self.assertEqual(result["pick"], 3)
        self.item.refresh_from_db()
        self.assertEqual(self.item.data["pick"], 3)
        # Прежний совет остаётся пояснением у прежней идеи, повторный выбор модель не спрашивает.
        self.assertEqual(ideas.advice_for(self.item, 1), "Её проще проверить за неделю.")
        with patch("founder.services.ideas.complete_text") as model:
            self.assertEqual(self.client.post(reverse("idea_new_advice", args=[self.item.pk, 0])).json()["pick"], 3)
        model.assert_not_called()

    def test_failed_advice_is_reported_and_other_founder_gets_404(self):
        with patch("founder.services.ideas.complete_text", side_effect=AIServiceError("нет")):
            response = self.client.post(reverse("idea_new_advice", args=[self.item.pk, 0]))
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())
        other = User.objects.create_user("advice2", email="advice2@example.test", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("idea_new_advice", args=[self.item.pk, 0])).status_code, 404)
