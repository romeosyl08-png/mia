from django.urls import path

from . import views, memory_views, recovery_views, persona_views, temporary_views

app_name = 'chat'

urlpatterns = [
    path('chat/api/big-persona/', persona_views.big_persona, name='big-persona'),
    path('chat/api/personas/<int:pk>/context/', persona_views.persona_context, name='persona-context'),
    path('chat/api/recovery/<str:token>/',recovery_views.recovery,name='recovery'),
    path('memory/', memory_views.memory_settings, name='memory'),
    path('memory/project/<int:workspace_id>/', memory_views.memory_settings, name='project-memory'),
	path('', views.chat_home, name='chat-home'),
	path('chat/api/send/', views.SendMessageView.as_view(), name='chat-send-message'),
	path('chat/api/images/generate/', views.ImageGenerationView.as_view(), name='image-generate'),
	path('chat/api/media/generate/', views.ImageGenerationView.as_view(), name='media-generate'),
	path('chat/api/media/gallery/', views.GeneratedMediaGalleryView.as_view(), name='generated-media-gallery'),
	path('chat/api/media/<int:pk>/', views.GeneratedMediaView.as_view(), name='generated-media'),
	path('chat/api/messages/<int:message_id>/feedback/', views.RoutingFeedbackView.as_view(), name='routing-feedback'),
	path('dap/', views.dap_home, name='dap-home'),
    path("customize/", views.customize, name="customize"),
	path('nous/', views.about_page, name='about'),
	path('juridique/', views.legal_page, name='legal'),

	path('api/conversations/', views.ConversationListView.as_view(), name='conversation-list'),
	path('chat/api/conversations/sync-local/', views.LocalConversationSyncView.as_view(), name='chat-sync-local-conversation'),
	path('chat/api/conversations/save-temporary/', temporary_views.TemporaryConversationSaveView.as_view(), name='chat-save-temporary-conversation'),
	path('api/conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='conversation-detail'),
	path('api/conversations/<int:pk>/delete/', views.ConversationDeleteView.as_view(), name='conversation-delete'),
	path('chat/api/conversations/', views.ConversationListView.as_view(), name='chat-conversation-list'),
	path('chat/api/folders/', views.ConversationFolderListView.as_view(), name='chat-folder-list'),
	path('chat/api/workspaces/', views.WorkspaceListView.as_view(), name='chat-workspace-list'),
	path('chat/api/preferences/', views.ChatPreferencesView.as_view(), name='chat-preferences'),
	path('chat/api/conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='chat-conversation-detail'),
	path('chat/api/conversations/<int:pk>/delete/', views.ConversationDeleteView.as_view(), name='chat-conversation-delete'),
	path('api/personas/', views.PersonaListView.as_view(), name='persona-list'),
	path('api/personas/<int:pk>/', views.PersonaDetailView.as_view(), name='persona-detail'),
	path('chat/api/personas/', views.PersonaListView.as_view(), name='chat-persona-list'),
	path('chat/api/personas/<int:pk>/', views.PersonaDetailView.as_view(), name='chat-persona-detail'),
	path('chat/api/personas/<int:pk>/suggest-memory/', views.PersonaMemorySuggestionView.as_view(), name='persona-memory-suggestion'),
]
