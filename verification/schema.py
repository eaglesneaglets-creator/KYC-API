"""Tells the generated API docs how platforms authenticate."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class PlatformKeyScheme(OpenApiAuthenticationExtension):
    target_class = 'verification.auth.PlatformKeyAuthentication'
    name = 'PlatformKey'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'kyc_<prefix>_<secret>',
            'description': 'Your platform API key, sent from your backend only.',
        }
