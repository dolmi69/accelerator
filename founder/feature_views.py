"""Project-scoped MVP actions; founders/editors write, viewers only read."""
from contextlib import contextmanager
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction,IntegrityError,OperationalError
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_POST,require_http_methods
from founder.models import FeatureCandidate,KanoResponse,StartupProfile
from founder.feature_forms import FeatureForm,KanoForm
from founder.services.access import get_startup
from founder.services import mvp,feature_ai,activity
from founder.services.ai import AIServiceError
from founder.services.qwen import QwenError
from founder.services.request_limits import acquire_ai_lease,release_ai_lease,consume_limit,RequestLimitExceeded


def _method(request):
    return 'ice' if (request.POST.get('method') or request.GET.get('method'))=='ice' else 'rice'


def _back(startup,method='rice',anchor='mvp-features'):
    return redirect(reverse('tasks',args=[startup.pk])+f'?method={method}#{anchor}')


def board_context(startup,request):
    method=_method(request)
    rows=mvp.board(startup,method)
    return {'features':rows,'mvp_method':method,
        'feature_count':len(rows),'rated_count':sum(row.score is not None for row in rows),
        'mvp_count':sum(row.in_mvp for row in rows),
        'transferable_count':sum(row.in_mvp and not row.task_id for row in rows),
        'mvp_effort':sum((row.effort or 0) for row in rows if row.in_mvp),
        'mvp_effort_incomplete':any(row.in_mvp and row.effort is None for row in rows)}


@contextmanager
def _ai_request(user):
    token=acquire_ai_lease(user.pk,ttl_seconds=300)
    try:
        consume_limit(f'ai-minute:{user.pk}',settings.AI_REQUESTS_PER_MINUTE,60)
        consume_limit(f'ai-day:{user.pk}',settings.AI_REQUESTS_PER_DAY,86400)
        yield
    finally:
        release_ai_lease(user.pk,token)


@login_required
@require_http_methods(['GET','POST'])
def feature_edit(request,startup_id,feature_id=None):
    startup=get_startup(request,startup_id,edit=True)
    feature=get_object_or_404(startup.feature_candidates,pk=feature_id) if feature_id else FeatureCandidate(startup=startup,created_by=request.user)
    form=FeatureForm(request.POST if request.method=='POST' else None,instance=feature,startup=startup)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                StartupProfile.objects.select_for_update().get(pk=startup.pk)
                if feature._state.adding and startup.feature_candidates.count()>=100:
                    raise ValueError('В проекте уже 100 функций. Уточните существующий список.')
                titles=startup.feature_candidates.exclude(pk=feature.pk).values_list('title',flat=True)
                if any(title.casefold()==feature.title.casefold() for title in titles):
                    raise ValueError('Такая функция уже есть в проекте.')
                if not feature._state.adding:
                    current=FeatureCandidate.objects.select_for_update().get(pk=feature.pk)
                    if current.kano_responses.exists():
                        feature.question_present=current.question_present
                        feature.question_absent=current.question_absent
                form.save()
                activity.log(startup,request.user,activity.Kind.TASK,'Функция MVP: '+activity.quoted(feature.title))
            messages.success(request,'Функция сохранена. Баллы пересчитаны из ваших оценок.')
            return _back(startup,_method(request),f'feature-{feature.pk}')
        except ValueError as exc:
            form.add_error(None,str(exc))
        except (IntegrityError,OperationalError):
            form.add_error(None,'Функция уже сохранена или проект занят другим запросом. Обновите список.')
    return render(request,'founder/feature_form.html',{'startup':startup,'workspace_tab':'tasks','form':form,
        'feature':feature if not feature._state.adding else None,'mvp_method':_method(request)},status=400 if request.method=='POST' else 200)


@login_required
@require_POST
def features_suggest(request,startup_id):
    startup=get_startup(request,startup_id,edit=True)
    try:
        if startup.feature_candidates.count()>=100:raise ValueError('В проекте уже 100 функций. Сначала уточните существующий список.')
        with _ai_request(request.user):
            items=feature_ai.suggest_features(startup)
        with transaction.atomic():
            StartupProfile.objects.select_for_update().get(pk=startup.pk)
            existing={title.casefold() for title in startup.feature_candidates.values_list('title',flat=True)}
            created=0
            for item in items:
                if item['title'].casefold() in existing:continue
                if len(existing)>=100:break
                FeatureCandidate.objects.create(startup=startup,created_by=request.user,source='bruno',**item)
                existing.add(item['title'].casefold());created+=1
            activity.log(startup,request.user,activity.Kind.TASK,f'Бруно предложил функций MVP: {created}')
        messages.success(request,f'Добавлено предложений Бруно: {created}. Оценки не заполнены — внесите свои данные.')
    except (AIServiceError,QwenError,RequestLimitExceeded,ValueError,IntegrityError,OperationalError) as exc:
        messages.error(request,str(exc) if not isinstance(exc,(IntegrityError,OperationalError)) else 'Проект занят. Обновите список функций.')
    return _back(startup,_method(request))


@login_required
@require_POST
def features_transfer(request,startup_id):
    startup=get_startup(request,startup_id,edit=True)
    try:
        selected=request.POST.getlist('features')
        if not selected:raise ValueError('Выберите функции, которые хотите перенести в задания.')
        count=mvp.transfer_features(startup,selected,_method(request))
        if count:activity.log(startup,request.user,activity.Kind.TASK,f'Функций MVP перенесено в задания: {count}')
        messages.success(request,f'Создано заданий: {count}.' if count else 'Выбранные функции уже связаны с заданиями. Дубликатов не создано.')
    except (ValueError,IntegrityError,OperationalError) as exc:
        messages.error(request,str(exc) if isinstance(exc,ValueError) else 'Проект занят. Повторите после обновления страницы.')
    return _back(startup,_method(request))


@login_required
@require_POST
def feature_survey_prepare(request,startup_id,feature_id):
    startup=get_startup(request,startup_id,edit=True)
    feature=get_object_or_404(startup.feature_candidates,pk=feature_id)
    try:
        questions=None
        if request.POST.get('with_ai')=='1':
            if feature.kano_responses.exists():raise ValueError('По этой паре уже есть ответы. Сохраните формулировки для сравнимости результатов.')
            with _ai_request(request.user):questions=feature_ai.prepare_questions(feature)
        with transaction.atomic():
            feature=FeatureCandidate.objects.select_for_update().get(pk=feature.pk)
            if questions:
                if feature.kano_responses.exists():raise ValueError('Пока готовились вопросы, появился ответ. Формулировки сохранены.')
                feature.question_present=questions['question_present']
                feature.question_absent=questions['question_absent']
                feature.save(update_fields=['question_present','question_absent','updated_at'])
            task,created=mvp.create_survey_task(feature)
            if questions and not created:
                present,absent=mvp.survey_questions(feature)
                task.instructions=f'Опросите реальных людей из целевой аудитории. Сохраните два ответа каждого респондента в разделе «Функции MVP».\n1. {present}\n2. {absent}'
                task.save(update_fields=['instructions'])
        messages.success(request,'Пара вопросов и задание опросить людей готовы.' if created else 'Опрос уже связан с заданием.')
    except (AIServiceError,QwenError,RequestLimitExceeded,ValueError,OperationalError) as exc:
        messages.error(request,str(exc) if not isinstance(exc,OperationalError) else 'Проект занят. Повторите позже.')
    return redirect(reverse('feature_kano',args=[startup.pk,feature.pk])+f'?method={_method(request)}')


@login_required
@require_http_methods(['GET','POST'])
def feature_kano(request,startup_id,feature_id,response_id=None):
    startup=get_startup(request,startup_id,edit=request.method=='POST' or response_id is not None)
    feature=get_object_or_404(startup.feature_candidates.select_related('kano_task','kano_evidence'),pk=feature_id)
    response=get_object_or_404(feature.kano_responses,pk=response_id) if response_id else KanoResponse(feature=feature,created_by=request.user)
    form=KanoForm(request.POST if request.method=='POST' else None,instance=response,feature=feature)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                feature=FeatureCandidate.objects.select_for_update().get(pk=feature.pk)
                present,absent=mvp.survey_questions(feature)
                feature.question_present,feature.question_absent=present,absent
                feature.save(update_fields=['question_present','question_absent','updated_at'])
                if not response.pk or response._state.adding:
                    response.question_present,response.question_absent=present,absent
                form.save()
                mvp.sync_kano_evidence(feature)
                activity.log(startup,request.user,activity.Kind.EVIDENCE,'Ответ Кано: '+activity.quoted(feature.title))
            messages.success(request,'Ответ сохранён. Категория рассчитана, итог обновлён в дневнике.')
            return redirect(reverse('feature_kano',args=[startup.pk,feature.pk])+f'?method={_method(request)}')
        except (IntegrityError,OperationalError):form.add_error(None,'Ответ уже сохранён или проект занят. Обновите страницу.')
    responses=list(feature.kano_responses.all())
    for row in responses:row.category_label=mvp.KANO_LABELS[row.category]
    present,absent=mvp.survey_questions(feature)
    return render(request,'founder/feature_kano.html',{'startup':startup,'workspace_tab':'tasks','feature':feature,
        'form':form,'editing_response':response if response_id else None,'responses':responses,'summary':mvp.kano_summary(responses),
        'present_question':present,'absent_question':absent,'mvp_method':_method(request)},status=400 if request.method=='POST' else 200)
