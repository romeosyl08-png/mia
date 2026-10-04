from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
	path('', views.chat_home, name='chat-home'),
	path('chat/api/send/', views.SendMessageView.as_view(), name='chat-send-message'),
	path('chat/api/images/generate/', views.ImageGenerationView.as_view(), name='image-generate'),
	path('chat/api/media/generate/', views.ImageGenerationView.as_view(), name='media-generate'),
	path('chat/api/media/gallery/', views.GeneratedMediaGalleryView.as_view(), name='generated-media-gallery'),
	path('chat/api/media/<int:pk>/', views.GeneratedMediaView.as_view(), name='generated-media'),
	path('dap/', views.dap_home, name='dap-home'),
    path("customize/", views.customize, name="customize"),
	path('nous/', views.about_page, name='about'),
	path('juridique/', views.legal_page, name='legal'),

	path('api/conversations/', views.ConversationListView.as_view(), name='conversation-list'),
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
