from django.urls import path

from . import views

urlpatterns = [
    path('verifications/', views.VerificationStartView.as_view()),
    path('verifications/<uuid:pk>/', views.VerificationDetailView.as_view()),
    path('verifications/<uuid:pk>/documents/', views.DocumentUploadView.as_view()),
    path('verifications/<uuid:pk>/submit/', views.SubmitView.as_view()),
    path('subjects/<str:ref>/', views.SubjectStatusView.as_view()),

    path('review/verifications/', views.ReviewQueueView.as_view()),
    path('review/verifications/<uuid:pk>/', views.ReviewDetailView.as_view()),
    path('review/verifications/<uuid:pk>/start-review/', views.StartReviewView.as_view()),
    path('review/verifications/<uuid:pk>/decision/', views.DecisionView.as_view()),
]
