"""
Move stored scans into their platform's folder.

    python manage.py relocate_documents --dry-run     # list what would move
    python manage.py relocate_documents               # move it

Each file is copied inside the bucket to
<S3_KEY_PREFIX>/platforms/<slug>/<verification id>/<kind>-<random>.<ext>,
the Document is pointed at the copy, and only then is the old object
deleted. Stopped half-way, the worst left behind is an extra copy, never a
Document pointing at nothing. Safe to run again: files already in place are
skipped.
"""

import posixpath

from django.core.management.base import BaseCommand
from django.db import transaction

from verification import storage
from verification.models import Document, Event


class Command(BaseCommand):
    help = "Move stored ID scans into their platform's folder in the bucket."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only list what would move.')

    def handle(self, *args, dry_run, **opts):
        docs = (Document.objects.select_related('verification__subject__platform')
                .exclude(storage_key='').order_by('uploaded_at'))
        moved = skipped = failed = 0
        for doc in docs.iterator():
            v = doc.verification
            slug = v.subject.platform.slug
            if doc.storage_key.startswith(f'{storage.platform_prefix(slug)}{v.pk}/'):
                skipped += 1
                continue
            ext = posixpath.splitext(doc.storage_key)[1]
            new_key = storage.document_key(slug, v.pk, doc.kind, ext)
            if dry_run:
                self.stdout.write(f'would move {doc.kind} of {v.pk} ({slug})')
                moved += 1
                continue
            if not storage.copy(doc.storage_key, new_key):
                failed += 1
                self.stderr.write(f'copy failed: {doc.kind} of {v.pk}; left where it is')
                continue
            old_key = doc.storage_key
            with transaction.atomic():
                # Re-read under lock: an upload may have replaced it meanwhile.
                current = Document.objects.select_for_update().get(pk=doc.pk)
                if current.storage_key != old_key:
                    storage.delete(new_key)
                    skipped += 1
                    continue
                current.storage_key = new_key
                current.save(update_fields=['storage_key'])
                Event.objects.create(verification=v, actor='system', action='document_relocated',
                                     note=doc.kind)
            storage.delete(old_key)
            moved += 1

        verb = 'Would move' if dry_run else 'Moved'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {moved}; already in place {skipped}; failed {failed}.'))
        if failed:
            raise SystemExit(1)
