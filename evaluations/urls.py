from django.urls import path

from . import views

app_name = 'evaluations'
urlpatterns = [
    path('', views.index, name='index'),
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('api/models/', views.models, name='models'),
    path('api/sessions/', views.sessions, name='sessions'),
    path('api/sessions/<int:pk>/', views.detail, name='detail'),
    path('api/sessions/<int:pk>/vote/', views.cast_vote, name='vote'),
]
