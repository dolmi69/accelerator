from django.urls import path
from founder.consumers import MessagesConsumer

websocket_urlpatterns = [path('ws/messages/', MessagesConsumer.as_asgi())]
