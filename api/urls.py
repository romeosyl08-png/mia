from django.urls import path

from . import views

urlpatterns = [
    path('', views.api_home, name='api-home'),
    path('models', views.models_view, name='api-models'),
    path('chat/completions', views.chat_completions, name='api-chat-completions'),
    path('balance', views.balance_view, name='api-balance'),
]
