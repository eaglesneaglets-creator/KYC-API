from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health(_request):
    return JsonResponse({'status': 'ok', 'environment': settings.ENVIRONMENT_NAME})


admin.site.site_header = 'KYC review'
admin.site.site_title = 'KYC review'

urlpatterns = [
    path('health/', health),
    path('v1/', include('verification.urls')),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='docs'),
    path(settings.ADMIN_URL, admin.site.urls),
]
