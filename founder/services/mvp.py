"""Deterministic RICE/ICE, Kano classification, recommendations and task links."""
from collections import Counter
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from founder.models import FeatureCandidate, KanoResponse, BrunoTask, EvidenceEntry, StartupProfile

KANO_TABLE = (
    ('Q','A','A','A','O'),
    ('R','I','I','I','M'),
    ('R','I','I','I','M'),
    ('R','I','I','I','M'),
    ('R','R','R','R','Q'),
)
KANO_LABELS = {'M':'Обязательная','O':'Линейная','A':'Привлекательная','I':'Безразличная',
    'R':'Обратная','Q':'Противоречивый ответ','mixed':'Смешанные ответы','none':'Нет ответов'}
PRIORITY_SHORT = {'must':'Must','should':'Should','could':'Could','wont':'Won’t','':'Не оценено'}


def rice_score(feature):
    values=(feature.reach,feature.rice_impact,feature.confidence,feature.effort)
    if any(value is None for value in values) or feature.effort <= 0:
        return None
    return Decimal(feature.reach)*Decimal(feature.rice_impact)*(Decimal(feature.confidence)/100)/Decimal(feature.effort)


def ice_score(feature):
    if any(value is None for value in (feature.ice_impact,feature.confidence,feature.ease)):
        return None
    return Decimal(feature.ice_impact)*(Decimal(feature.confidence)/10)*Decimal(feature.ease)


def kano_category(present,absent):
    if type(present) is not int or type(absent) is not int or not 1 <= present <=5 or not 1 <= absent <=5:
        raise ValueError('Выберите один из пяти ответов на каждый вопрос.')
    return KANO_TABLE[present-1][absent-1]


def kano_summary(responses):
    counts=Counter(kano_category(response.present,response.absent) for response in responses)
    valid={key:counts[key] for key in ('M','O','A','I','R')}
    total=sum(valid.values())
    leading=max(valid.values(),default=0)
    leaders=[key for key,count in valid.items() if count == leading and leading]
    category=leaders[0] if len(leaders)==1 else 'mixed' if leaders else 'none'
    label='Нет пригодных ответов' if category=='none' and counts['Q'] else KANO_LABELS[category]
    return {'category':category,'label':label,'valid':total,'total':sum(counts.values()),
        'questionable':counts['Q'],'counts':valid,
        'distribution':[{'key':key,'label':KANO_LABELS[key],'count':counts[key]} for key in ('M','O','A','I','R','Q')]}


def board(startup,method='rice'):
    candidates=list(startup.feature_candidates.select_related('task','kano_task','kano_evidence').prefetch_related('kano_responses'))
    for feature in candidates:
        feature.rice=rice_score(feature)
        feature.ice=ice_score(feature)
        feature.score=feature.ice if method=='ice' else feature.rice
        feature.kano=kano_summary(list(feature.kano_responses.all()))
    candidates.sort(key=lambda f:(f.score is None,-f.score if f.score is not None else Decimal(0),f.created_at,str(f.pk)))
    rated=[feature for feature in candidates if feature.score is not None]
    for feature in candidates:
        better=sum(other.score > feature.score for other in rated) if feature.score is not None else 0
        percentile=better/max(len(rated)-1,1)
        suggestion=('' if feature.score is None else 'wont' if feature.score==0 else
            'must' if percentile < .25 else 'should' if percentile < .5 else 'could' if percentile < .75 else 'wont')
        feature.suggested_priority=suggestion
        feature.effective_priority=feature.priority or suggestion
        feature.priority_label=PRIORITY_SHORT[feature.effective_priority]
        # The founder's explicit Won't takes precedence over any recommendation.
        feature.in_mvp=(feature.effective_priority=='must' or feature.kano['category']=='M') and feature.priority!='wont'
        feature.mvp_reason=('Вы отложили функцию' if feature.priority=='wont' else
            'Обязательная по Кано' if feature.kano['category']=='M' else
            'Must — ваш выбор' if feature.priority=='must' else 'Must — предложение по рейтингу' if feature.in_mvp else '')
    return candidates


def survey_questions(feature):
    return (feature.question_present or f'Как вы отнесётесь к тому, что в продукте есть функция «{feature.title}»?',
        feature.question_absent or f'Как вы отнесётесь к тому, что в продукте нет функции «{feature.title}»?')


def create_survey_task(feature):
    if feature.kano_task_id:
        return feature.kano_task,False
    present,absent=survey_questions(feature)
    task=BrunoTask.objects.create(startup=feature.startup,origin='kano',axis='product',
        title=('Опросить пользователей: '+feature.title)[:160],
        instructions=(f'Поговорите с реальными людьми из целевой аудитории. Для каждого сохраните два ответа по пяти вариантам Кано в разделе «Функции MVP».\n\n1. {present}\n2. {absent}\n\nФиксируйте ответы дословно, источник и дату. Используйте анонимные коды респондентов.')[:1200],
        success_criterion='Получены реальные пары ответов, указаны источник и даты. Распределение категорий сохранено в дневнике доказательств.')
    feature.question_present,feature.question_absent=present,absent
    feature.kano_task=task
    feature.save(update_fields=['question_present','question_absent','kano_task','updated_at'])
    return task,True


def sync_kano_evidence(feature):
    responses=list(feature.kano_responses.all())
    summary=kano_summary(responses)
    if not responses:
        return None
    present,absent=survey_questions(feature)
    lines=[f'Кано: {feature.title}. Ответы внесены участником проекта, не сгенерированы моделью.',
        f'С вопросом о наличии: {present}',f'С вопросом об отсутствии: {absent}',
        f'Всего ответов: {summary["total"]}; пригодных: {summary["valid"]}; противоречивых: {summary["questionable"]}.',
        'Распределение: '+', '.join(f'{item["label"]} — {item["count"]}' for item in summary['distribution']),
        f'Категория по наиболее частому ответу: {summary["label"]}. Ничья не разрешается автоматически.',
        'Количество ответов само по себе не доказывает репрезентативность.']
    for response in responses[:12]:
        lines.append(f'{response.respondent}: {response.observed_on:%d.%m.%Y}, {KANO_LABELS[response.category]}; источник: {response.source}')
    if len(responses)>12:lines.append('Полный список ответов сохранён в разделе «Функции MVP».')
    values={'startup':feature.startup,'task':feature.kano_task,'axis':'product',
        'claim':('Потребность пользователей в функции: '+feature.title)[:300],
        'observation':'\n'.join(lines)[:4000],
        'source':('Опрос Кано: '+', '.join(dict.fromkeys(r.source for r in responses)))[:500],
        'observed_on':max(r.observed_on for r in responses),'outcome':EvidenceEntry.Outcome.UNCLEAR,
        'next_question':'Как эта функция влияет на основной сценарий и какие сегменты дают разные ответы?'}
    if feature.kano_evidence_id:
        for key,value in values.items():setattr(feature.kano_evidence,key,value)
        feature.kano_evidence.save()
    else:
        feature.kano_evidence=EvidenceEntry.objects.create(**values)
        feature.save(update_fields=['kano_evidence','updated_at'])
    # Do not silently mark the survey task done after the first response.
    return feature.kano_evidence


@transaction.atomic
def transfer_features(startup,selected,method='rice'):
    StartupProfile.objects.select_for_update().get(pk=startup.pk)
    rows=board(startup,method)
    selected=set(map(str,selected))
    if len(selected)>50 or not selected <= {str(feature.pk) for feature in rows}:
        raise ValueError('Выберите функции этого проекта, не более 50 за один раз.')
    created=0
    for feature in rows:
        if str(feature.pk) not in selected:continue
        if not feature.in_mvp:
            raise ValueError('В задания переносятся Must и обязательные по Кано. Сначала подтвердите приоритет функции.')
    for feature in rows:
        if str(feature.pk) not in selected or feature.task_id:continue
        task=BrunoTask.objects.create(startup=startup,origin='mvp',axis='product',title=feature.title,
            instructions=(f'Реализуйте функцию MVP: {feature.title}.\n{feature.description}\n\nОснование выбора: {feature.mvp_reason}. Оценки основателя: {feature.estimate_basis or "источник оценки не указан"}. Проверьте главный пользовательский сценарий и сохраните результат в дневнике.')[:1200],
            success_criterion='Функция работает в основном сценарии; результат проверки и оставшиеся ограничения записаны в дневник.')
        feature.task=task
        feature.save(update_fields=['task','updated_at'])
        created+=1
    return created
