"""
URL configuration for ai_2ai project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from django.conf import settings
from django.conf.urls.static import static
from . import healthcheck_views, pwa_views

urlpatterns = [
    path('healthz', healthcheck_views.healthz, name='healthz'),
    path('manifest.webmanifest', pwa_views.web_manifest, name='pwa-manifest'),
    path('service-worker.js', pwa_views.service_worker, name='pwa-service-worker'),
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
    path('v1/', include('api.urls')),
    path('', include('ai_user.urls')),
    path('', include('chat.urls')),
    path('bara/', include('bara.urls')),
    path('studio/', include('studio.urls')),
    path('creation/', include('creation.urls')),
    path('comparisons/', include('evaluations.urls')),
]
# MEDIA_ROOT ne doit jamais être servi directement en production : il a
# longtemps exposé les pièces jointes Bara de tous les espaces de travail.
# Les fichiers confidentiels passent par le stockage privé et par des vues
# qui vérifient les droits ; seules les démos locales gardent ce raccourci.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
