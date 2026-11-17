"""
Connect a platform and issue it an API key.

    python manage.py create_platform ona-records --name "ONA Records" \
        --webhook-url https://ona-records-backend-staging.up.railway.app/api/v1/kyc/webhook/ \
        --key-name "ONA staging backend" [--can-review]

Running it again for the same slug updates the name and webhook URL and
issues an additional key (old keys keep working until revoked in the admin).
The key and the webhook secret are printed ONCE: put them straight into the
platform's environment variables.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from verification.models import ApiKey, Platform
from verification.webhooks import new_secret


class Command(BaseCommand):
    help = 'Create or update a platform and issue it an API key.'

    def add_arguments(self, parser):
        parser.add_argument('slug')
        parser.add_argument('--name', required=True)
        parser.add_argument('--webhook-url', default='')
        parser.add_argument('--key-name', default='default')
        parser.add_argument('--can-review', action='store_true',
                            help="Let this key approve and reject the platform's own users.")
        parser.add_argument('--rotate-webhook-secret', action='store_true')

    def handle(self, *args, slug, name, webhook_url, key_name, can_review, rotate_webhook_secret, **opts):
        if webhook_url and not webhook_url.startswith('https://') and not settings.DEBUG:
            raise CommandError('The webhook URL must use https.')
        platform, created = Platform.objects.update_or_create(
            slug=slug, defaults={'name': name, 'webhook_url': webhook_url})
        _, raw_key = ApiKey.issue(platform, key_name, can_review=can_review)

        self.stdout.write(self.style.SUCCESS(f'{"Created" if created else "Updated"} platform {slug}.'))
        self.stdout.write(f'KYC_API_KEY={raw_key}')
        if webhook_url and (created or rotate_webhook_secret or not platform.webhook_secret_encrypted):
            self.stdout.write(f'KYC_WEBHOOK_SECRET={new_secret(platform)}')
        self.stdout.write(self.style.WARNING('These values are shown once. Store them now.'))
