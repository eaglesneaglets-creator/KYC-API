"""
The back office. Read-mostly: platforms and keys are managed here, but
verifications are DECIDED through the review API (services.decide), never by
editing a status field — that is how the audit trail stays complete.
"""

from django.contrib import admin, messages
from django.utils import timezone

from .models import ApiKey, Event, Platform, Verification, WebhookDelivery
from .webhooks import new_secret


class ApiKeyInline(admin.TabularInline):
    model = ApiKey
    extra = 0
    fields = ['name', 'prefix', 'can_review', 'created_at', 'last_used_at', 'revoked_at']
    readonly_fields = ['name', 'prefix', 'can_review', 'created_at', 'last_used_at']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False  # keys are issued with `manage.py create_platform` so the secret is shown once


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'webhook_url', 'is_active', 'created_at']
    inlines = [ApiKeyInline]

    def get_readonly_fields(self, request, obj=None):
        # The slug names the platform's folder in the bucket; renaming it
        # would leave its existing scans under the old name.
        return ['slug'] if obj else []

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # A webhook URL added here needs a signing secret; show it once.
        if obj.webhook_url and not obj.webhook_secret_encrypted:
            self.message_user(
                request, f'Webhook secret for {obj.slug} (shown once): {new_secret(obj)}', messages.WARNING)


class EventInline(admin.TabularInline):
    model = Event
    extra = 0
    can_delete = False
    readonly_fields = ['at', 'actor', 'action', 'from_status', 'to_status', 'note']

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Verification)
class VerificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'subject', 'level', 'status', 'full_name', 'submitted_at', 'decided_at']
    list_filter = ['status', 'level', 'subject__platform']
    search_fields = ['subject__external_ref', 'full_name', 'id']
    inlines = [EventInline]
    readonly_fields = [f.name for f in Verification._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ['event_type', 'platform', 'attempts', 'delivered_at', 'last_error', 'created_at']
    list_filter = ['platform', 'event_type']
    readonly_fields = [f.name for f in WebhookDelivery._meta.fields]
    actions = ['retry_now']

    @admin.action(description='Retry now')
    def retry_now(self, request, queryset):
        queryset.filter(delivered_at__isnull=True).update(next_attempt_at=timezone.now(), attempts=0)
