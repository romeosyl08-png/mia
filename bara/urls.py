from django.urls import path
from . import views

app_name = "bara"

urlpatterns = [
    path(
        "",
        views.workspace_home,
        name="home",
    ),

    path(
        "create/",
        views.create_workspace,
        name="create",
    ),

    path(
        "<int:workspace_id>/",
        views.workspace_detail,
        name="detail",
    ),

    path(
        "<int:workspace_id>/upload/",
        views.upload_file,
        name="upload",
    ),

    path(
        "<int:workspace_id>/message/",
        views.send_message,
        name="message",
    ),

    path('<int:workspace_id>/model/', views.update_model, name='update-model'),

    path('<int:workspace_id>/files/<int:file_id>/download/', views.download_file, name='download-file'),
]