# Признаки «AI slop» в дизайне веб-интерфейсов (2025–2026) и как их избегать

Источники собраны 2026-10-26. Пометки: **[широко]** — 3+ независимых источника; **[1 ист.]** — мнение одного источника. Главный количественный источник — замер Адриана Кребса по Show HN (детерминированные DOM/CSS-проверки в Playwright, без LLM). Остальное — гайды и скиллы.

## 1. Визуальные признаки AI-сайта: какие подтверждаются и что встречается чаще всего

### Takeaway
Ни один признак сам по себе ничего не доказывает, но вместе они образуют узнаваемый «отпечаток». Чаще всего называют фиолетовые/индиго градиенты, Inter везде, сетку одинаковых карточек с иконкой, цветную полосу слева у карточки, uppercase-надписи над секциями, плашку над H1, баннер с цифрами и glassmorphism. С 2026 года отдельно описана «вторая волна», которая появляется, когда первую запрещают: кремовый фон + serif + терракота, почти чёрный фон + кислотный акцент, моноширинные подписи.

### Cited Findings
- Кребс прогнал 1 590 страниц Show HN: у 22% 4+ паттернов, у 32% 2–3, у 46% 0–1. Ложных срабатываний 5–10%. Его вывод: сайты от этого «uninspired», а не обязательно плохие — [Krebs](https://www.adriankrebs.ch/blog/design-slop/). Gigazine пересказывает другие цифры (500 сайтов, 15 паттернов, 21/46/33%), похоже, по ранней версии поста: [Gigazine](https://www.gigazine.net/gsc_news/en/20260423-design-slop) **противоречит** [Krebs](https://www.adriankrebs.ch/blog/design-slop/).
- Доля сайтов с паттерном (по Gigazine): градиенты в фоне или заголовке 28%; shadcn/ui без изменений 23,5%; заголовок по центру 23,5%; вечная тёмная тема + серый текст + uppercase-подписи 20,3%; сетка одинаковых карточек с иконкой 20,1%; стеклянные размытые панели 17,1%; Space Grotesk / Instrument Serif / Geist / Syne / Fraunces 15,8%; цветная полоса сверху или слева у карточек 13%; баннер с цифрами 12,2%; фиолетовые CTA 10,7%; ALL CAPS в подписях и меню 10,5%; нумерованные шаги 9,4%; плашка-капсула над заголовком 4,7%; цветные тени 4,3%; эмодзи в меню 3,8% — [Gigazine](https://www.gigazine.net/gsc_news/en/20260423-design-slop).
- **[широко] Фиолетовый/индиго, градиентный текст и фон** — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [impeccable](https://impeccable.style/slop) («AI color palette», «Gradient text»), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md), [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026). Популярная версия происхождения: модели переняли `bg-indigo-500` из Tailwind UI, за который Адам Уотан извинился — [prg.sh](https://prg.sh/ramblings/Why-Your-AI-Keeps-Building-the-Same-Purple-Gradient-Website).
- **[широко] Шрифт по умолчанию (Inter, Roboto, Geist, системный) на всём** — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [impeccable](https://impeccable.style/slop), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [925studios](https://www.925studios.co/blog/ai-slop-design-tells). Скилл Anthropic конкретные шрифты не называет, просто требует выбирать не те, что взяли бы в любом проекте — [Anthropic frontend-design](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md).
- **[широко] Сетка одинаковых карточек «иконка → заголовок → текст»** и шаблон «hero → плашка → 2 кнопки → логотипы → 3 карточки → цены → CTA» — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [impeccable](https://impeccable.style/slop) («Identical card grids», «Icon tile stacked above heading»), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md) («SaaS-card kit»).
- **[широко] Цветная полоса сбоку или сверху у карточки** — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [impeccable](https://impeccable.style/slop) («Side-tab accent border»), [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md) (можно только как статус, приоритет или выбор: «если не можете назвать смысл цвета одним словом — уберите»). Сравнение с тире в AI-тексте — [hyper.ai](https://hyper.ai/en/stories/87f7113ad962c94b2b8f9da986afda49); «самая узнаваемая подпись AI-UI» — [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026) **[1 ист., автор — «AI Bot»]**.
- **[широко] Шаблонная обвязка:** uppercase-подпись с разрядкой над каждым заголовком, плашка над H1, строки «A · B · C», моноширинные мелкие подписи, «→» на каждой ссылке и кнопке, 01/02/03 там, где нет последовательности — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [impeccable](https://impeccable.style/slop) («Label above a heading», «Badge above the main headline», «Tiny numbered section labels»), [Krebs](https://www.adriankrebs.ch/blog/design-slop/).
- **[широко] Цифры-плитки и KPI-баннер** («Hero metric layout», «Stat banner rows»), часто с выдуманными числами («10,000% ROI») — [impeccable](https://impeccable.style/slop), [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/).
- **[широко] Набор карточки:** один большой радиус на всём, мягкая широкая тень, карточки внутри карточек, тонкая рамка + широкая тень на одном краю — [impeccable](https://impeccable.style/slop), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026).
- **[широко] Glassmorphism, свечение, радиальный ореол за hero** — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [impeccable](https://impeccable.style/slop), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/).
- **[широко] Движение:** одинаковый fade-and-slide-up на каждой секции, hover-эффект на каждой карточке, bounce/elastic, счётчики-накрутки цифр, пульсирующая точка или «Live» без живого статуса — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [impeccable](https://impeccable.style/slop), [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026).
- **[широко] Вторая волна:** кремовый фон около `#F4F1EA` + контрастный serif + терракота около `#D97757` (это акцент самого Claude); почти чёрный + один кислотно-зелёный или киноварный акцент; «газетный косплей» (тонкие линейки, нулевой радиус, колонки) на неиздательском контенте; один курсивный serif-акцент в заголовке — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [impeccable](https://impeccable.style/slop) («Cream / beige palette», «Italic serif display headline»), [Krebs](https://www.adriankrebs.ch/blog/design-slop/).
- **[1–2 ист.]** Эмодзи вместо иконок — [Krebs](https://www.adriankrebs.ch/blog/design-slop/), [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md). Декоративная сетка-линейка на фоне, полосы repeating-gradient, «небрежные» SVG-иллюстрации из кругов и блоков, бегущая строка, мигающий курсор, одинаковые отступы везде, плоская иерархия шрифтов — [impeccable](https://impeccable.style/slop). Тёмная тема по умолчанию — [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md), [Krebs](https://www.adriankrebs.ch/blog/design-slop/). Чрезмерная разрядка у подписей — [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026). Заглушки «John Doe», «Acme» — [prg.sh](https://prg.sh/ramblings/Why-Your-AI-Keeps-Building-the-Same-Purple-Gradient-Website).

### Inferences
- Если считать независимые источники, чаще всего упоминаются: градиенты и фиолетовый, Inter, одинаковые карточки, полоса сбоку, uppercase-подписи и плашка над H1, KPI-баннер, glassmorphism, fade-up на всём. Это первые пункты чек-листа.
- Проблема почти везде формулируется как «поставлено по привычке, где угодно и без смысла», а не как запрет приёма. Поэтому каждый пункт чек-листа стоит проверять вопросом «что этот элемент значит здесь?».

### Gaps
- О «тройных иконках-карточках» и «hover lift» как отдельном замере данных нет: Кребс меряет карточки с иконкой и цветные тени, подъём при наведении не меряет.
- Manrope и Golos Text ни в одном найденном списке «шрифтов AI» не встречаются. Это значит только то, что источники их не называют.

## 2. Признаки AI в текстах интерфейса (RU/EN)

### Takeaway
В английском это словарь вроде Unlock / Elevate / Seamless / Empower / Supercharge, кнопки «Get Started» и выдуманные цифры. В русском — «не X, а Y», «не только…, но и», рубленые фразы-парцелляции, канцелярит («является»), оценочная «вода» и тире почти в каждом предложении. Выдаёт скопление признаков, а не одно слово.

### Cited Findings
- **[широко]** Злоупотребление тире, общие маркетинговые обещания и лозунги «не X, а Y» — [impeccable](https://impeccable.style/slop); «Get Started», «Elevate your workflow» и выдуманные цифры (их визуальная правка не поймает) — [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/); подписи в виде «СЛОВО — фрагмент» с тире — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md).
- EN-словарь: unlock, unleash, elevate, empower, seamless, streamline, robust, leverage, delve. Правило из этих источников: два таких слова рядом — переписывать секцию, «seamless» заменить тем, что реально происходит — [microcopyexamples](https://microcopyexamples.substack.com/p/stop-sounding-like-ai), [Blake Stockton](https://www.blakestockton.com/p/red-flag-words) (блоги практиков, а не исследования).
- RU: отрицательный параллелизм («не только… но и»), канцелярит («является», «славится»), оценочная вода («гениальный», «захватывающий»), парцелляция («Два года. Без операций.»), скачки стиля, эмодзи и одинаковые списки — [iPhones.ru](https://www.iphones.ru/iNotes/kak-ponyat-chto-tekst-napisal-ii-tire-i-spiski-bolshe-ni-o-chyom-ne-govoryat). «Не X, а Y», пафосные вводные «в современном мире», «на сегодняшний день», лишние тире — [Хабр](https://habr.com/ru/articles/1013632) (видел только сниппет поиска, статью целиком не проверял).
- Для интерфейса: конкретные подписи кнопок («Save API Key», а не «Continue»), ошибки с подсказкой, как исправить, «…» одним символом, «Loading…», числа цифрами — [Vercel Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).

### Inferences
- «Новый уровень» — русский аналог «next level» из тех же списков. Для Bruno Lab те же правила stop-slop, что уже есть у Бруно в `polish_stream`, стоит применить и к статическим текстам интерфейса.
- Совет Vercel про Title Case к русскому не подходит: у нас заглавная только в первом слове.

### Gaps
- Источника с URL о восклицательных знаках и «тройках» («быстро, просто, удобно») как признаке AI в UI-тексте не нашёл. Это известный пункт stop-slop, но в найденных материалах его нет.
- Русских исследований именно по текстам лендингов не нашлось, только общие статьи о «нейроязе».

## 3. Что делает дизайн «сделанным человеком под конкретный продукт»

### Takeaway
Все источники сходятся: брать мотивы из мира продукта (его материалы, артефакты, язык), собирать страницу на реальном контенте и данных, тратить смелость в одном месте (один оркестрованный момент движения, один намеренный разрыв сетки) и проверять «могла бы эта первая страница принадлежать другой компании?».

### Cited Findings
- **[широко]** Отличительные решения берутся из отрасли, предмета, материалов и языка продукта; строить на реальном контенте; «Spend your boldness in one place»; Chanel-тест «убрать один аксессуар»; один оркестрованный момент вместо разбросанных эффектов — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md). Начинать с самого характерного объекта продукта (в демо страница перестроена вокруг таблицы когорт самого продукта); анимировать только в ответ на действие пользователя — [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/). Реальные скриншоты и данные, голос основателя, движение показывает смену состояния — [noqta](https://noqta.tn/en/blog/ai-design-slop-overused-ui-patterns-fix-2026).
- **[широко]** Записать направление явно: 4–6 именованных цветов с ролями, 1–2 шрифта, шкала размеров; хранить в DESIGN.md или токенах — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/), [DaisyUI](https://trends.daisyui.com/how-to-make-ai-ui-less-generic/) (описывать систему: отступы, повторяющиеся формы, где цвет несёт смысл).
- **[1 ист.] Refero:** по умолчанию без карточек; карточка нужна только как контейнер действия. Тест: если убрать рамку, тень, фон и радиус и ничего не сломалось — это не карточка. Сетку ломать одним элементом, а не всем подряд. «Identity test»: если первый экран подходит другой компании, добавить одну деталь бренда. Каждое решение должно объясняться так: «выбрал, потому что [причина именно этого проекта]» — [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md).
- Пустые состояния и крайние случаи (пустые массивы, очень длинный ввод), `tabular-nums` для чисел, `Intl.NumberFormat`, `prefers-reduced-motion`, анимировать только transform/opacity, не писать `transition: all` — [Vercel](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).

### Inferences
- Для «Тетради наставника» мотивы продукта уже есть: картина проекта со статусами факт/догадка/неизвестно, радар, дневник доказательств, задания Бруно. Их и стоит делать главными визуальными объектами вместо абстрактных карточек-фич.
- Единственный «момент» движения логично отдать Бруно (маскот уже несёт абрикосовый цвет), остальное движение — только в ответ на действия.

### Gaps
- Количественных данных о том, что «человечный» дизайн лучше конвертирует, в надёжных источниках нет. Цифры у VP0 и похожих вендоров не проверены.

## 4. Как тёплой бумажной палитре не выглядеть дефолтом «крем + serif + терракота»

### Takeaway
Шаблоном выглядит связка: кремовый фон + крупный (курсивный) serif + терракотовый или оливковый акцент + «воздушная» редакционная вёрстка без причины. Один кремовый фон шаблоном не считается. Палитра выглядит намеренной, когда каждый тон держит свою роль и бумага объяснима миром продукта.

### Cited Findings
- **[широко]** Кремовый фон около `#F4F1EA` с контрастным serif и терракотой или глиной Anthropic называет одним из дефолтов, вокруг которых кучкуется AI-дизайн, — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md). Если попросить «менее AI», модель выдаёт как раз крем + терракоту или чёрный + неон — [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/). Крем и беж «выбраны по привычке» — [impeccable](https://impeccable.style/slop).
- **[1 ист.] Refero:** serif и землистые тона не запрещены, запрещён «calm editorial на автопилоте». Перед кремом, serif и землистыми тонами надо ответить, зачем продукту редакционный голос, почему serif, почему эти тона подходят аудитории. Для дашбордов, SaaS и функционального UI без исследования это шаблон. «Editorial test»: если заменить логотип на кофейню или бутик-отель и hero всё ещё правдоподобен, язык недостаточно конкретен. Нельзя дрейфовать ролями токенов (акцент только для CTA не должен становиться фоном или бейджем) — [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md).
- Все приёмы допустимы для какого-то брифа, но это «defaults rather than choices»; слова брифа всегда побеждают — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md), [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/). Обратная позиция: Lovart советует крем + serif + терракоту как «профессиональный» вид — [Lovart](https://www.lovart.ai/blog/non-designers-dictionary-translating-vibes-visuals/) **[1 ист.]**.

### Inferences
- `#f5f4ef` визуально почти совпадает с `#F4F1EA`. Отличие Bruno Lab должно идти от других элементов связки: sans-шрифты (Manrope + Golos, без курсивного serif-акцента в заголовках), бирюзовый `#147d73` вместо терракоты, абрикосовый только у Бруно (чтобы он не расползался в кнопки и бейджи, это и есть «дрейф ролей»).
- «Тетрадь» оправдывает бумагу, только если мотив функционален: разлиновка или поля там, где основатель реально пишет (заметки, дневник, картина проекта). Сплошная сетка на фоне попадает в «Decorative grid-line background» и «газетный косплей».
- Плоские карточки с рамкой 1px без широкой тени не попадают в «Hairline border with wide shadow». Риск в другом: одинаковые сетки и вложенные карточки.

### Gaps
- Не нашёл источников, где разобраны удачные примеры тёплой бумажной палитры в продуктовых (не редакционных) интерфейсах.

## 5. Опубликованные инструменты и чек-листы

### Takeaway
Есть готовые скиллы и детекторы, которые можно прогнать по шаблонам Bruno Lab: impeccable (каталог антипаттернов + CLI-детектор), frontend-design от Anthropic, anti-ai-slop.md от Refero, Vercel Web Interface Guidelines. Скрипт Кребса показывает, что большинство признаков ловится детерминированными проверками DOM и CSS.

### Cited Findings
- impeccable (Пол Бакаус): каталог антипаттернов по группам (визуальные детали, типографика, цвет, вёрстка, движение, копирайт, иллюстрации) — [impeccable](https://impeccable.style/slop); CLI-детектор для HTML/CSS/JSX/Vue/Svelte, «AI Slop Test»: поверит ли человек сразу, что это сделал AI — [Emelia](https://emelia.io/hub/impeccable-ai-design-skill).
- Anthropic frontend-design: список дефолтов первой и второй волны, план 4–6 цветов, проверка «пройти похожий промпт и посмотреть, не придёшь ли туда же», минимум качества: адаптивность до мобильного, видимый фокус, reduced motion — [Anthropic](https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md).
- Refero anti-ai-slop.md: чек-лист перед выпуском («screenshot test» рядом с реальными продуктами, обоснование каждого выбора) — [Refero](https://raw.githubusercontent.com/referodesign/refero_skill/f78b4eccf112d7a179b92afeafdd7e8684560ac2/references/anti-ai-slop.md).
- Vercel Web Interface Guidelines: правила типографики, анимации, текстов, пустых состояний и список антипаттернов — [Vercel](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).
- Метод Кребса (Playwright + скрипт по computed styles, 5–10% ложных срабатываний) — [Krebs](https://www.adriankrebs.ch/blog/design-slop/). Сканер стоит ставить в CI или хук: правило в инструкции агента можно проигнорировать, упавшую сборку нельзя; осознанные решения помечать ignore-комментарием — [capitalandcompute](https://capitalandcompute.net/blog/fix-ai-slop-design/).

### Inferences
- Для Bruno Lab практичнее всего свой короткий скрипт по шаблонам (`grep` по `app.css` и шаблонам: `linear-gradient`, `backdrop-filter`, `border-left:` с цветом, `text-transform: uppercase` + `letter-spacing`, `→` в подписях, `cubic-bezier` с выбросом) плюс ручная проверка текстов по списку из раздела 2.

### Gaps
- Обсуждение поста Кребса на HN целиком не нашёл, реакцию комментаторов оценить не могу. Вики [Laith0003/ux-skill](https://github.com/Laith0003/ux-skill/wiki/How-to-detect-AI-slop-in-your-design) и статью [Fountain Institute](https://pages.thefountaininstitute.com/posts/7-tells-that-a-ui-is-ai-generated) не открывал.
