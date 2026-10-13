from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class SignupForm(UserCreationForm):
    """Inscription : e-mail obligatoire, normalisé et unique.

    Sans unicité, deux comptes peuvent partager une adresse : la connexion
    par e-mail devient alors ambiguë et un utilisateur peut s'approprier
    l'adresse d'un tiers sans vérification.
    """

    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            raise ValidationError('Une adresse e-mail est obligatoire.')
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Cette adresse e-mail est déjà utilisée.')
        # La casse ne distingue pas deux adresses : on normalise une fois pour
        # toutes afin que la recherche par e-mail reste fiable.
        return email.lower()

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if username != self.cleaned_data.get('username'):
            raise ValidationError("Le nom d'utilisateur ne doit pas contenir d'espaces.")
        return username
