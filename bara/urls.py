from django.urls import path
from . import views

app_name = "bara"

urlpatterns = [
    path('', views.workspace_home, name='home'),
    path('create/', views.create_workspace, name='create'),
    path('<int:workspace_id>/', views.workspace_detail, name='detail'),
    path('<int:workspace_id>/c/new/', views.new_conversation, name='new-conversation'),
    path('<int:workspace_id>/c/<int:conversation_id>/', views.workspace_detail, name='conversation'),
    path('<int:workspace_id>/c/<int:conversation_id>/delete/', views.delete_conversation, name='delete-conversation'),
    path('<int:workspace_id>/c/<int:conversation_id>/message/', views.send_message, name='message'),
    path('<int:workspace_id>/upload/', views.upload_file, name='upload'),
    path('<int:workspace_id>/model/', views.update_model, name='update-model'),
    path('<int:workspace_id>/rename/', views.rename_workspace, name='rename-workspace'),
    path('<int:workspace_id>/delete/', views.delete_workspace, name='delete-workspace'),
    path('<int:workspace_id>/instructions/', views.update_instructions, name='update-instructions'),
    path('<int:workspace_id>/tools/', views.update_workspace_tools, name='update-tools'),
    path('<int:workspace_id>/mcp/', views.add_mcp_server, name='add-mcp-server'),
    path('<int:workspace_id>/mcp/<int:server_id>/refresh/', views.refresh_mcp_server, name='refresh-mcp-server'),
    path('<int:workspace_id>/mcp/<int:server_id>/toggle/', views.toggle_mcp_server, name='toggle-mcp-server'),
    path('<int:workspace_id>/mcp/<int:server_id>/delete/', views.delete_mcp_server, name='delete-mcp-server'),
    path('<int:workspace_id>/journal/', views.tool_journal, name='tool-journal'),
    path('<int:workspace_id>/export/', views.export_zip, name='export-zip'),
    path('<int:workspace_id>/actions/<int:action_id>/<str:decision>/', views.resolve_action, name='resolve-action'),
    path('<int:workspace_id>/files/<int:file_id>/download/', views.download_file, name='download-file'),
    path('<int:workspace_id>/files/<int:file_id>/delete/', views.delete_file, name='delete-file'),
    path('<int:workspace_id>/files/<int:file_id>/preview/', views.preview_file, name='preview-file'),
    path('<int:workspace_id>/files/<int:file_id>/edit/', views.edit_file_view, name='edit-file'),
    path('<int:workspace_id>/files/<int:file_id>/history/', views.file_history, name='file-history'),
    path('<int:workspace_id>/files/<int:file_id>/history/<int:version_id>/restore/', views.restore_version, name='restore-version'),
    path('<int:workspace_id>/files/<int:file_id>/publish/', views.publish_file, name='publish-file'),
    path('<int:workspace_id>/files/<int:file_id>/unpublish/', views.unpublish_file, name='unpublish-file'),
    path('p/<str:token>/', views.public_page, name='public-page'),
]