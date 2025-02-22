from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
from invoke import task
import internetarchive
import itertools
import json
import os
import pytz
import re
import requests
import signal
import subprocess
import surt
import sys
from tqdm import tqdm
import time


from django.conf import settings
from django.core.files.storage import default_storage
from django.db import connections
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone

from perma.email import send_user_email, send_self_email, registrar_users, registrar_users_plus_stats
from perma.models import Capture, Folder, HistoricalLink, Link, LinkUser, Organization, Registrar, WeekStats


import logging
logger = logging.getLogger(__name__)

@task
def run(ctx, port="0.0.0.0:8000", cert_file='perma-test.crt', key_file='perma-test.key', debug_toolbar=False):
    """
        Run django test server on open port, so it's accessible outside Docker.

        Use runserver_plus for SSL.
    """
    commands = []

    if settings.CELERY_TASK_ALWAYS_EAGER:
        print("\nWarning! Batch Link creation will not work as expected:\n" +
              "to create new batches you should run with settings.CELERY_TASK_ALWAYS_EAGER = False\n")
    else:
        print("\nStarting background celery process.")
        commands.append("watchmedo auto-restart -d ./ -p '*.py' -R -- celery -A perma worker --loglevel=info -Q celery,background,ia,ia-readonly -B -n w1@%h")

    if settings.PROXY_CAPTURES:
        print("\nStarting Tor service in the background.")
        commands.append('tor')

    # Only run the webpack background process in debug mode -- with debug False, dev server uses static assets,
    # and running webpack just messes up the webpack stats file.
    # Similarly, don't download fresh replayweb.page assets, to avoid confusion.
    if settings.DEBUG:
        commands.append('npm start')
        commands.append("watchmedo auto-restart -d ./perma/settings/ -p '*.py' -R -- invoke dev.get-replay-assets")

    proc_list = [subprocess.Popen(command, shell=True, stdout=sys.stdout, stderr=sys.stderr) for command in commands]

    with ctx.prefix(f'export DEBUG_TOOLBAR={"1" if debug_toolbar else ""}'):

        try:
            # use runserver_plus
            import django_extensions  # noqa
            if not os.path.exists(os.path.join(settings.PROJECT_ROOT, cert_file)) or not os.path.exists(os.path.join(settings.PROJECT_ROOT, key_file)):
                print("\nError! The required SSL cert and key files are missing. See developer.md for instructions on how to generate.")
                return
            options = f'--cert-file {cert_file} --key-file {key_file} --keep-meta-shutdown'
            ctx.run(f"python manage.py runserver_plus {port} {options}")
        finally:
            for proc in proc_list:
                os.kill(proc.pid, signal.SIGKILL)


_default_tests = "functional_tests perma api lockss"

@task
def test(ctx, apps=_default_tests):
    """ Run perma tests. (For coverage, run `coverage report` after tests pass.) """
    test_python(ctx, apps)
    if apps == _default_tests:
        test_js(ctx)

@task
def test_python(ctx, apps=_default_tests):
    """ Run Python tests. """

    # In order to run functional_tests, we have to run collectstatic, since functional tests use DEBUG=False
    # For speed we use the default Django STATICFILES_STORAGE setting here, which also has to be set in settings_testing.py
    if "functional_tests" in apps:
        ctx.run("DJANGO__STATICFILES_STORAGE=django.contrib.staticfiles.storage.StaticFilesStorage python manage.py collectstatic --noinput")

    ctx.run(f"pytest {apps} --no-migrations --ds=perma.settings.deployments.settings_testing --cov --cov-config=setup.cfg --cov-report= ")

@task
def test_js(ctx):
    """ Run Javascript tests. """
    ctx.run("npm test")


@task
def pip_compile(ctx, args=''):
    # run pip-compile
    # Use --allow-unsafe because pip --require-hashes needs all requirements to be pinned, including those like
    # setuptools that pip-compile leaves out by default.
    command = ['pip-compile', '--generate-hashes', '--allow-unsafe']+args.split()
    print("Calling %s" % " ".join(command))
    subprocess.check_call(command, env=dict(os.environ, CUSTOM_COMPILE_COMMAND='invoke pip-compile'))


@task()
def get_replay_assets(ctx):
    for asset in ['sw.js', 'ui.js', 'ruffle/ruffle.js']:
        dest = os.path.join(settings.PROJECT_ROOT, f'static/vendors/replay-web-page/{asset}')
        with open(dest, 'wb') as file:
            url = f'{settings.REPLAYWEBPAGE_SOURCE_URL}@{settings.REPLAYWEBPAGE_VERSION}/{asset}'
            print(f'Downloading {url} to {dest}')
            response = requests.get(url)
            file.write(response.content)


@task
def logs(ctx, log_dir=os.path.join(settings.PROJECT_ROOT, '../services/logs/')):
    """ Tail all logs. """
    ctx.run(f"tail -f {log_dir}/*")


@task
def init_db(ctx):
    """
        Apply migrations and import fixtures for new dev database.
    """
    ctx.run("python manage.py migrate")
    ctx.run("python manage.py loaddata fixtures/sites.json fixtures/users.json fixtures/folders.json")


@task
def build_week_stats(ctx):
    """
        A temporary helper to populate our weekly stats
    """
    # regenerate all weekly stats
    WeekStats.objects.all().delete()

    oldest_link = Link.objects.earliest('creation_timestamp')

    # this is always the end date in our range, usually a saturday
    date_of_stats = oldest_link.creation_timestamp

    # this is the start date in our range, always a sunday
    start_date = date_of_stats

    links_this_week = 0
    users_this_week = 0
    orgs_this_week = 0
    registrars_this_week = 0

    while date_of_stats < timezone.now():
        links_this_week += Link.objects.filter(creation_timestamp__year=date_of_stats.year,
            creation_timestamp__month=date_of_stats.month, creation_timestamp__day=date_of_stats.day).count()

        users_this_week += LinkUser.objects.filter(date_joined__year=date_of_stats.year,
            date_joined__month=date_of_stats.month, date_joined__day=date_of_stats.day).count()

        orgs_this_week += Organization.objects.filter(date_created__year=date_of_stats.year,
            date_created__month=date_of_stats.month, date_created__day=date_of_stats.day).count()

        registrars_this_week += Registrar.objects.approved().filter(date_created__year=date_of_stats.year,
            date_created__month=date_of_stats.month, date_created__day=date_of_stats.day).count()

        # if this is a saturday, write our sums and reset our counts
        if date_of_stats.weekday() == 5:
            week_of_stats = WeekStats(start_date=start_date, end_date=date_of_stats, links_sum=links_this_week,
                users_sum=users_this_week, organizations_sum=orgs_this_week, registrars_sum=registrars_this_week)
            week_of_stats.save()

            links_this_week = 0
            users_this_week = 0
            orgs_this_week = 0
            registrars_this_week = 0

            start_date = date_of_stats + timedelta(days=1)

        date_of_stats += timedelta(days=1)


@task
def count_pending_ia_links(ctx):
    """
    For use in monitoring the size of the queue.
    """
    count = Link.objects.visible_to_ia().filter(
        internet_archive_upload_status__in=['not_started', 'failed', 'upload_or_reupload_required', 'deleted']
    ).count()
    print(count)


@task
def count_links_without_cached_playback_status(ctx):
    """
    For use in monitoring the size of the queue.
    """
    count = Link.objects.permanent().filter(cached_can_play_back__isnull=True).count()
    print(count)


@task
def rebuild_folder_trees(ctx):
    print("Checking for broken folder trees ...")

    for o in Organization.objects.all():
        if set(o.folders.all()) != set(o.shared_folder.get_descendants(include_self=True)):
            print(f"Tree corruption found for org: {o}")
            Folder._tree_manager.partial_rebuild(o.shared_folder.tree_id)

    for u in LinkUser.objects.all():
        if u.root_folder and set(u.folders.all()) != set(u.root_folder.get_descendants(include_self=True)):
            print(f"Tree corruption found for user: {u}")
            Folder._tree_manager.partial_rebuild(u.root_folder.tree_id)


@task
def ping_all_users(ctx, limit_to="", exclude="", batch_size=500):
    '''
       Sends an email to all our current users. See templates/email/special.txt

       Arguments should be strings, with multiple values separated by semi-colons
       e.g. invoke ping-all-users --limit-to "14;27;30" --batch-size 1000

       Limit filters are applied before exclude filters.
    '''
    logger.info("BEGIN: ping_all_users")

    # load desired Perma users
    if limit_to:
        users = LinkUser.objects.filter(id__in=limit_to.split(";"))
    else:
        users = LinkUser.objects.filter(is_confirmed=True, is_active=True)
    if exclude:
        users = users.exclude(id__in=exclude.split(";"))

    # exclude users we have already emailed
    already_emailed_path = '/tmp/perma_emailed_user_list'
    already_emailed = set()
    if os.path.exists(already_emailed_path):
        logging.info("Loading list of already-emailed users.")
        with open(already_emailed_path) as f:
            lines = f.read().splitlines()
            for line in lines:
                already_emailed.add(int(line))
    if already_emailed:
        users = users.exclude(id__in=already_emailed)

    # limit to our desired batch size
    not_yet_emailed = users.count()
    if not_yet_emailed > batch_size:
        logger.info(f"{not_yet_emailed} users to email: limiting to first {batch_size}")
        users = users[:batch_size]

    to_send_count = users.count()
    if not to_send_count:
        logger.info("No users to email.")
        return

    sent_count = 0
    failed_list = []
    logger.info(f"Begin emailing {to_send_count} users.")
    with open(already_emailed_path, 'a') as f:
        for user in tqdm(users):
            succeeded = send_user_email(user.email,
                                        'email/special.txt',
                                         {'user': user})
            if succeeded:
                sent_count += 1
                f.write(str(user.id)+"\n")
            else:
                failed_list.append(user.id)

    logger.info(f"Emailed {sent_count} users")
    if to_send_count != sent_count:
        if failed_list:
            msg = f"Some users were not emailed: {str(failed_list)}. Check log for fatal SMTP errors."
        else:
            msg = "Some users were not emailed. Check log for fatal SMTP errors."
        logger.error(msg)

    # offer to send another batch if there are any users left to email
    remaining_to_email = not_yet_emailed - sent_count
    if remaining_to_email:
        if input(f"\nSend another batch of size {batch_size}? [y/n]\n").lower() == 'y':
            ping_all_users(batch_size=str(batch_size))
        else:
            logger.info(f"Stopped with ~ {remaining_to_email} remaining users to email")
    else:
        logger.info("Done! Run me again, to catch anybody who signed up while this was running!")


@task
def ping_registrar_users(ctx, limit_to="", limit_by_tag="", exclude="", exclude_by_tag="", email="stats", year=""):
    '''
       Sends an email to our current registrar users. See templates/email/registrar_user_ping.txt

       Arguments should be strings, with multiple values separated by semi-colons
       e.g. invoke ping-registrar-users --limit-to "14;27;30" --exclude-by-tag "opted_out" --email "special"

       Limit filters are applied before exclude filters.
    '''
    registrars = Registrar.objects.all()
    if limit_to:
        registrars = registrars.filter(id__in=limit_to.split(";"))
    if limit_by_tag:
        registrars = registrars.filter(tags__name__in=limit_by_tag.split(";")).distinct()
    if exclude:
        registrars = registrars.exclude(id__in=exclude.split(";"))
    if exclude_by_tag:
        registrars = registrars.exclude(tags__name__in=exclude_by_tag.split(";")).distinct()
    if year:
        year = int(year)
    else:
        year = datetime.now().year - 1

    if email == 'stats':
        template = 'email/registrar_user_ping.txt'
        users = registrar_users_plus_stats(registrars=registrars, year=year)
    elif email == 'special':
        # update special template as desired, to send one-off emails
        # update email.registrar_users if you need more context variables
        template = 'email/special.txt'
        users = registrar_users(registrars=registrars)
    else:
        NotImplementedError()

    logger.info("Begin emailing registrar users.")
    send_count = 0
    failed_list = []
    for user in users:
        context = {}
        context.update(user)
        context["year"] = year
        succeeded = send_user_email(user['email'],
                                    template,
                                     context)
        if succeeded:
            send_count += 1
        else:
            failed_list.append(user.id)

    # Another option is to use Django's send_mass_email.
    # It's unclear which would be more performant in real life.
    # send_count = send_mass_user_email('email/registrar_user_ping.txt',
    #                                   [(user['email'], user) for user in users])
    logger.info("Done emailing registrar users.")
    if len(users) != send_count:
        if failed_list:
            msg = f"Some registrar users were not emailed: {failed_list}. Check log for fatal SMTP errors."
        else:
            msg = "Some registrar users were not emailed. Check log for fatal SMTP errors."
        logger.error(msg)
        result = "incomplete"
    else:
        result = "ok"
    send_self_email("Registrar Users Emailed",
                     HttpRequest(),
                     'email/admin/pinged_registrar_users.txt',
                     {"users": users, "result": result})
    return json.dumps({"result": result, "send_count": send_count})


@task
def fix_ia_metadata(ctx):
    """
        One-off helper function, kept for example purposes. Update all existing IA uploads to remove `sponsor` metadata.
    """
    for link in Link.objects.filter(internet_archive_upload_status='completed').order_by('guid').values('guid'):
        result = 'success'
        identifier = settings.INTERNET_ARCHIVE_IDENTIFIER_PREFIX + link['guid']
        try:
            item = internetarchive.get_item(identifier)
            if item.exists and item.metadata.get('sponsor'):
                item.modify_metadata({"sponsor": "REMOVE_TAG"},
                                     access_key=settings.INTERNET_ARCHIVE_ACCESS_KEY,
                                     secret_key=settings.INTERNET_ARCHIVE_SECRET_KEY)
        except Exception as e:
            result = str(e)
        print(f"{link['guid']}\t{result}")


@task
def check_s3_hashes(ctx):
    """
        Confirm that files in primary (disk) storage are also in secondary (s3) storage.

        One-off helper function, kept for example purposes.
    """
    local_cache_path = '/tmp/perma_local_file_list'
    remote_cache_path = '/tmp/perma_remote_file_list'
    remote_paths = {}

    if not os.path.exists(local_cache_path):
        print("Building local state ...")
        local_warc_path = os.path.join(settings.MEDIA_ROOT, settings.WARC_STORAGE_DIR)
        remove_char_count = len(settings.MEDIA_ROOT+1)
        with open(local_cache_path, 'w') as tmp_file:
            for root, subdirs, files in tqdm(os.walk(local_warc_path)):
                for f in files:
                    tmp_file.write(os.path.join(root, f)[remove_char_count:]+"\n")
    else:
        print(f"Using cached local state from {local_cache_path}")

    if not os.path.exists(remote_cache_path):
        print("Building remote state ...")
        remove_char_count = len(settings.SECONDARY_MEDIA_ROOT)
        with open(remote_cache_path, 'w') as tmp_file:
            for f in tqdm(default_storage.secondary_storage.bucket.list('generated/warcs/')):
                key = f.key[remove_char_count:]
                val = f.etag[1:-1]
                tmp_file.write(f"{key}\t{val}\n")
                remote_paths[key] = val
    else:
        print(f"Using cached remote state from {remote_cache_path}")
        for line in open(remote_cache_path):
            key, val = line[:-1].split("\t")
            remote_paths[key] = val

    print("Comparing local and remote ...")
    blocksize = 2 ** 20
    for local_path in tqdm(open(local_cache_path)):
        local_path = local_path[:-1]
        if local_path not in remote_paths:
            print("Missing from remote:", local_path)
            continue
        m = hashlib.md5()
        with open(os.path.join(settings.MEDIA_ROOT, local_path), "rb") as f:
            while True:
                buf = f.read(blocksize)
                if not buf:
                    break
                m.update(buf)
        if m.hexdigest() != remote_paths[local_path]:
            print(f"Hash mismatch! Local: {m.hexdigest()} Remote: {remote_paths[local_path]}")


@task
def check_storage(ctx, start_date=None):
    """
        Confirm that, for every link, there is a WARC in each storage, and that their hashes match.

        start_date is in the format YYYY-MM-DD

        Ground truth is the list of link objects: compare its list of warcs with those of each storage,
        and compare hashes when more than one such file is present.

        Derived from check_s3_hashes
    """
    # check the arg
    if not start_date:
        # use first archive date
        start_datetime = Link.objects.order_by('creation_timestamp')[0].creation_timestamp
    elif re.match(r'^\d\d\d\d-\d\d-\d\d$', start_date):
        start_datetime = pytz.utc.localize(datetime.strptime(start_date, "%Y-%m-%d"))
    else:
        print("Bad argument")
        return
    end_datetime = pytz.utc.localize(datetime.now())

    # The abstraction of multiple storages is an artifact of the
    # transition to S3 for storage; although it's conceivable that we'd
    # want multiple storages at some point again, there's no need right now
    # to diverge from the Django norm. The abstraction remains here as a
    # point of historical interest.
    storages = {'primary': {'storage': default_storage, 'lookup': {}}}

    # only use cache files when all are present: link cache, and one for each storage
    link_cache = '/tmp/perma_link_cache{0}.txt'.format("" if start_date is None else start_date)
    caches = [link_cache]
    for key in storages:
        caches.append('/tmp/perma_storage_cache_{0}{1}.txt'.format(key, "" if start_date is None else start_date))

    if not all(os.path.exists(p) for p in caches):
        print("Building link cache ...")
        with open(link_cache, 'w') as tmp_file:
            capture_filter = (Q(role="primary") & Q(status="success")) | (Q(role="screenshot") & Q(status="success"))
            # assemble list of links by year-month, as in lockss/views.titledb:
            start_month = date(year=start_datetime.year, month=start_datetime.month, day=1)
            today = date.today()
            while start_month <= today:
                for link in Link.objects.filter(
                        creation_timestamp__year=start_month.year,
                        creation_timestamp__month=start_month.month,
                        creation_timestamp__gte=start_datetime,
                        creation_timestamp__lt=end_datetime,
                        captures__in=Capture.objects.filter(capture_filter)
                ).distinct():
                    tmp_file.write(f"{link.warc_storage_file()}\n")
                    # this produces strings like u'warcs/0G/GO/XR/XG/0-GGOX-RXGQ.warc.gz'; make the storage paths match
                    # by chopping off the prefix, whether storage.location, ._root_path, or .base_location
                start_month += relativedelta(months=1)

        print("Building storage cache{0} ...".format("s" if len(storages) > 1 else ""))
        for key in storages:
            storage = storages[key]['storage']
            with open('/tmp/perma_storage_cache_{0}{1}.txt'.format(key, "" if start_date is None else start_date), 'w') as tmp_file:
                if hasattr(storage, 'bucket'):
                    # S3
                    for f in storage.bucket.list('generated/warcs/'):
                        if (not start_date) or (start_datetime <= pytz.utc.localize(datetime.strptime(f.last_modified, '%Y-%m-%dT%H:%M:%S.%fZ')) < end_datetime):
                            # here we chop off the prefix aka storage.location
                            path = f.key[(len(storage.location)):]
                            # etag is a string like u'"3ea8c903d9991d466ec437d1789379a6"', so we need to
                            # knock off the extra quotation marks
                            hash = f.etag[1:-1]
                            tmp_file.write(f"{path}\t{hash}\n")
                            storages[key]['lookup'][path] = hash
                else:
                    if hasattr(storage, '_root_path'):
                        # SFTP -- no longer in use, but leaving this here to show that different storages may have
                        # different bases
                        base = storage._root_path
                    else:
                        # local file storage -- are there other possibilities to consider?
                        base = storage.base_location
                    for f in storage.walk(os.path.join(base, 'warcs')):
                        # os.walk: "For each directory in the tree rooted at directory top (including top itself),
                        # it yields a 3-tuple (dirpath, dirnames, filenames)" -- so:
                        for filename in f[2]:
                            full_path = os.path.join(f[0], filename)
                            if (not start_date) or (start_datetime <= pytz.utc.localize(storage.modified_time(full_path)) < end_datetime):
                                # here we chop off the prefix, whether storage._root_path or storage.base_location
                                path = full_path[len(base):]
                                # note that etags are not always md5sums, but should be in these cases; we can rewrite
                                # or replace md5hash if necessary
                                hash = md5hash(full_path, storage)
                                tmp_file.write(f"{path}\t{hash}\n")
                                storages[key]['lookup'][path] = hash
    else:
        print("Reading storage caches ...")
        for key in storages:
            with open('/tmp/perma_storage_cache_{0}{1}.txt'.format(key, "" if start_date is None else start_date)) as f:
                for line in f:
                    path, hash = line[:-1].split("\t")
                    storages[key]['lookup'][path] = hash

    # now check ground truth against storage lookup tables
    print("Comparing link cache against storage caches ...")
    with open(link_cache) as f:
        for line in f:
            path = line[:-1]
            file_present = True
            for key in storages:
                if path not in storages[key]['lookup']:
                    print(f"{path} not in {key}")
                    file_present = False
            if file_present and len(storages) > 1:
                hashes = []
                for key in storages:
                    hashes.append(storages[key]['lookup'][path])
                # this looks funny (and is unnecessary here) but is faster than using set, per
                # http://stackoverflow.com/a/3844948/4074877
                if hashes.count(hashes[0]) != len(hashes):
                    print("Hash mismatch for {0}: {1}".format(path, str(zip(storages.keys(), hashes))))


def md5hash(path, storage):
    """
    helper function to calculate MD5 hash of a file

    """
    blocksize = 2 ** 20
    m = hashlib.md5()
    with storage.open(path) as f:
        while True:
            buf = f.read(blocksize)
            if not buf:
                break
            m.update(buf)
        return m.hexdigest()


@task
def update_cloudflare_cache(ctx):
    """ Update Cloudflare IP lists. """
    for ip_filename in ('ips-v4', 'ips-v6'):
        with open(os.path.join(settings.CLOUDFLARE_DIR, ip_filename), 'w') as ip_file:
            ip_file.write(requests.get(f'https://www.cloudflare.com/{ip_filename}').text)


@task
def test_db_connection(ctx, connection):
    """
    Open a database connection.
    Use this task repeatedly, possibly with different database connection settings,
    e.g. in order to flush out a transient SSL connection problem, something like:
    while [ 1 ] ; do date ; invoke dev.test-db-connection "some-connection" ; sleep 1 ; done
    """
    print(f"Attempting connection to {connection} ...")
    cursor = connections[connection].cursor()
    print("Succeeded.")
    cursor.close()


@task
def populate_link_surt_column(ctx, batch_size=500, model='Link'):
    logger.info("BEGIN: populate_link_surt_column")

    models = {'Link': Link, 'HistoricalLink': HistoricalLink}
    links = models[model].objects.filter(submitted_url_surt__isnull=True)

    # limit to our desired batch size
    not_populated = links.count()
    if not_populated > batch_size:
        logger.info(f"{not_populated} links to update: limiting to first {batch_size}")
        links = links[:batch_size]

    to_update = links.count()
    if not to_update:
        logger.info("No links to update.")
        return

    for link in tqdm(links):
        link.submitted_url_surt = surt.surt(link.submitted_url)
        link.save()

    # offer to send another batch if there are any links left to update
    remaining_to_update = not_populated - to_update
    if remaining_to_update:
        if input(f"\nSend another batch of size {batch_size}? [y/n]\n").lower() == 'y':
            populate_link_surt_column(batch_size=str(batch_size), model=model)
        else:
            logger.info(f"Stopped with ~ {remaining_to_update} remaining {model}s to update")
    else:
        logger.info(f"No more {model}s left to update!")


@task
def populate_folder_cached_path(ctx, batch_size=500):
    logger.info("BEGIN: populate_folder_cached_path")

    folders = Folder.objects.filter(cached_path__isnull=True)

    # limit to our desired batch size
    not_populated = folders.count()
    if not_populated > batch_size:
        logger.info(f"{not_populated} folders to update: limiting to first {batch_size}")
        folders = folders[:batch_size]

    to_update = folders.count()
    if not to_update:
        logger.info("No folders to update.")
        return

    for folder in tqdm(folders):
        folder.cached_path = folder.get_path()
        folder.save()

    # offer to send another batch if there are any links left to update
    remaining_to_update = not_populated - to_update
    if remaining_to_update:
        if input(f"\nSend another batch of size {batch_size}? [y/n]\n").lower() == 'y':
            populate_folder_cached_path(batch_size=str(batch_size))
        else:
            logger.info(f"Stopped with ~ {remaining_to_update} remaining folders to update")
    else:
        logger.info("No more folders left to update!")

#
# Merge user accounts
#

TRANSFERRED_ORG_LINKS_CSV = 'merge_reports/tranferred_org_links.csv'
TRANSFERRED_PERSONAL_LINKS_CSV = 'merge_reports/tranferred_personal_links.csv'
MERGED_USERS_CSV = 'merge_reports/merged_users.csv'
RETAINED_USERS_CSV = 'merge_reports/retained_users.csv'

def initialize_csvs():
    for filename in [TRANSFERRED_ORG_LINKS_CSV, TRANSFERRED_PERSONAL_LINKS_CSV, MERGED_USERS_CSV, RETAINED_USERS_CSV]:
        os.makedirs(os.path.dirname(filename), exist_ok=True)

    with open(TRANSFERRED_ORG_LINKS_CSV, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        writer.writerow(['guid', 'from user id', 'to user id', 'moved at'])

    with open(TRANSFERRED_PERSONAL_LINKS_CSV, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        writer.writerow(['guid', 'from user id', 'from folder id', 'to user id', 'to folder id', 'moved at'])

    with open(MERGED_USERS_CSV, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        writer.writerow([
            'user id',
            'original email',
            'changed to placeholder',
            'normalized email',
            'merged with user id',
            'org links transferred',
            'personal links transferred',
            'merged at'
        ])

    with open(RETAINED_USERS_CSV, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        writer.writerow(['user id', 'original email', 'normalized email', 'merged with accounts', 'merged at'])


def merge_accounts(
        to_keep,
        to_delete,
        copy_memberships=True,
        transfer_links=False):

    #
    # Make sure the 'kept' account belongs to the same registrar, or the same orgs, as the other accounts
    #
    if copy_memberships:
        try:
            to_keep.copy_memberships_from_users(itertools.chain([to_keep], to_delete))
        except AssertionError as e:
            logger.error(f"MERGING: Could not merge users {to_keep.id}, {', '.join([str(u.id) for u in to_delete])}: {str(e)}")
            return

    #
    # If we know we need to move links around, do so: first org links, then personal links.
    #
    updated_org_links = defaultdict(lambda: 0)
    updated_personal_links = defaultdict(lambda: 0)
    if transfer_links:
        # Find all links in org folders and change 'created_by' to the new ID.
        org_links = Link.objects.filter(created_by__in=to_delete, organization__isnull=False)
        with open(TRANSFERRED_ORG_LINKS_CSV, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter='|')
            for link in org_links:
                writer.writerow([link.guid, link.created_by_id, to_keep.id, timezone.now()])
                updated_org_links[link.created_by_id] += 1
        org_links.update(
            created_by=to_keep
        )

        # Then, move all the Personal Links into the target account's Personal Links folder,
        # in addition to setting 'created_by' to the new ID. We know from studying the accounts
        # in question that folder tree structure can be ignored.
        for user in to_delete:
            personal_links = Link.folders.through.objects.filter(link__in=user.created_links.all())
            with open(TRANSFERRED_PERSONAL_LINKS_CSV, 'a', newline='') as csvfile:
                writer = csv.writer(csvfile, delimiter='|')
                for lf in personal_links:
                    writer.writerow([lf.link_id, lf.link.created_by_id, lf.folder_id, to_keep.id, to_keep.root_folder_id, timezone.now()])
            updated_personal_links[user.id] += personal_links.update(folder_id=to_keep.root_folder_id)
            user.created_links.all().update(created_by_id=to_keep.id)

    #
    # Finally, soft-delete the redundant accounts...
    #
    with open(MERGED_USERS_CSV, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        for user in to_delete:
            original_email, placeholder_email = user.soft_delete_after_merge_with_user(to_keep)
            writer.writerow([
                user.id,
                original_email,
                placeholder_email,
                original_email.lower(),
                to_keep.id,
                updated_org_links[user.id],
                updated_personal_links[user.id],
                timezone.now()
            ])

    #
    # ...and update our records.
    #
    merged_with = ', '.join([str(user.id) for user in to_delete])
    to_keep.prepend_to_notes(f"Merged with {merged_with}")
    if updated_org_links or updated_personal_links:
        to_keep.link_count = to_keep.created_links.count()
    to_keep.save(update_fields=['notes', 'link_count'])
    with open(RETAINED_USERS_CSV, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, delimiter='|')
        writer.writerow([
            to_keep.id,
            to_keep.email,
            to_keep.email.lower(),
            merged_with,
            timezone.now()
        ])


def unmerge_accounts(from_user_id, log_to_file=None):
    user = LinkUser.objects.get(id=from_user_id)
    if match := re.search(r"\n*Merged with (?P<user_ids>.+)", user.notes):
        user_ids = [int(uid) for uid in match.group('user_ids').split(', ')]
    else:
        logger.info(f"MERGING: Found no accounts to unmerge from user {from_user_id}.")
        return

    reversed_org_links = defaultdict(list)
    reversed_personal_links = defaultdict(list)

    # Find all transferred org links and change 'created_by' to the old ID.
    with open(TRANSFERRED_ORG_LINKS_CSV, 'r', newline='') as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter='|')
        to_reverse = []
        for row in csv_reader:
            if int(row['from user id']) in user_ids:
                guid = row['guid']
                original_creator = int(row['from user id'])
                link = Link.objects.get(guid=guid)
                link.created_by_id = original_creator
                to_reverse.append(link)
                reversed_org_links[original_creator].append(guid)
        Link.objects.bulk_update(to_reverse, ['created_by_id'])

    # Find all transferred personal links and move them back to their original folder,
    # in addition to setting 'created_by' to the original ID.
    with open(TRANSFERRED_PERSONAL_LINKS_CSV, 'r', newline='') as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter='|')
        lfs_to_reverse = []
        links_to_reverse = []
        for row in csv_reader:
            if int(row['from user id']) in user_ids:
                guid = row['guid']
                original_creator = int(row['from user id'])
                original_folder = int(row['from folder id'])
                lf = Link.folders.through.objects.select_related('link').get(link_id=guid)
                lf.folder_id = original_folder
                lf.link.created_by_id = original_creator
                lfs_to_reverse.append(lf)
                links_to_reverse.append(lf.link)
                reversed_personal_links[original_creator].append(guid)
        Link.folders.through.objects.bulk_update(lfs_to_reverse, ['folder_id'])
        Link.objects.bulk_update(links_to_reverse, ['created_by_id'])

    # Reverse the soft-deletions
    with open(MERGED_USERS_CSV, 'r', newline='') as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter='|')
        to_reverse = []
        from_users = set()

        for row in csv_reader:
            if int(row['user id']) in user_ids:
                original_email = row['original email']
                from_user_id = int(row['merged with user id'])

                user = LinkUser.objects.get(id=int(row['user id']))
                user.email = original_email
                user.is_active = user.is_confirmed
                user.link_count = int(row['org links transferred']) + int(row['personal links transferred'])

                if match := re.search(r"\n*Original registrar: (?P<registrar_id>\d+)", user.notes):
                    user.registrar_id = int(match.group('registrar_id'))
                    user.remove_line_from_notes('Original registrar')

                if match := re.search(r"\n*Original orgs: (?P<org_ids>.+)", user.notes):
                    orgs = [int(oid) for oid in match.group('org_ids').split(', ')]
                    user.organizations.add(*orgs)
                    user.remove_line_from_notes('Original orgs')

                user.prepend_to_notes(f'Previously merged with user { from_user_id }.')
                user.remove_line_from_notes('Original email')
                to_reverse.append(user)

                from_user = LinkUser.objects.get(id=from_user_id)
                from_user.prepend_to_notes(f"Extracted previously merged user { user.id }.")
                from_user.save(update_fields=['notes'])
                from_users.add(from_user)

        LinkUser.objects.bulk_update(to_reverse, ['email', 'is_active', 'link_count', 'registrar_id', 'notes'])
        for user in from_users:
            user.refresh_from_db()
            if match := re.search(r"\n*Added registrar during the merging of accounts", user.notes):
                user.registrar_id = None
                user.remove_line_from_notes('Added registrar')

            if match := re.search(r"\n*Added organizations.*: (?P<org_ids>.+)", user.notes):
                orgs = [int(oid) for oid in match.group('org_ids').split(', ')]
                user.organizations.remove(*orgs)
                user.remove_line_from_notes('Added organizations')

            user.link_count = user.created_links.count()
            user.save(update_fields=['link_count', 'registrar_id', 'notes'])

        # Report everything that was changed
        if log_to_file:
            with open(log_to_file, 'a', newline='') as file:
                file.write(f"## Unmerged accounts {user_ids} from user {from_user_id}\n")
                file.write(f"{''.join([str(u) for u in to_reverse])} from {from_users.pop()}\n\n")
                if reversed_org_links:
                    file.write("\tREVERSED ORG LINKS\n\n")
                    for original_creator_id, link_list in reversed_org_links.items():
                        file.write(f"\tRestored to user: {original_creator_id}\n")
                        file.write(f"\t{link_list}")
                        file.write("\n\n")
                if reversed_personal_links:
                    file.write("\tREVERSED PERSONAL LINKS\n\n")
                    for original_creator_id, link_list in reversed_personal_links.items():
                        file.write(f"\tRestored to user: {original_creator_id}\n")
                        file.write(f"\t{str(link_list)}")
                        file.write("\n\n")
        else:
            print(f"Unmerged accounts {user_ids} from user {from_user_id}")
            print(f"{to_reverse} from {from_users}")
            if reversed_org_links:
                print(f"Reversed org links {reversed_org_links}")
            if reversed_personal_links:
                print(f"Reversed personal links {reversed_personal_links}")
            print("")


def merge_users_with_only_unconfirmed_accounts(user_list):
    """
    Sync all the registrars/orgs to the most recently created and delete the others.
    """
    user_list.sort(key=lambda u: u.id, reverse=True)
    to_keep, *to_delete = user_list
    merge_accounts(to_keep, to_delete)


def merge_users_with_only_one_confirmed_account(user_list):
    """
    Sync all the registrars/orgs to the confirmed one and delete the other ones.
    """
    user_list.sort(key=lambda u: u.is_confirmed, reverse=True)
    to_keep, *to_delete = user_list
    merge_accounts(to_keep, to_delete)


def merge_users_with_multiple_confirmed_accounts_but_no_links(user_list):
    """
    Select the account they have logged into most recently, or if they
    have never logged in, the most recently created confirmed account.
    Then sync all the registrars/orgs to it and delete the other ones.
    """
    to_keep = None
    to_delete = []

    if any(u.last_login for u in user_list):
        for user in user_list:
            if user.last_login:
                if to_keep:
                    if to_keep.last_login < user.last_login:
                        to_delete.append(to_keep)
                        to_keep = user
                    else:
                        to_delete.append(user)
                else:
                    to_keep = user
            else:
                to_delete.append(user)
    else:
        for user in user_list:
            if user.is_confirmed:
                if to_keep:
                    if to_keep.id < user.id:
                        to_delete.append(to_keep)
                        to_keep = user
                    else:
                        to_delete.append(user)
                else:
                    to_keep = user
            else:
                to_delete.append(user)

    merge_accounts(to_keep, to_delete)


def merge_users_with_only_one_account_with_links(user_list):
    """
    If the account with the links is the one they have logged into most recently, keep that one:
    sync registrars/orgs and then delete the other ones.

    If the account with the links is not the one they have logged into most recently,
    move the links to the most recently logged into account, sync the registrars/orgs,
    and then delete the others.
    """
    [account_with_links] = filter(lambda u: u.link_count, user_list)
    most_recently_logged_into_account = account_with_links
    for user in user_list:
        if user.last_login and user.last_login > most_recently_logged_into_account.last_login:
            most_recently_logged_into_account = user

    if account_with_links == most_recently_logged_into_account:
        to_keep = account_with_links
        to_delete = list(filter(lambda u: u is not to_keep, user_list))
        merge_accounts(to_keep, to_delete)
    else:
        to_keep = most_recently_logged_into_account
        to_delete = list(filter(lambda u: u is not to_keep, user_list))
        merge_accounts(to_keep, to_delete, transfer_links=True)


def merge_users_with_multiple_accounts_with_links(user_list):
    """
    Move all links into the account with the most recent login,
    sync registrars/orgs and then delete the other ones.
    """
    to_keep = None
    to_delete = []
    for user in user_list:
        if user.last_login:
            if to_keep:
                if to_keep.last_login < user.last_login:
                    to_delete.append(to_keep)
                    to_keep = user
                else:
                    to_delete.append(user)
            else:
                to_keep = user
        else:
            to_delete.append(user)
    merge_accounts(to_keep, to_delete, transfer_links=True)


DUPLICATIVE_USER_SQL = '''
  SELECT
    perma_linkuser.id,
    perma_linkuser.email,
    perma_linkuser.is_active,
    perma_linkuser.is_confirmed,
    perma_linkuser.link_count,
    perma_linkuser.registrar_id,
    STRING_AGG (DISTINCT perma_linkuser_organizations.organization_id::TEXT, ',') organization_ids,
    perma_linkuser.cached_subscription_status
  FROM
    perma_linkuser
    LEFT OUTER JOIN perma_linkuser_organizations ON (
      perma_linkuser.id = perma_linkuser_organizations.linkuser_id
    )
  WHERE
    LOWER(perma_linkuser.email) in (
      SELECT
        LOWER(perma_linkuser.email)
      FROM
        perma_linkuser
      GROUP BY
        LOWER(perma_linkuser.email)
      HAVING
        COUNT(*) > 1
    )
  GROUP BY
    perma_linkuser.id;
'''

def get_and_categorize_duplicative_users():
    duplicative_users = LinkUser.objects.raw(DUPLICATIVE_USER_SQL)
    grouped_duplicative_users = defaultdict(list)

    count = 0
    for user in duplicative_users:
        count += 1
        grouped_duplicative_users[user.email.lower()].append(user)
    logger.info(f"MERGING: Found {count} addresses, for {len(grouped_duplicative_users)} users.")

    any_paid_history = set()
    registrar_and_org_mix = set()
    none_confirmed = set()
    only_one_confirmed = set()

    multiple_confirmed = defaultdict(list)
    multiple_confirmed_none_with_links = set()
    multiple_confirmed_only_one_with_links = set()
    multiple_confirmed_several_with_links = set()

    #
    # Identify any groups of users that are not safe to merge
    #
    for normalized_email, user_group in grouped_duplicative_users.items():

        registrar = False
        orgs = False
        for user in user_group:
            purchase_history = user.get_purchase_history()
            has_purchase_history = bool(purchase_history['purchases']) if purchase_history else False
            if user.cached_subscription_status or has_purchase_history:
                any_paid_history.add(normalized_email)
            if user.registrar_id:
                registrar = True
            if user.organization_ids:
                orgs = True
        if registrar and orgs:
            registrar_and_org_mix.add(normalized_email)

    exclude_group = any_paid_history | registrar_and_org_mix
    logger.warning(f"MERGING: Found {len(any_paid_history)} users who have purchased subscriptions or bonus links.")
    logger.warning(f"MERGING: Found {len(registrar_and_org_mix)} users who have accounts associated with both registrars and orgs.")

    #
    # Organize remaining users by how many accounts associated with their email address have been confirmed.
    #
    for normalized_email, user_group in grouped_duplicative_users.items():
        if normalized_email not in exclude_group:
            confirmed = []
            for user in user_group:
                if user.is_confirmed:
                    confirmed.append(user.id)
            if not confirmed:
                none_confirmed.add(normalized_email)
            elif len(confirmed) == 1:
                only_one_confirmed.add(normalized_email)
            else:
                multiple_confirmed[normalized_email] = user_group

    logger.info(f"MERGING: Found {len(none_confirmed)} users with no confirmed accounts.")
    logger.info(f"MERGING: Found {len(only_one_confirmed)} users with only one confirmed account.")

    #
    # Organize with multiple confirmed accounts by how many of them have links.
    #
    for normalized_email, user_group in multiple_confirmed.items():
        has_links = []
        for user in user_group:
            if user.link_count > 0:
                has_links.append(user.id)
        if not has_links:
            multiple_confirmed_none_with_links.add(normalized_email)
        elif len(has_links) == 1:
            multiple_confirmed_only_one_with_links.add(normalized_email)
        else:
            multiple_confirmed_several_with_links.add(normalized_email)

    logger.info(f"MERGING: Found {len(multiple_confirmed_none_with_links)} users with multiple confirmed accounts, but no links.")
    logger.info(f"MERGING: Found {len(multiple_confirmed_only_one_with_links)} users that have multiple confirmed accounts but only one account with links.")
    logger.info(f"MERGING: Found {len(multiple_confirmed_several_with_links)} users that have multiple accounts with links.")

    return {
        'none_confirmed': none_confirmed,
        'only_one_confirmed': only_one_confirmed,
        'multiple_confirmed_none_with_links': multiple_confirmed_none_with_links,
        'multiple_confirmed_only_one_with_links': multiple_confirmed_only_one_with_links,
        'multiple_confirmed_several_with_links': multiple_confirmed_several_with_links
    }

@task
def merge_duplicative_accounts(ctx):
    soup = time.time()

    emails_by_category = get_and_categorize_duplicative_users()

    initialize_csvs()

    def merge_category(category, merge_func):
        start = time.time()
        for normalized_email in emails_by_category[category]:
            users = LinkUser.objects.filter(email__iexact=normalized_email)
            merge_func(list(users))
        end = time.time()
        logger.info(f"MERGING: Merged {category} in {end - start} seconds.")

    merge_category('none_confirmed', merge_users_with_only_unconfirmed_accounts)
    merge_category('only_one_confirmed', merge_users_with_only_one_confirmed_account)
    merge_category('multiple_confirmed_none_with_links', merge_users_with_multiple_confirmed_accounts_but_no_links)
    merge_category('multiple_confirmed_only_one_with_links', merge_users_with_only_one_account_with_links)
    merge_category('multiple_confirmed_several_with_links', merge_users_with_multiple_accounts_with_links)

    nuts = time.time()
    logger.info(f"MERGING: Merged all duplicative accounts in {nuts - soup} seconds.")

@task
def unmerge_duplicative_accounts(ctx, log_to_file=None):
    with open(RETAINED_USERS_CSV, 'r', newline='') as csvfile:
        csv_reader = csv.DictReader(csvfile, delimiter='|')
        for row in csv_reader:
            unmerge_accounts(row['user id'], log_to_file)

@task
def assert_no_duplicative_accounts(ctx):
    duplicative_users = LinkUser.objects.raw(DUPLICATIVE_USER_SQL)
    assert not len(duplicative_users)
