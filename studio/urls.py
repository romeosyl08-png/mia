from django.urls import path
from . import views

app_name = 'studio'
urlpatterns = [
    path('',views.index,name='index'),path('create/',views.create,name='create'),
    path('<int:pk>/',views.detail,name='detail'),path('<int:pk>/state/',views.state,name='state'),
    path('<int:pk>/scene/',views.save_scene,name='save'),path('<int:pk>/restore/',views.restore,name='restore'),
    path('<int:pk>/upload/',views.upload,name='upload'),path('<int:pk>/quote/',views.quote,name='quote'),
    path('<int:pk>/jobs/',views.enqueue,name='enqueue'),path('<int:pk>/jobs/<int:job_id>/cancel/',views.cancel,name='cancel'),
    path('<int:pk>/director/',views.propose,name='director'),path('<int:pk>/scene.json',views.scene_json,name='json'),
    path('<int:pk>/media/<int:asset_id>/',views.asset_file,name='asset'),
]
