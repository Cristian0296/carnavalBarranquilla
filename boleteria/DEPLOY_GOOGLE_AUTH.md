# Configurar Registro con Google (Hosting)

## 1) Variables de entorno

Define estas variables en el servidor:

```env
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=trocasjdl.com,www.trocasjdl.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://trocasjdl.com,https://www.trocasjdl.com
DJANGO_SITE_ID=1
DJANGO_SITE_DOMAIN=www.trocasjdl.com
DJANGO_SITE_NAME=Trocas JDL

ENABLE_GOOGLE_AUTH=1
GOOGLE_OAUTH_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=xxxxxxxx
```

## 2) Google Cloud Console

En el OAuth Client agrega:

- Authorized JavaScript origins:
  - `https://trocasjdl.com`
  - `https://www.trocasjdl.com`
- Authorized redirect URIs:
  - `https://trocasjdl.com/accounts/google/login/callback/`
  - `https://www.trocasjdl.com/accounts/google/login/callback/`

## 3) Comandos en el servidor

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py sync_site_domain
python manage.py collectstatic --noinput
```

Con esto, el botón de Google aparece en login/registro y el flujo OAuth queda operativo en producción.
