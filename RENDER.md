# Deployer sur Render

## 1. Publier le depot

Pousse le projet sur GitHub ou GitLab, puis cree un Blueprint Render avec `render.yaml`.
Render creera le service web et la base PostgreSQL.

## 2. Variables obligatoires

Dans le service web Render, renseigne `FERNET_KEY` avec une cle Fernet valide. Tu peux en generer une localement :

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`SECRET_KEY`, `DATABASE_URL`, `DEBUG`, `ALLOWED_HOSTS` et `CSRF_TRUSTED_ORIGINS` sont deja configurees par `render.yaml`.

## 3. Premier deploiement

Le build installe les dependances et collecte les fichiers statiques. Le processus de demarrage execute les migrations avant Gunicorn.

Apres le premier lancement, ouvre `/admin/` et cree un compte administrateur si necessaire :

```bash
python manage.py createsuperuser
```

Sur Render, execute cette commande depuis le Shell du service web.

## 4. Integrations optionnelles

Ajoute les secrets Stripe, Telegram et API dans les variables d'environnement Render si ces fonctions sont utilisees. Configure ensuite l'URL publique du service dans les webhooks Stripe et Telegram.

Pour activer l'API compatible OpenAI, `API_COMPATIBLE_KEY` est obligatoire. Les requetes sont limitees a 60 par minute et par adresse IP par defaut. Ajuste `API_RATE_LIMIT_REQUESTS` et `API_RATE_LIMIT_WINDOW_SECONDS` si necessaire.

## Notes

- PostgreSQL est la base persistante de production ; SQLite reste uniquement un fallback local.
- Ne commit jamais `.env`, les cles API ou la base SQLite.
- Le plan PostgreSQL `free` peut dependre de la disponibilite de Render. Si Render le refuse, choisis un plan PostgreSQL disponible dans le tableau de bord.
