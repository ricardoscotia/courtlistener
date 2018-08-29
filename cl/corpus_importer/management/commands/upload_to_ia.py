import argparse
import os

import redis
from django.conf import settings
from django.db.models import Q

from cl.audio.models import Audio
from cl.audio.tasks import upload_audio_to_ia
from cl.corpus_importer.tasks import upload_pdf_to_ia
from cl.lib.celery_utils import CeleryThrottle
from cl.lib.command_utils import VerboseCommand, logger
from cl.search.models import RECAPDocument, Docket

PACER_USERNAME = os.environ.get('PACER_USERNAME', settings.PACER_USERNAME)
PACER_PASSWORD = os.environ.get('PACER_PASSWORD', settings.PACER_PASSWORD)


def upload_non_free_pdfs_to_internet_archive(options):
    upload_pdfs_to_internet_archive(options, do_non_free=True)


def upload_pdfs_to_internet_archive(options, do_non_free=False):
    """Upload items to the Internet Archive."""
    q = options['queue']
    rds = RECAPDocument.objects.filter(
        Q(ia_upload_failure_count__lt=3) | Q(ia_upload_failure_count=None),
        is_available=True,
        filepath_ia='',
    ).exclude(
        filepath_local='',
    ).values_list(
        'pk',
        flat=True,
    ).order_by()
    if do_non_free:
        rds = rds.filter(Q(is_free_on_pacer=False) | Q(is_free_on_pacer=None))
    else:
        rds = rds.filter(is_free_on_pacer=True)

    count = rds.count()
    logger.info("Sending %s items to Internet Archive.", count)
    throttle = CeleryThrottle(queue_name=q)
    for i, rd in enumerate(rds):
        throttle.maybe_wait()
        if i > 0 and i % 1000 == 0:
            logger.info("Sent %s/%s tasks to celery so far.", i, count)
        upload_pdf_to_ia.si(rd).set(queue=q).apply_async()


def upload_oral_arguments_to_internet_archive(options):
    """Upload oral arguments to the Internet Archive"""
    q = options['queue']
    af_pks = Audio.objects.filter(Q(ia_upload_failure_count__lt=3) |
                               Q(ia_upload_failure_count=None),
                               filepath_ia='')\
        .exclude(local_path_mp3='')\
        .values_list('pk', flat=True)\
        .order_by()
    count = len(af_pks)
    logger.info("Sending %s oral argument files to Internet Archive", count)
    throttle = CeleryThrottle(queue_name=q)
    for i, af_pk in enumerate(af_pks):
        throttle.maybe_wait()
        if i > 0 and i % 1000 == 0:
            logger.info("Sent %s/%s tasks to celery so far.", i, count)
        upload_audio_to_ia.si(af_pk).set(queue=q).apply_async()


def upload_recap_data(options):
    """Upload RECAP data to Internet Archive."""
    q = options['queue']
    r = redis.StrictRedis(host=settings.REDIS_HOST,
                          port=settings.REDIS_PORT,
                          db=settings.REDIS_DATABASES['CACHE'])
    redis_key = 'recap-docket-last-id'
    last_pk = r.getset(redis_key, 0)
    ds = Docket.objects.filter(source__in=Docket.RECAP_SOURCES,
                               pk__gt=last_pk).order_by('pk').only('pk')

    chunk_size = 100  # Small to save memory
    previous_last_pk = None
    while True:
        for d in ds.filter(pk__gt=last_pk)[:chunk_size]:
            # make and upload json
            last_pk = d.pk
            r.set(redis_key, last_pk)

        # Detect if we've hit the end of the loop and reset it if so. We do
        # this by keeping track of the last_pk that we saw the last time the
        # for loop changed. If that PK doesn't change after the for loop has
        # run again, then we know we've hit the end of the loop and we should
        # reset it.
        if previous_last_pk == last_pk:
            # The PK is the same as the last time
            # the for loop finished. Reset things.
            last_pk = 0
        else:
            previous_last_pk = last_pk


def do_routine_uploads(options):
    logger.info("Uploading free opinions to Internet Archive.")
    upload_pdfs_to_internet_archive(options)
    logger.info("Uploading non-free PDFs to Internet Archive.")
    upload_non_free_pdfs_to_internet_archive(options)
    logger.info("Uploading oral arguments to Internet Archive.")
    upload_oral_arguments_to_internet_archive(options)


class Command(VerboseCommand):
    help = "Get all the free content from PACER. There are three modes."

    def valid_actions(self, s):
        if s.lower() not in self.VALID_ACTIONS:
            raise argparse.ArgumentTypeError(
                "Unable to parse action. Valid actions are: %s" % (
                    ', '.join(self.VALID_ACTIONS.keys())
                )
            )

        return self.VALID_ACTIONS[s]

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=self.valid_actions,
            required=True,
            help="The action you wish to take. Valid choices are: %s" % (
                ', '.join(self.VALID_ACTIONS.keys())
            )
        )
        parser.add_argument(
            '--queue',
            default='batch1',
            help="The celery queue where the tasks should be processed.",
        )

    def handle(self, *args, **options):
        super(Command, self).handle(*args, **options)
        options['action'](options)

    VALID_ACTIONS = {
        'do-routing-uploads': do_routine_uploads,
        'upload-pdfs-to-ia': upload_pdfs_to_internet_archive,
        'upload-non-free-pdfs-to-ia': upload_non_free_pdfs_to_internet_archive,
        'upload-oral-arguments-to-ia': upload_oral_arguments_to_internet_archive,
        'upload-recap-data': upload_recap_data,
    }
