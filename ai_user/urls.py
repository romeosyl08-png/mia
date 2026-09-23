from django.urls import path

from . import views


urlpatterns = [
    path("auth/", views.auth_page, name="auth-page"),
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("telegram/auth/", views.telegram_auth, name="telegram_auth"),
    path("whatsapp/send-otp/", views.whatsapp_send_otp, name="whatsapp_send_otp"),
    path("recharge/", views.recharge_page, name="recharge-page"),
    path("recharge/create-checkout/", views.create_recharge_checkout, name="create-recharge-checkout"),
    path("recharge/mobile-money/", views.create_mobile_money_checkout, name="create-mobile-money-checkout"),
    path("recharge/success/", views.recharge_success, name="recharge-success"),
    path("recharge/webhook/", views.stripe_webhook, name="stripe-webhook"),
    path('recharge/cinetpay-webhook/', views.cinetpay_webhook, name='cinetpay-webhook'),
    path('recharge/wave/', views.wave_recharge_request, name='wave-recharge-request'),
path('recharge/wave/pending/', views.wave_pending_payments, name='wave-pending-payments'),
path('recharge/wave/confirm/<int:purchase_id>/', views.confirm_wave_payment, name='confirm-wave-payment'),
path('recharge/wave/reject/<int:purchase_id>/', views.reject_wave_payment, name='reject-wave-payment'),
]