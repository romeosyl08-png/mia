from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("auth/", views.auth_page, name="auth-page"),
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    # Déconnexion, changement et réinitialisation du mot de passe :
    # sans eux, aucun moyen de fermer une session ni de reprendre la main
    # sur un compte dont le mot de passe a fuité.
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("password/change/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password/reset/", views.SafePasswordResetView.as_view(), name="password_reset"),
    path("password/reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("telegram/auth/", views.telegram_auth, name="telegram_auth"),
    path("whatsapp/send-otp/", views.whatsapp_send_otp, name="whatsapp_send_otp"),
    path('auth/google/', views.google_login, name='google-login'),
    path('accounts/google/login/callback/', views.google_callback, name='google-callback'),
    path('auth/google/callback/', views.google_callback, name='google-callback-legacy'),
    path("recharge/", views.recharge_page, name="recharge-page"),
    path("recharge/currency/", views.set_currency, name="set-currency"),
    path("tarifs/", views.tariffs_page, name="tariffs-page"),
    path("recharge/create-checkout/", views.create_recharge_checkout, name="create-recharge-checkout"),
    path("recharge/paydunya/", views.create_paydunya_checkout, name="create-paydunya-checkout"),
    path("recharge/success/", views.recharge_success, name="recharge-success"),
    path("recharge/webhook/", views.stripe_webhook, name="stripe-webhook"),
    path("recharge/paydunya-webhook/", views.paydunya_webhook, name="paydunya-webhook"),

    path('api-keys/', views.api_keys_page, name='api-keys-page'),
    path('api-keys/barrassons/', views.barrassons_guide, name='barrassons-guide'),
]
