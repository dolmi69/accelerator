from django.urls import path

from payments import views

urlpatterns = [
    path("", views.wallet, name="wallet"),
    path("topup/", views.topup_page, name="topup_page"),
    path("topup/new/", views.topup_create, name="topup_create"),
    path("<uuid:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path("<uuid:invoice_id>/status/", views.invoice_status, name="invoice_status"),
    path("<uuid:invoice_id>/qr.png", views.invoice_qr, name="invoice_qr"),
]
