# Дизайн сайтов акселераторов и фондов: YC, Techstars, Antler, EF, ФРИИ, a16z speedrun

Метод: 2026-10-26 скачаны HTML и CSS главных страниц (curl), шрифты, радиусы и цвета посчитаны по частоте в CSS; структура страниц снята через WebFetch. Скриншотов не было: числа из CSS показывают, что есть в стилях, а не что видно на экране.

## 1. Структура страницы и доказательства

### Takeaway
Сильнее всех YC: доказательство — это лица основателей «тогда и сейчас» плюс одна цифра на карточку; EF объясняет программу простыми списками без логотипов. ФРИИ показывает цифры каруселью с перепутанными подписями и не объясняет условия.

### Cited Findings
- YC: первый экран — одна фраза «YC turns builders into formidable founders» со сноской-цитатой Пола Грэма, без подзаголовка; кнопка «Apply» только в шапке и баннере со сроком (Winter 2027, до 2 ноября) — [ycombinator.com](https://www.ycombinator.com/)
- YC: дальше 13 карточек компаний с фото «During YC» и «Now» и одной цифрой оценки; полоса логотипов с «$1.3 Trillion»; описание программы ($500k, 4 раза в год, 3 месяца); 5 цитат основателей с фото; «All partners were YC founders first» (20 партнёров); в конце «It's never too early to apply» — [ycombinator.com](https://www.ycombinator.com/)
- YC (видео «How We Redesigned Our Website», 30.01.2026, А. Эпштейн и Э. Буффар, по пересказу): цель «вдохновлять, а не продавать»; старый сайт был похож на шаблон B2B SaaS, показывал логотипы вместо людей; описание YC оставили словами Грэма — [summify.io (пересказ видео)](https://summify.io/discover/how-we-redesigned-our-website-K5JoLA/)
- Techstars: «In a World of Noise, the Right Network Is Everything.», две кнопки («Apply Today», «Review $220K Investment Terms»); 14 блоков (логотипы, новости, вертикали, 16 программ, менторы, блог, книга); цифры 11 171 основатель, 29 единорогов, $56B — [techstars.com](https://www.techstars.com/)
- Antler: «Further —Faster», одна кнопка «Pitch Us», карусель карточек портфеля; в историях основателей у каждой карточки конкретная сумма («$73.5M Series B», «2 million monthly users»); на одной странице «30 locations» и «26 locations» — [antler.co](https://www.antler.co/)
- EF: «FOUND, DON'T FOLLOW»; программа — пять блоков «We …» со списками (кофаундеры, путь «от идеи до первой выручки», деньги), портфель и инвесторы текстом без картинок-логотипов, блок «Interested?» со списком требований к кандидату — [joinef.com](https://www.joinef.com/)
- speedrun: «Your first believers»; «What you get» тремя столбцами (капитал / клиенты / найм) с цифрами и единицами (до $1M, закрытие за 2 недели, комиссии $0); FAQ из 6 конкретных вопросов («How fast do you decide?») — [speedrun.a16z.com](https://speedrun.a16z.com/)
- ФРИИ: «Крупнейший венчурный фонд в России для IT-стартапов», кнопка «Хочу инвестиции»; цифры в карусели, подписи в разметке перепутаны; три карточки 01–03 («Лучшая программа акселерации в России») без пояснений; новости без года; есть баннер «бесплатный ИИ-аналитик презентаций» — [iidf.ru](https://iidf.ru/)

### Inferences
- Рабочий шаблон для доказательств: одна цифра с единицей и сроком на карточку (YC, Antler) вместо стены из 4–6 метрик.
- У ФРИИ уже есть бесплатный ИИ-разбор презентаций: питч-режим Bruno Lab стоит позиционировать через тренировку разговора (акулы), а не через «разбор слайдов».

### Gaps
- Не проверено, как первый экран YC и Antler выглядит на 375 px (скриншотов не было).

## 2. Типографика

### Takeaway
Выделяются YC (антиква Source Serif 4 в заголовках и даже в кнопке курсивом + гротеск Outfit для навигации) и speedrun (узкий Messina Sans Condensed с плотным трекингом). Остальные берут нейтральный гротеск.

### Cited Findings
- YC: Google Fonts подключают Source Serif 4 и DM Sans; в разметке корневой блок `font-['Source_Serif_4',serif]`, заголовок `leading-[1.2]`, кнопка — чёрная «таблетка» `rounded-full` с `font-['Source_Serif_4'] italic`, навигация `font-['Outfit'] text-sm tracking-[0.3px]` — [ycombinator.com (HTML)](https://www.ycombinator.com/)
- Сторонний «design.md» называет у YC Verlag и Verdana — живой CSS это не подтверждает — [webdesignhot.com](https://www.webdesignhot.com/design.md/ycombinator/); против [ycombinator.com (HTML)](https://www.ycombinator.com/)
- Techstars: Suisse Intl (Regular 60 правил, Semibold 28, Bold 1) + JetBrains Mono; самый частый трекинг 0.15px, в крупном −0.02em — [techstars.com (inline CSS)](https://www.techstars.com/)
- Antler: основной Satoshi (400/500/700), второй — антиква Nib Pro (фолбэк Georgia); размеры через `clamp` (display 2→4.5rem, h1 2→3.5rem, h2 1.65→3rem), трекинг −0.03em, интерлиньяж 1.15/1.3/1.4, `text-wrap: pretty`, обрезка полей шрифта (trim-top/bottom) — [antler CSS](https://cdn.prod.website-files.com/68c1084e137e63873a526f0f/css/antler-lumos.shared.a6598e193.min.css)
- EF: через Typekit только neue-haas-grotesk-display 600; заголовки и кнопки капсом — [use.typekit.net/ihl0alr.css](https://use.typekit.net/ihl0alr.css), [joinef.com](https://www.joinef.com/)
- speedrun: Messina Sans + Messina Sans Condensed, трекинг −0.03…−0.05em, капс-подписи +0.06em — [speedrun CSS](https://speedrun.a16z.com/_next/static/css/3a3fa0886f53205e.css)
- ФРИИ: Museo Sans Cyrl — [iidf.ru CSS](https://iidf.ru/local/templates/main_new/assets/css/app.min.css?v=1703502599)

### Inferences
- Пара «антиква для голоса + гротеск для интерфейса» (YC, Antler) подходит «Тетради наставника»: антиква для реплик и выводов Бруно, Golos Text для интерфейса. Нужна антиква с хорошей кириллицей; поддержку у Source Serif 4 надо проверить перед выбором.

### Gaps
- Длину строки (max-width текста) и полную шкалу кеглей не извлекал; у EF не нашёл CSS темы (WordPress), радиусы и размеры неизвестны.

## 3. Плотность, сетка, границы, радиусы

### Takeaway
YC и speedrun держат маленькие радиусы и почти без теней; Techstars тянет тени Material UI. На мобильном YC делает горизонтальную ленту карточек с привязкой прокрутки.

### Cited Findings
- YC: карточки `aspect-[4/5] w-[85vw] max-w-[360px] snap-center rounded-xl`; в редизайне отказались от рамок и жёстких разделителей, чтобы контент «плавал» — [ycombinator.com (HTML)](https://www.ycombinator.com/), [summify.io](https://summify.io/discover/how-we-redesigned-our-website-K5JoLA/)
- Techstars: радиусы 4px, 16px и «таблетки» 9999px; тени `0 1px 2px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.06)` и `0px 3px 1px -2px rgba(0,0,0,0.2)…` (формула теней Material UI) — [techstars.com (inline CSS)](https://www.techstars.com/)
- Antler: `--radius--main .5rem`, `--radius--small .1875rem`, теней почти нет (`box-shadow:none` ×8) — [antler CSS](https://cdn.prod.website-files.com/68c1084e137e63873a526f0f/css/antler-lumos.shared.a6598e193.min.css)
- speedrun: 6px / .375rem / .625rem — [speedrun CSS](https://speedrun.a16z.com/_next/static/css/3a3fa0886f53205e.css)
- ФРИИ: крупные радиусы 2.4rem и 2.8rem; версия CSS `v=1703502599` (25.12.2023) — [iidf.ru CSS](https://iidf.ru/local/templates/main_new/assets/css/app.min.css?v=1703502599)

### Inferences
- Для 375 px лента YC (85vw, привязка к центру) — готовый образец для «Было/Стало» и карточек идей; тени лучше заменить тонкой линией бумаги, как у Antler и YC.

### Gaps
- Не измерял отступы между секциями.

## 4. Цвет и графические мотивы

### Takeaway
Тёплая бумажная гамма есть у YC и Antler, и обе прячут яркий цвет до взаимодействия. Узнаваемость YC держится на оранжевом и фото людей, не на иллюстрациях.

### Cited Findings
- YC: самые частые цвета в общем CSS-бандле — тёплый почти чёрный #16140f (105), оранжевый #fb651e (61), тёплый белый #fdfdf8; в разметке главной текст `text-[#16140f]`, выделение текста чёрным — [YC CSS](https://bookface-static.ycombinator.com/vite/assets/ycdc-new-8-4CqYVT.css), [ycombinator.com](https://www.ycombinator.com/)
- Antler: светлый фон #f9f7f5, бледная мята #baded8 как фон секций, тёмный #0c1f21; красный #ed4746 только на наведении основной кнопки — [antler CSS](https://cdn.prod.website-files.com/68c1084e137e63873a526f0f/css/antler-lumos.shared.a6598e193.min.css)
- Techstars: зелёный #1f6f43 и #0f4d2c на белом; последний найденный пост о ребрендинге — 12.03.2020 (текущая гамма может быть позже) — [techstars.com](https://www.techstars.com/), [newsroom](https://www.techstars.com/newsroom/techstars-has-a-new-look)
- speedrun: голубой #57c5f7/#96dbfa и фиолетовый #7524ff — [speedrun CSS](https://speedrun.a16z.com/_next/static/css/3a3fa0886f53205e.css); ФРИИ: голубой #00aff0 на #171717 — [iidf.ru CSS](https://iidf.ru/local/templates/main_new/assets/css/app.min.css?v=1703502599)
- Antler: ребрендинг делала Superside (декабрь 2023, известно только по выдаче поиска) — [superside.com](https://www.superside.com/blog/antler-case-study)

### Inferences
- Палитра Bruno Lab (#f5f4ef, чернила #122338) по логике ближе к YC и Antler; их приём «акцент только на действии» совпадает с правилом «абрикос только для медведя».

## 5. Движение

### Takeaway
Осмысленное: «маркер» по ключевой фразе (Techstars), бегущий счётчик единорогов, ожившие фото «тогда» у YC. Декоративное: призмы, вращающиеся рамки и бегущие строки speedrun.

### Cited Findings
- YC: наведение — только прозрачность (`hover:opacity-60/80`), `transition-transform duration-300` у карточек; ранние фото партнёров и основателей оживлены с помощью ИИ — [ycombinator.com (HTML)](https://www.ycombinator.com/), [summify.io](https://summify.io/discover/how-we-redesigned-our-website-K5JoLA/)
- Techstars: анимации `highlightMark` и `unicornTicker` — [techstars.com (inline CSS)](https://www.techstars.com/)
- Antler: GSAP + ScrollTrigger + Lenis (плавная прокрутка) + Swiper — [antler.co (HTML)](https://www.antler.co/)
- speedrun: `PrismReveal` (light/travel/wipe), `border-spin`, `cardReveal`, `marqueeScrollDown`, `blink-animation` — [speedrun CSS](https://speedrun.a16z.com/_next/static/css/3a3fa0886f53205e.css)

### Inferences
- Lenis перехватывает прокрутку; в WebView Telegram это может спорить со свайпом закрытия Mini App — Bruno Lab лучше остаться на нативной прокрутке.

### Gaps
- Переходы между страницами вживую не смотрел.

## 6. Ручная работа или шаблон

### Takeaway
По-человечески выглядят YC (реальные лица, сноска Грэма, текст основателей) и EF (голос и списки вместо иконок). Шаблонно — ФРИИ (01–03 «лучшая программа», карусель цифр) и Techstars (тени Material UI, ленты новостей и блога).

### Cited Findings
- Команда YC сама называла старый сайт похожим на шаблон B2B SaaS и переделала его вокруг лиц основателей — [summify.io](https://summify.io/discover/how-we-redesigned-our-website-K5JoLA/)
- EF: тема WordPress `joinef2023`, jQuery, Font Awesome; портфель и наставники текстом без логотипов и фото — [joinef.com (HTML)](https://www.joinef.com/)
- Antler собран на Webflow с фреймворком Lumos; две разные цифры локаций на одной странице — [antler.co](https://www.antler.co/)

### Inferences
- Признак «ручной» работы — конкретика (имя, сумма, срок, фото «тогда»), а не эффекты: у speedrun эффектов больше всех, но человечнее YC и EF.

## 7. Что взять Bruno Lab и чего избегать

### Takeaway
Взять «Было/Стало», антикву для голоса Бруно, маркер-выделение и ленту на 85vw; избегать карусели цифр, нумерованных карточек преимуществ и декоративных эффектов.

### Cited Findings
- Источники приёмов — [ycombinator.com](https://www.ycombinator.com/), [techstars.com](https://www.techstars.com/), [antler CSS](https://cdn.prod.website-files.com/68c1084e137e63873a526f0f/css/antler-lumos.shared.a6598e193.min.css), [joinef.com](https://www.joinef.com/), [iidf.ru](https://iidf.ru/), [speedrun.a16z.com](https://speedrun.a16z.com/)

### Inferences
- Взять 1 (YC «During YC / Now»): карточка «Было → Стало» по проекту — первая версия идеи и радара против текущей, с одной цифрой-доказательством из дневника.
- Взять 2 (YC, Antler Nib Pro): антиква для реплик, выводов и «Подготовки к куратору», Manrope/Golos для интерфейса; кнопку-«таблетку» не повторять, курсив YC узнаваем.
- Взять 3 (Techstars `highlightMark`): один раз проявить маркерное выделение главного пробела или вывода в «тетради», с учётом reduced motion.
- Взять 4 (EF «Interested?», speedrun FAQ): честные короткие списки «что вы получите / что нужно от вас» и вопросы вида «Сколько ждать ответа Бруно?» вместо карточек-преимуществ.
- Избегать 1 (ФРИИ, Techstars): карусели и стены метрик без контекста, карточки 01–03 с общими словами, тени Material UI.
- Избегать 2 (speedrun, Antler): призмы, вращающиеся рамки, бегущие строки и плавная прокрутка на JS в Mini App.

### Gaps
- Российские вузовские инкубаторы не смотрел (лимит времени).
