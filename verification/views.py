"""
The HTTP API. Thin on purpose: validate input, find the verification the
caller may touch, call services, render.

A platform only ever sees its own users: every lookup is filtered by the
platform behind the key, and someone else's id answers 404, not 403, so ids
cannot be probed.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import levels, services
from .auth import IsPlatform, IsReviewer, is_platform
from .models import Subject, Verification
from .serializers import (
    DecisionSerializer,
    DetailsSerializer,
    DocumentSerializer,
    ReviewDetailSerializer,
    ReviewListSerializer,
    StartSerializer,
    SubjectStatusSerializer,
    UploadSerializer,
    VerificationSerializer,
)

BASE = Verification.objects.select_related('subject__platform').prefetch_related('documents')


def _actor(request, reviewer: str = '') -> str:
    if is_platform(request):
        name = f'platform:{request.user.platform.slug}'
        return f'{name}/{reviewer}' if reviewer else name
    return f'staff:{request.user.get_username()}'


def _scoped(request):
    if is_platform(request):
        return BASE.filter(subject__platform=request.user.platform)
    return BASE


# ── Platform API ──────────────────────────────────────────────────────────

class VerificationStartView(APIView):
    permission_classes = [IsPlatform]

    @extend_schema(request=StartSerializer, responses={200: VerificationSerializer, 201: VerificationSerializer},
                   summary='Start (or resume) a verification for one of your users')
    def post(self, request):
        s = StartSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v, created = services.start(request.user.platform, s.validated_data['subject_ref'],
                                    s.validated_data['level'], _actor(request))
        v = _scoped(request).get(pk=v.pk)
        return Response(VerificationSerializer(v).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class VerificationDetailView(APIView):
    permission_classes = [IsPlatform]

    @extend_schema(responses=VerificationSerializer, summary='A verification and what it still needs')
    def get(self, request, pk):
        return Response(VerificationSerializer(get_object_or_404(_scoped(request), pk=pk)).data)

    @extend_schema(request=DetailsSerializer, responses=VerificationSerializer,
                   summary='Set name, date of birth and ID details')
    def patch(self, request, pk):
        v = get_object_or_404(_scoped(request), pk=pk)
        s = DetailsSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        services.update_details(v, s.validated_data, _actor(request))
        return Response(VerificationSerializer(_scoped(request).get(pk=pk)).data)


class DocumentUploadView(APIView):
    permission_classes = [IsPlatform]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request={'multipart/form-data': UploadSerializer}, responses={201: DocumentSerializer},
                   summary='Upload an ID side or the selfie (replaces an earlier one)')
    def post(self, request, pk):
        v = get_object_or_404(_scoped(request), pk=pk)
        s = UploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        doc = services.attach_document(v, s.validated_data['kind'], s.validated_data['file'], _actor(request))
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class SubmitView(APIView):
    permission_classes = [IsPlatform]

    @extend_schema(request=None, responses=VerificationSerializer, summary='Send for review')
    def post(self, request, pk):
        v = get_object_or_404(_scoped(request), pk=pk)
        services.submit(v, _actor(request))
        return Response(VerificationSerializer(_scoped(request).get(pk=pk)).data)


class SubjectStatusView(APIView):
    """What a platform gates features on: is this user verified, per level?"""

    permission_classes = [IsPlatform]

    @extend_schema(responses=SubjectStatusSerializer, summary="A user's current status at every level")
    def get(self, request, ref):
        subject = get_object_or_404(Subject, platform=request.user.platform, external_ref=ref)
        out = {}
        for level in levels.LEVELS:
            latest = subject.verifications.filter(level=level).order_by('-created_at').first()
            approved = subject.verifications.filter(level=level, status='approved').exists()
            out[level] = {
                'verified': approved,
                'latest_status': latest.status if latest else None,
                'latest_verification_id': str(latest.id) if latest else None,
            }
        return Response({'subject_ref': ref, 'levels': out})


# ── Review API ────────────────────────────────────────────────────────────

class ReviewQueueView(generics.ListAPIView):
    permission_classes = [IsReviewer]
    serializer_class = ReviewListSerializer

    @extend_schema(summary='Verifications waiting for a decision (filter with ?status=)')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        wanted = self.request.query_params.get('status', 'submitted,in_review').split(',')
        # Oldest first: whoever has waited longest is reviewed next.
        return _scoped(self.request).filter(status__in=wanted).order_by('submitted_at', 'created_at')


class ReviewDetailView(APIView):
    permission_classes = [IsReviewer]

    @extend_schema(responses=ReviewDetailSerializer,
                   summary='The full record: ID number, scan links, history. Each view is logged.')
    def get(self, request, pk):
        v = get_object_or_404(_scoped(request).prefetch_related('events'), pk=pk)
        services.record_view(v, _actor(request, request.query_params.get('reviewer', '')))
        return Response(ReviewDetailSerializer(v).data)


class StartReviewView(APIView):
    permission_classes = [IsReviewer]

    @extend_schema(request=None, responses=ReviewListSerializer, summary='Claim a submitted verification')
    def post(self, request, pk):
        v = get_object_or_404(_scoped(request), pk=pk)
        v = services.start_review(v, _actor(request, request.data.get('reviewer', '')))
        return Response(ReviewListSerializer(v).data)


class DecisionView(APIView):
    permission_classes = [IsReviewer]

    @extend_schema(request=DecisionSerializer, responses=ReviewListSerializer,
                   summary='Approve, reject, or ask for changes')
    def post(self, request, pk):
        v = get_object_or_404(_scoped(request), pk=pk)
        s = DecisionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        reviewer = s.validated_data.get('reviewer', '').strip()
        if is_platform(request) and not reviewer:
            raise ValidationError({'reviewer': 'Say who decided (e.g. their email), for the audit trail.'})
        v = services.decide(v, s.validated_data['decision'], _actor(request, reviewer),
                            s.validated_data.get('reason', ''))
        return Response(ReviewListSerializer(v).data)
