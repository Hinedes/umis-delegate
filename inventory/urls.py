from django.urls import path

from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('new/', views.consumable_create, name='consumable_create'),
    path('<int:pk>/edit/', views.consumable_edit, name='consumable_edit'),
    path('<int:pk>/toggle/', views.consumable_toggle_active, name='consumable_toggle_active'),
    path('<int:pk>/delete/', views.consumable_delete, name='consumable_delete'),
    path('<int:pk>/adjust/', views.stock_adjustment, name='stock_adjustment'),
]
