# BrunoLab on Amvera

Deployment branch: `amvera-deployment`. The cloud runs a clean source snapshot in
Amvera's own repository; GitHub updates are not automatically deployed.

## Web service

Use `deploy/Dockerfile` and root `amvera.yaml`. Listen on port 8080, one replica.
The initial setup uses Warsaw-0, 1 GB RAM. SQLite is at `/data/db.sqlite3`;
private uploads are at `/data/private_uploads`. Keep `/data` across deployments.
The container applies migrations before starting Daphne. WhiteNoise serves static
assets. A single process uses Channels' in-memory backend; add Redis before
scaling to multiple web processes.

Set secrets in the hosting panel, never in git or a source upload:
- DJANGO_SECRET_KEY: a persistent random secret of at least 50 characters
- DJANGO_DEBUG=0
- DJANGO_TRUST_PROXY_HTTPS=1 (Amvera terminates TLS)
- TELEGRAM_MINI_APP_URL: the exact HTTPS public origin, no trailing path
- selected AI provider keys and model configuration

The public Mini App origin is added to allowed hosts and trusted CSRF origins.
The bundled trusted root is used for GigaChat TLS verification.
Payments and local generated backend execution are disabled in cloud settings.
Generated backend applications still require a separate isolated runtime; do not
remove their local-host guard to publish them.

## Telegram launcher

Deploy the same source as a separate service with BRUNOLAB_SERVICE=bot,
TELEGRAM_BOT_TOKEN and TELEGRAM_MINI_APP_URL. Django's secret is still required
when loading production settings. Run only one long-polling process per token.
After confirming the public website works, run configure_telegram_bot using the
new public URL to update the bot menu and commands.

## Checks

`manage.py check --deploy --settings=config.amvera`, collectstatic, public login,
static assets, HTTPS redirects and Telegram launcher/menu. HSTS subdomains and
preload are intentionally not enabled for a shared provider domain.

New cloud database starts empty. Local users/projects/uploads are not copied by
shipping source. Any later migration must use a consistent private snapshot and
must not replace a cloud database that already contains new data.

## Initial publication (2026-10-19)

- Account: brunolab. Web: https://brunolab-brunolab.waw0.amvera.tech/
- Telegram: @AISaaSacceleratorbot, menu configured for the cloud website.
- Amvera repositories: `https://git.waw0.amvera.ru/brunolab/brunolab` and
  `https://git.waw0.amvera.ru/brunolab/brunolab-bot`, deployment branch `master`.
- Python image is pulled from Docker's official ECR mirror because Amvera's
  Docker Hub proxy repeatedly failed extracting images with `unexpected EOF`.
- Both containers started. Public login, CSS/JS/images returned 200; the bot
  logged successful long-polling startup. Its menu URL was checked through the
  Telegram API. Existing local accounts/projects have not been migrated.
- Initial trial balance: 110 RUB. Web + bot cost roughly 1.08 RUB/hour,
  approximately 181 RUB/week, excluding AI calls and storage above quota.
