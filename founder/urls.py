from django.contrib.auth import views as auth_views
from django.urls import path
from django.urls import reverse_lazy

from founder import views, workbench_views, lab_views


urlpatterns = [
    path('startups/<uuid:startup_id>/lab/', lab_views.lab, name='lab'),
    path('startups/<uuid:startup_id>/lab/generate/', lab_views.lab_generate, name='lab_generate'),
    path('startups/<uuid:startup_id>/lab/<uuid:version_id>/preview/', lab_views.lab_preview, name='lab_preview'),
    path('startups/<uuid:startup_id>/tasks/', workbench_views.tasks, name='tasks'),
    path('startups/<uuid:startup_id>/tasks/generate/', workbench_views.tasks_generate, name='tasks_generate'),
    path('startups/<uuid:startup_id>/tasks/<uuid:task_id>/skip/', workbench_views.task_skip, name='task_skip'),
    path('startups/<uuid:startup_id>/evidence/', workbench_views.evidence_list, name='evidence_list'),
    path('startups/<uuid:startup_id>/evidence/new/', workbench_views.evidence_edit, name='evidence_create'),
    path('startups/<uuid:startup_id>/evidence/<uuid:entry_id>/', workbench_views.evidence_edit, name='evidence_edit'),
    path('startups/<uuid:startup_id>/investor/', workbench_views.investor, name='investor'),
    path('startups/<uuid:startup_id>/review/', workbench_views.review, name='review'),
    path('startups/<uuid:startup_id>/review/generate/', workbench_views.review_generate, name='review_generate'),
    path('startups/<uuid:startup_id>/review/<uuid:review_id>/steps/<int:index>/task/', workbench_views.review_step_task,
         name='review_step_task'),
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("register/", views.register, name="register"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("account/password/", auth_views.PasswordChangeView.as_view(
        template_name="registration/password_change_form.html", success_url=reverse_lazy("home"),
    ), name="password_change"),
    path("startups/new/", views.startup_create, name="startup_create"),
    path("startups/<uuid:startup_id>/", views.dashboard, name="dashboard"),
    path("startups/<uuid:startup_id>/edit/", views.startup_edit, name="startup_edit"),
    path("startups/<uuid:startup_id>/metrics/", views.metrics_create, name="metrics_create"),
    path("startups/<uuid:startup_id>/metrics/assess/", views.metrics_assess, name="metrics_assess"),
    path("startups/<uuid:startup_id>/chat/new/", views.chat_create, name="chat_create"),
    path("startups/<uuid:startup_id>/chat/refine/<str:axis>/", views.chat_refine, name="chat_refine"),
    path("startups/<uuid:startup_id>/pitch/new/", views.pitch_create, name="pitch_create"),
    path("startups/<uuid:startup_id>/chat/<uuid:session_id>/", views.chat_detail, name="chat_detail"),
    path("startups/<uuid:startup_id>/chat/<uuid:session_id>/send/", views.chat_send, name="chat_send"),
    path("startups/<uuid:startup_id>/chat/<uuid:session_id>/finish/", views.pitch_finish, name="pitch_finish"),
    path("startups/<uuid:startup_id>/chat/<uuid:session_id>/messages/<uuid:message_id>/feedback/",
         views.message_feedback, name="message_feedback"),
]
