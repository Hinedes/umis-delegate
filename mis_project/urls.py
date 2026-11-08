from django.contrib import admin
from django.urls import include, path

from core.views.health import healthz

# django.contrib.auth.urls serves /accounts/login/ with registration/login.html.
urlpatterns = [
    path('healthz', healthz, name='healthz'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('stock/', include('inventory.urls')),
    path('control/', include('control.urls')),
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]
