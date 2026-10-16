from django.urls import path

from . import views

app_name = 'creation'

urlpatterns = [
    path('', views.index, name='index'),
    path('library/', views.index, {'tab': 'library'}, name='library'),
    path('api/generate/', views.GenerationView.as_view(), name='generate'),
    path('api/library/', views.library, name='media-list'),
    path('api/use-in-chat/', views.use_in_chat, name='use-in-chat'),
    path('api/use-in-studio/', views.use_in_studio, name='use-in-studio'),
]
