from django.urls import path

from . import views

urlpatterns = [
    path('', views.panel, name='control_panel'),
    path('policy/', views.policy_update, name='control_policy'),
    path('run/start/', views.start_run, name='control_start'),
    path('ask/', views.assistant_ask, name='control_ask'),
    path('run/<int:pk>/', views.run_detail, name='control_run_detail'),
    path(
        'run/<int:pk>/approval/<int:approval_pk>/<str:decision>/',
        views.approval_decide,
        name='control_approval',
    ),
    path('audit/', views.audit_activity, name='control_audit'),
]
