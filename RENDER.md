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

Pour activer Mobile Money via CinetPay, configure aussi `CINETPAY_API_KEY` et `CINETPAY_SITE_ID`. Le taux par défaut est `CINETPAY_XOF_PER_USD=600`, soit `600 FCFA = 1 USD = 10 000 jetons`; remplace-le dans l'environnement Render si tu choisis un autre taux. Les montants sont demandés en FCFA et le minimum correspond à l'équivalent configuré de 1 USD. La page `/tarifs/` présente les coûts d'utilisation des modèles cloud en jetons.

Pour activer le chat cloud et les générations d'images et de vidéos, configure `OPENROUTER_API_KEY`. Les modèles média se règlent avec `OPENROUTER_IMAGE_MODEL` et `OPENROUTER_VIDEO_MODEL` (par défaut : `bytedance-seed/seedream-4.5` et `alibaba/wan-3.0`). L’audio utilise toujours les clés et modèles audio configurés séparément.

Pour activer l'API compatible OpenAI, `API_COMPATIBLE_KEY` est obligatoire. Les requetes sont limitees a 60 par minute et par adresse IP par defaut. Ajuste `API_RATE_LIMIT_REQUESTS` et `API_RATE_LIMIT_WINDOW_SECONDS` si necessaire.

Pour activer Gmail et Outlook dans Bara, renseigne `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID` et `MICROSOFT_CLIENT_SECRET` dans les variables du service. `MICROSOFT_TENANT_ID` est facultatif et vaut `common` par defaut. Enregistre les URI de redirection OAuth suivantes dans les consoles Google et Microsoft :

```text
https://<domaine-render>/bara/plugins/email/google_mail/callback/
https://<domaine-render>/bara/plugins/email/microsoft_mail/callback/
```

Google demande les scopes Gmail `gmail.readonly` et `gmail.compose`; Microsoft demande `Mail.ReadWrite` et `Mail.Send`. `Mail.ReadWrite` est plus large que les actions Outlook offertes dans Bara. Les envois restent soumis a une confirmation dans l'interface.

Pour Blender, telecharge le module complementaire depuis `AUTOMATISATIONS`, installe-le depuis les preferences Blender, puis configure l'URL Render et le jeton genere dans Bara. L'extension se connecte au service par HTTPS sortant. Elle ne recoit que les actions autorisees par Bara, et les modifications de scene necessitent une confirmation.

2AI est installable comme PWA sur le domaine HTTPS Render. Le service worker met uniquement en cache les ressources publiques sous `/static/` ; il ne met pas en cache les pages authentifiees ni les conversations.

## Notes

- PostgreSQL est la base persistante de production ; SQLite reste uniquement un fallback local.
- Ne commit jamais `.env`, les cles API ou la base SQLite.
- Le plan PostgreSQL `free` peut dependre de la disponibilite de Render. Si Render le refuse, choisis un plan PostgreSQL disponible dans le tableau de bord.
