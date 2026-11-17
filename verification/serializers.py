from rest_framework import serializers

from . import crypto, levels, storage
from .models import Document, DocumentKind, Event, IdType, Verification
from .states import DECISIONS


class StartSerializer(serializers.Serializer):
    subject_ref = serializers.CharField(max_length=128, help_text="Your platform's id for this user.")
    level = serializers.ChoiceField(choices=sorted(levels.LEVELS), default='identity')


class DetailsSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200, required=False)
    date_of_birth = serializers.DateField(required=False, validators=[levels.validate_date_of_birth])
    id_type = serializers.ChoiceField(choices=IdType.choices, required=False)
    id_number = serializers.CharField(max_length=40, required=False, write_only=True)

    def validate_full_name(self, value):
        value = ' '.join(value.split())
        if len(value) < 3:
            raise serializers.ValidationError('Enter the full name as it appears on the ID.')
        return value


class UploadSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=DocumentKind.choices)
    file = serializers.FileField()


class DecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=sorted(DECISIONS))
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    reviewer = serializers.CharField(
        max_length=150, required=False,
        help_text='Who decided, for the audit trail. Required when a platform key decides.')


class LevelStatusSerializer(serializers.Serializer):
    verified = serializers.BooleanField(help_text='True once any verification at this level was approved.')
    latest_status = serializers.CharField(allow_null=True)
    latest_verification_id = serializers.UUIDField(allow_null=True)


class SubjectStatusSerializer(serializers.Serializer):
    """Documents the shape of GET /v1/subjects/{ref}/."""

    subject_ref = serializers.CharField()
    levels = serializers.DictField(child=LevelStatusSerializer())


class DocumentSerializer(serializers.ModelSerializer):
    purged = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ['kind', 'content_type', 'size', 'uploaded_at', 'purged']

    def get_purged(self, d) -> bool:
        return d.purged_at is not None


class VerificationSerializer(serializers.ModelSerializer):
    """What a platform sees: its own user's check, without the scans."""

    subject_ref = serializers.CharField(source='subject.external_ref', read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    missing = serializers.SerializerMethodField()

    class Meta:
        model = Verification
        fields = ['id', 'subject_ref', 'level', 'status', 'full_name', 'date_of_birth', 'id_type',
                  'id_number_last4', 'documents', 'missing', 'decision_reason',
                  'submitted_at', 'decided_at', 'created_at', 'updated_at']

    def get_missing(self, v) -> list[str]:
        return levels.missing(v)


class ReviewDocumentSerializer(DocumentSerializer):
    url = serializers.SerializerMethodField()

    class Meta(DocumentSerializer.Meta):
        fields = [*DocumentSerializer.Meta.fields, 'sha256', 'url']

    def get_url(self, d) -> str:
        return storage.url_for(d.storage_key)


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ['at', 'actor', 'action', 'from_status', 'to_status', 'note']


class ReviewListSerializer(serializers.ModelSerializer):
    platform = serializers.CharField(source='subject.platform.slug', read_only=True)
    subject_ref = serializers.CharField(source='subject.external_ref', read_only=True)

    class Meta:
        model = Verification
        fields = ['id', 'platform', 'subject_ref', 'level', 'status', 'full_name', 'flags',
                  'submitted_at', 'decided_at']


class ReviewDetailSerializer(VerificationSerializer):
    """Everything a reviewer needs to decide: the full number, the scans, the history."""

    platform = serializers.CharField(source='subject.platform.slug', read_only=True)
    id_number = serializers.SerializerMethodField()
    documents = ReviewDocumentSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)

    class Meta(VerificationSerializer.Meta):
        fields = [*VerificationSerializer.Meta.fields, 'platform', 'id_number', 'flags',
                  'decided_by', 'events']

    def get_id_number(self, v) -> str:
        return crypto.decrypt(v.id_number_encrypted)
