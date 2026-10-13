from django.urls import path
from founder import community_views as views

urlpatterns = [
    path('community/', views.community_feed, name='community'),
    path('community/<uuid:startup_id>/', views.card_detail, name='card_detail'),
    path('community/<uuid:startup_id>/save/', views.bookmark, name='bookmark'),
    path('community/<uuid:startup_id>/report/', views.card_report, name='card_report'),
    path('community/<uuid:startup_id>/message/', views.conversation_start, name='conversation_start'),
    path('startups/<uuid:startup_id>/card/', views.card_edit, name='card_edit'),
    path('startups/<uuid:startup_id>/card/generate/', views.card_generate, name='card_generate'),
    path('startups/<uuid:startup_id>/card/unpublish/', views.card_unpublish, name='card_unpublish'),
    path('messages/', views.inbox, name='inbox'),
    path('messages/<uuid:conversation_id>/', views.inbox, name='conversation'),
    path('messages/<uuid:conversation_id>/block/', views.block_contact, name='block_contact'),
    path('messages/<uuid:conversation_id>/search/', views.conversation_search, name='conversation_search'),
    path('messages/<uuid:conversation_id>/attachments/', views.direct_attachment_upload, name='direct_attachment_upload'),
    path('messages/attachments/<uuid:attachment_id>/', views.direct_attachment, name='direct_attachment'),
    path('messages/attachments/<uuid:attachment_id>/delete/', views.direct_attachment_delete, name='direct_attachment_delete'),
]
