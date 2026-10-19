from django.urls import path

from . import views, memory_views, appearance_views

urlpatterns = [
    path('', views.api_home, name='api-home'),
    path('models', views.models_view, name='api-models'),
    path('chat/completions', views.chat_completions, name='api-chat-completions'),
    path('balance', views.balance_view, name='api-balance'),
    path('memory', memory_views.memory_view, name='api-memory'),
    path('appearance', appearance_views.appearance_view, name='api-appearance'),
]
