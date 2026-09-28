from django.urls import path
from founder import profile_views as views

urlpatterns = [
    path('account/profile/', views.my_profile, name='my_profile'),
    path('account/profile/edit/', views.profile_edit, name='profile_edit'),
    path('account/profile/handle/', views.handle_available, name='handle_available'),
    path('people/', views.member_directory, name='member_directory'),
    path('people/<int:user_id>/avatar/', views.user_avatar, name='user_avatar'),
    path('people/<int:user_id>/message/', views.profile_message, name='profile_message'),
    path('@<slug:handle>/', views.user_profile, name='user_profile'),
]
