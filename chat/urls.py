from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
	path('', views.chat_home, name='chat-home'),
	path('chat/api/send/', views.SendMessageView.as_view(), name='chat-send-message'),
	path('dap/', views.dap_home, name='dap-home'),
    path("customize/", views.customize, name="customize"),
	path('prix/', views.pricing_page, name='pricing-page'),

	path('api/conversations/', views.ConversationListView.as_view(), name='conversation-list'),
	path('api/conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='conversation-detail'),
	path('api/conversations/<int:pk>/delete/', views.ConversationDeleteView.as_view(), name='conversation-delete'),
	path('chat/api/conversations/', views.ConversationListView.as_view(), name='chat-conversation-list'),
	path('chat/api/workspaces/', views.WorkspaceListView.as_view(), name='chat-workspace-list'),
	path('chat/api/conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='chat-conversation-detail'),
	path('chat/api/conversations/<int:pk>/delete/', views.ConversationDeleteView.as_view(), name='chat-conversation-delete'),
	path('api/personas/', views.PersonaListView.as_view(), name='persona-list'),
	path('api/personas/<int:pk>/', views.PersonaDetailView.as_view(), name='persona-detail'),
	path('chat/api/personas/', views.PersonaListView.as_view(), name='chat-persona-list'),
	path('chat/api/personas/<int:pk>/', views.PersonaDetailView.as_view(), name='chat-persona-detail'),
]
