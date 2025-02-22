from glob import glob

import os
import dateutil.parser
from django.conf import settings
from django.core.files.storage import default_storage
from django.urls import reverse
from django.http import StreamingHttpResponse
from django.test.utils import override_settings
from io import StringIO
import json
import urllib.parse
import re
from mock import patch

from .utils import ApiResourceTestCase, ApiResourceTransactionTestCase, TEST_ASSETS_DIR, index_warc_file
from perma.models import Link, LinkUser, Folder


class LinkResourceTestMixin():

    resource_url = '/archives'
    fixtures = ['fixtures/users.json',
                'fixtures/folders.json',
                'fixtures/archive.json',
                'fixtures/api_keys.json']

    rejected_status_code = 400  # Bad Request

    def setUp(self):
        super(LinkResourceTestMixin, self).setUp()

        self.org_user = LinkUser.objects.get(pk=3)
        self.regular_user = LinkUser.objects.get(pk=4)
        self.firm_user = LinkUser.objects.get(email="case_one_lawyer@firm.com")
        self.firm_folder = Folder.objects.get(name="Some Case")

        self.sponsored_user = LinkUser.objects.get(email="test_sponsored_user@example.com")

        self.unrelated_link = Link.objects.get(pk="7CF8-SS4G")
        self.unrelated_private_link = Link.objects.get(pk="ABCD-0001")
        self.capture_view_link = Link.objects.get(pk="ABCD-0007")
        self.link = Link.objects.get(pk="3SLN-JHX9")
        self.sponsored_user_link = Link.objects.get(pk="ABCD-0011")

        self.sponsored_user_link_detail_url = "{0}/{1}".format(self.list_url, self.sponsored_user_link.pk)
        self.unrelated_link_detail_url = "{0}/{1}".format(self.list_url, self.unrelated_link.pk)
        self.capture_view_link_url = "{0}/{1}".format(self.list_url, self.capture_view_link.pk)
        self.link_detail_url = "{0}/{1}".format(self.list_url, self.link.pk)

        self.logged_in_list_url = self.list_url
        self.logged_in_unrelated_link_detail_url = reverse('api:archives', args=[self.unrelated_link.pk])
        self.logged_in_private_link_download_url = reverse('api:archives_download', args=[self.unrelated_private_link.pk])

        self.public_list_url = reverse('api:public_archives')
        self.public_link_detail_url = reverse('api:public_archives', args=[self.link.pk])
        self.public_link_download_url = reverse('api:public_archives_download', args=[self.link.pk])
        self.public_link_download_url_for_private_link = reverse('api:public_archives_download', args=[self.unrelated_private_link.pk])

        self.replaced_link_public_download_url = reverse('api:public_archives_download', args=['ABCD-0006'])
        self.replaced_link_public_download_redirect_target = reverse('api:public_archives_download', args=['3SLN-JHX9'])
        self.replaced_link_authed_download_url = reverse('api:archives_download', args=['ABCD-0006'])
        self.replaced_link_authed_download_redirect_target = reverse('api:archives_download', args=['3SLN-JHX9'])
        self.replaced_link_owner = LinkUser.objects.get(id=4)

        self.logged_out_fields = [
            'title',
            'description',
            'url',
            'guid',
            'creation_timestamp',
            'captures',
            'warc_size',
            'warc_download_url',
            'queue_time',
            'capture_time',
        ]
        self.logged_in_fields = self.logged_out_fields + [
            'organization',
            'notes',
            'created_by',
            'archive_timestamp',
            'is_private',
            'private_reason',
        ]

    def assertRecordsInWarc(self, link, upload=False, expected_records=None, check_screenshot=False):

        def find_recording_in_warc(index, capture_url, content_type):
            return next(
                (entry for entry in index if
                    entry['content-type'] == 'application/http;msgtype=response' and
                    entry['http:content-type'].split(';',1)[0] == content_type.split(';',1)[0] and
                    re.fullmatch(r'^{}/?$'.format(capture_url), entry['warc-target-uri'])),
                None
            )

        def find_file_in_warc(index, capture_url, content_type):
            return next(
                (entry for entry in index if
                    entry['content-type'] ==  content_type and
                    re.fullmatch(r'^{}/?$'.format(capture_url), entry['warc-target-uri'])),
                None
            )

        # verify the primary capture's basics are sound
        self.assertEqual(link.primary_capture.status, 'success')
        self.assertTrue(link.primary_capture.content_type, "Capture is missing a content type.")

        # create an index of the warc
        with default_storage.open(link.warc_storage_file(), 'rb') as warc_file:
            index = index_warc_file(warc_file)

        # see if the index reports the content is in the warc
        find_record = find_file_in_warc if upload else find_recording_in_warc
        self.assertTrue(find_record(index, link.primary_capture.url, link.primary_capture.content_type))
        for record in expected_records or []:
            self.assertTrue(find_record(index, "{}/{}".format(self.server_url, record[0]), record[1]), "No matching record found for {}.".format(record[0]))

        # repeat for the screenshot
        if check_screenshot:
            self.assertEqual(link.screenshot_capture.status, 'success')
            self.assertTrue(link.screenshot_capture.content_type, "Capture is missing a content type.")
            self.assertTrue(find_file_in_warc(index, link.screenshot_capture.url, link.screenshot_capture.content_type))


class LinkResourceTestCase(LinkResourceTestMixin, ApiResourceTestCase):

    #######
    # GET #
    #######

    def test_get_list_json(self):
        self.successful_get(self.public_list_url, count=13)

    def test_get_detail_json(self):
        self.successful_get(self.public_link_detail_url, fields=self.logged_out_fields)

    @patch('api.views.stream_warc', autospec=True)
    def test_public_download(self, stream):
        stream.return_value = StreamingHttpResponse(StringIO("warc placeholder"))
        resp = self.api_client.get(self.public_link_download_url)
        self.assertHttpOK(resp)
        self.assertEqual(stream.call_count, 1)

    def test_private_download_at_public_url(self):
        self.rejected_get(self.public_link_download_url_for_private_link, expected_status_code=404)

    def test_private_download_unauthenticated(self):
        self.rejected_get(self.logged_in_private_link_download_url, expected_status_code=401)

    def test_private_download_unauthorized(self):
        self.rejected_get(
            self.logged_in_private_link_download_url,
            expected_status_code=403,
            user=self.firm_user
        )

    def test_replaced_link_public_download(self):
        resp = self.api_client.get(self.replaced_link_public_download_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self.replaced_link_public_download_redirect_target)

    def test_replaced_link_authed_download(self):
        self.api_client.force_authenticate(user=self.replaced_link_owner)
        resp = self.api_client.get(self.replaced_link_authed_download_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, self.replaced_link_authed_download_redirect_target)

    @patch('perma.utils.stream_warc', autospec=True)
    def test_private_download(self, stream):
        stream.return_value = StreamingHttpResponse(StringIO("warc placeholder"))
        self.api_client.force_authenticate(user=self.regular_user)
        resp = self.api_client.get(
            self.logged_in_private_link_download_url,
        )
        self.assertHttpOK(resp)
        self.assertEqual(stream.call_count, 1)

    ############
    # Updating #
    ############

    def test_patch_detail(self):
        self.successful_patch(self.unrelated_link_detail_url,
                              user=self.unrelated_link.created_by,
                              data={'notes': 'These are new notes',
                                    'title': 'This is a new title',
                                    'description': 'This is a new description'})

    def test_patch_default_view(self):
        self.successful_patch(self.capture_view_link_url,
                              user=self.capture_view_link.created_by,
                              data={'default_to_screenshot_view': True})

    def test_should_reject_updates_to_disallowed_fields(self):
        response = self.rejected_patch(self.unrelated_link_detail_url,
                                     user=self.unrelated_link.created_by,
                                     data={'url':'foo'})
        self.assertIn(b"Only updates on these fields are allowed", response.content)

    def test_moving_to_sponsored_links_not_allowed_patch(self):
        response = self.rejected_patch(self.sponsored_user_link_detail_url,
                                     user=self.sponsored_user,
                                     data={'folder':self.sponsored_user.sponsored_root_folder.pk})
        self.assertIn(b"You can't move links to your Sponsored Links folder.", response.content)


    ##################
    # Private/public #
    ##################

    def test_dark_archive(self):
        self.successful_patch(self.unrelated_link_detail_url,
                              user=self.unrelated_link.created_by,
                              data={'is_private': True, 'private_reason':'user'})

    ##########
    # Moving #
    ##########

    def test_moving(self):
        folder = self.org_user.organizations.first().folders.first()
        folder_url = "{0}/folders/{1}".format(self.url_base, folder.pk)

        self.successful_put("{0}/archives/{1}".format(folder_url, self.unrelated_link.pk),
                            user=self.org_user)

        # Make sure it's listed in the folder
        obj = self.successful_get(self.unrelated_link_detail_url, user=self.org_user)
        data = self.successful_get(folder_url+"/archives", user=self.org_user)
        self.assertIn(obj, data['objects'])

    def test_moving_to_sponsored_links_not_allowed_put(self):
        folder = self.sponsored_user.sponsored_root_folder
        folder_url = "{0}/folders/{1}".format(self.url_base, folder.pk)

        response = self.rejected_put("{0}/archives/{1}".format(folder_url, self.sponsored_user_link.pk),
                            user=self.sponsored_user)
        self.assertIn(b"You can't move links to your Sponsored Links folder.", response.content)


    ############
    # Ordering #
    ############

    def test_should_be_ordered_by_creation_timestamp_desc_by_default(self):
        data = self.successful_get(self.logged_in_list_url, user=self.regular_user)
        objs = data['objects']
        for i, obj in enumerate(objs):
            if i > 0:
                self.assertGreaterEqual(dateutil.parser.parse(objs[i - 1]['creation_timestamp']),
                                   dateutil.parser.parse(obj['creation_timestamp']))

    #############
    # Filtering #
    #############

    def test_should_allow_filtering_guid_by_query_string(self):
        data = self.successful_get(self.logged_in_list_url, data={'q': '3SLN'}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]['guid'], '3SLN-JHX9')

    def test_should_allow_filtering_url_by_query_string(self):
        data = self.successful_get(self.logged_in_list_url, data={'q': 'metafilter.com'}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[0]['url'], 'http://metafilter.com')

    def test_should_allow_filtering_title_by_query_string(self):
        data = self.successful_get(self.logged_in_list_url, data={'q': 'Community Weblog'}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[0]['title'], 'MetaFilter | Community Weblog')

    def test_should_allow_filtering_notes_by_query_string(self):
        data = self.successful_get(self.logged_in_list_url, data={'q': 'all cool things'}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[1]['notes'], 'Maybe the source of all cool things on the internet.')

    def test_should_allow_filtering_url(self):
        data = self.successful_get(self.logged_in_list_url, data={'url': 'metafilter.com'}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 2)
        self.assertEqual(objs[0]['title'], 'MetaFilter | Community Weblog')

    def test_should_allow_filtering_by_date_and_query(self):
        data = self.successful_get(self.logged_in_list_url, data={'url': 'metafilter.com','date':"2016-12-07T18:55:37Z"}, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]['title'], 'MetaFilter | Community Weblog')
        self.assertEqual(objs[0]['notes'], 'Maybe the source of all cool things on the internet. Second instance.')

    def test_should_allow_filtering_by_date_range_and_query(self):
        data = self.successful_get(self.logged_in_list_url, data={
            'url': 'metafilter.com',
            'min_date':"2016-12-06T18:55:37Z",
            'max_date':"2016-12-08T18:55:37Z",
        }, user=self.regular_user)
        objs = data['objects']

        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]['title'], 'MetaFilter | Community Weblog')
        self.assertEqual(objs[0]['notes'], 'Maybe the source of all cool things on the internet. Second instance.')


# Use a TransactionTestCase here because archive capture is threaded
class LinkResourceTransactionTestCase(LinkResourceTestMixin, ApiResourceTransactionTestCase):

    serve_files = glob(os.path.join(settings.PROJECT_ROOT, TEST_ASSETS_DIR, 'target_capture_files/*')) + [
        ['target_capture_files/test.html', 'test page.html'],
        ['target_capture_files/test.html', 'subdir/test.html'],

        ['target_capture_files/test.wav', 'test2.wav'],
        ['target_capture_files/test.mp4', 'test2.mp4'],
        ['target_capture_files/test.swf', 'test2.swf'],
        ['target_capture_files/test.swf', 'test3.swf'],
    ]

    def setUp(self):
        super(LinkResourceTransactionTestCase, self).setUp()
        self.post_data = {
            'url': self.server_url + "/test.html",
            'title': 'This is a test page',
            'description': 'This is a test description'
        }

    ########################
    # URL Archive Creation #
    ########################

    # This doesn't really belong here. Try to readdress.
    @patch('perma.models.Registrar.link_creation_allowed', autospec=True)
    def test_should_permit_create_if_folder_registrar_good_standing(self, allowed):
        allowed.return_value = True
        self.successful_post(
            self.list_url,
            expected_status_code=201,
            user=self.firm_user,
            data=dict(self.post_data,
                      folder=self.firm_folder.pk)
        )
        allowed.assert_called_once_with(self.firm_folder.organization.registrar)

    # This doesn't really belong here. Try to readdress.
    @patch('perma.models.LinkUser.get_links_remaining', autospec=True)
    def test_should_permit_create_if_folder_sponsorship_active(self, get_links_remaining):
        get_links_remaining.return_value = 0
        sponsored_folder = self.sponsored_user.sponsorships.first().folders.first()
        self.successful_post(
            self.list_url,
            expected_status_code=201,
            user=self.sponsored_user,
            data=dict(self.post_data,
                      folder=sponsored_folder.pk)
        )
        get_links_remaining.assert_not_called()


    def test_should_create_archive_from_html_url(self):
        target_folder = self.org_user.root_folder
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertRecordsInWarc(link, check_screenshot=True)

        # test favicon captured via meta tag
        self.assertIn("favicon_meta.ico", link.favicon_capture.url)

        self.assertFalse(link.is_private)
        self.assertEqual(link.submitted_title, "Test title.")

        self.assertEqual(link.submitted_description, "Test description.")

        # check folder
        self.assertTrue(link.folders.filter(pk=target_folder.pk).exists())


    @patch('perma.models.Registrar.link_creation_allowed', autospec=True)
    def test_should_create_archive_from_pdf_url(self, allowed):
        target_org = self.org_user.organizations.first()
        allowed.return_value = True
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.pdf",
                                       'folder': target_org.shared_folder.pk,
                                   },
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertRecordsInWarc(link)

        # check folder
        self.assertTrue(link.folders.filter(pk=target_org.shared_folder.pk).exists())
        self.assertEqual(link.organization, target_org)
        allowed.assert_called_once_with(target_org.shared_folder.organization.registrar)


    def test_should_add_http_to_url(self):
        self.successful_post(self.list_url,
                             data={'url': self.server_url.split("//")[1] + "/test.html"},
                             user=self.org_user)


    def test_should_not_use_bonus_link_if_regular_limit_is_available(self):
        # give our user a bonus link
        user = self.org_user
        user.bonus_links = 1
        user.save(update_fields=['bonus_links'])
        user.refresh_from_db()

        # establish baselines
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 9)
        self.assertEqual(bonus_links, 1)

        # make a link
        target_folder = self.org_user.root_folder
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=user)
        link = Link.objects.get(guid=obj['guid'])
        user.refresh_from_db()

        # assertions
        self.assertFalse(link.bonus_link)
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 8)
        self.assertEqual(bonus_links, 1)


    @patch('perma.models.LinkUser.links_remaining_in_period', autospec=True)
    def test_should_use_bonus_link_if_available_and_regular_limit_met(self, remaining):
        # give our user a bonus link, but nothing else remaining
        user = self.org_user
        user.bonus_links = 1
        user.save(update_fields=['bonus_links'])
        user.refresh_from_db()
        remaining.return_value = 0

        # establish baselines
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 1)

        # make a link
        target_folder = self.org_user.root_folder
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=user)
        link = Link.objects.get(guid=obj['guid'])
        user.refresh_from_db()

        # assertions
        self.assertTrue(link.bonus_link)
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 0)


    @patch('perma.models.LinkUser.links_remaining_in_period', autospec=True)
    def test_should_not_use_bonus_link_in_sponsored_folder(self, remaining):
        # give our user a bonus link, but nothing else remaining
        user = self.sponsored_user
        user.bonus_links = 1
        user.save(update_fields=['bonus_links'])
        user.refresh_from_db()
        remaining.return_value = 0

        # establish baselines
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 1)

        # make a link
        target_folder = user.folders.get(name="Test Library")
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=user)
        link = Link.objects.get(guid=obj['guid'])
        user.refresh_from_db()

        # assert nothing has changed
        self.assertFalse(link.bonus_link)
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 1)


    @patch('perma.models.LinkUser.links_remaining_in_period', autospec=True)
    def test_should_not_use_bonus_link_in_org_folder(self, remaining):
        # give our user a bonus link, but nothing else remaining
        user = self.org_user
        user.bonus_links = 1
        user.save(update_fields=['bonus_links'])
        user.refresh_from_db()
        remaining.return_value = 0

        # establish baselines
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 1)

        # make a link
        target_folder = Folder.objects.get(name="Test Journal")
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=user)
        link = Link.objects.get(guid=obj['guid'])
        user.refresh_from_db()

        # assert nothing has changed
        self.assertFalse(link.bonus_link)
        links_remaining, _ , bonus_links = user.get_links_remaining()
        self.assertEqual(links_remaining, 0)
        self.assertEqual(bonus_links, 1)


    def test_should_dark_archive_when_perma_noarchive_in_html(self):
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/perma-noarchive.html"},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

        # test favicon captured via favicon.ico well-known URL
        self.assertIn("favicon.ico", link.favicon_capture.url)

    @override_settings(PRIVATE_LINKS_IF_GENERIC_NOARCHIVE=False)
    def test_should_not_dark_archive_if_generic_noarchive_in_html_with_setting(self):
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/noarchive.html"},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertFalse(link.is_private)

        # test favicon captured via favicon.ico well-known URL
        self.assertIn("favicon.ico", link.favicon_capture.url)

    @override_settings(PRIVATE_LINKS_IF_GENERIC_NOARCHIVE=True)
    def test_should_dark_archive_if_generic_noarchive_in_html_with_setting(self):
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/noarchive.html"},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

        # test favicon captured via favicon.ico well-known URL
        self.assertIn("favicon.ico", link.favicon_capture.url)

    def test_should_dark_archive_when_perma_disallowed_in_robots_txt(self):
        with self.serve_file('extra_capture_files/robots.txt'):
            obj = self.successful_post(self.list_url,
                                       data={'url': self.server_url + "/subdir/test.html"},
                                       user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

    @override_settings(PRIVATE_LINKS_IF_GENERIC_NOARCHIVE=False)
    def test_should_not_dark_archive_when_generic_disallowed_in_xrobots_with_setting(self):
        headers = urllib.parse.quote(json.dumps([("x-robots-tag", "noarchive")]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertFalse(link.is_private)

    @override_settings(PRIVATE_LINKS_IF_GENERIC_NOARCHIVE=True)
    def test_should_dark_archive_when_generic_disallowed_in_xrobots_with_setting(self):
        headers = urllib.parse.quote(json.dumps([("x-robots-tag", "noarchive")]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

    def test_should_dark_archive_when_perma_disallowed_in_xrobots(self):
        headers = urllib.parse.quote(json.dumps([("x-robots-tag", "perma: noarchive")]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

    def test_should_dark_archive_when_perma_disallowed_in_xrobots_multi(self):
        headers = urllib.parse.quote(json.dumps([
            ("x-robots-tag", "noindex"),
            ("x-robots-tag", "perma: noarchive"),
            ("x-robots-tag", "noindex"),
        ]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

    def test_should_dark_archive_when_perma_disallowed_in_xrobots_malformed(self):
        headers = urllib.parse.quote(json.dumps([
            ("x-robots-tag", "noindex"),
            ("x-robots-tag", "google: perma: noarchive"),
            ("x-robots-tag", "noindex"),
        ]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertTrue(link.is_private)
        self.assertEqual(link.private_reason, "policy")

    def test_should_not_dark_archive_when_allowed_in_xrobots(self):
        headers = urllib.parse.quote(json.dumps([
            ("x-robots-tag", "noindex"),
            ("x-robots-tag", "perma: noindex"),
            ("x-robots-tag", "noindex"),
        ]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertFalse(link.is_private)

    def test_should_not_dark_archive_when_allowed_in_xrobots_complex(self):
        headers = urllib.parse.quote(json.dumps([
            ("x-robots-tag", "noindex"),
            ("x-robots-tag", "perma: noindex"),
            ("x-robots-tag", "google: noarchive"),
        ]))
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test.html?response_headers=" + headers},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertFalse(link.is_private)


    def test_media_capture_in_iframes(self):
        target_folder = self.org_user.root_folder
        obj = self.successful_post(self.list_url,
                                   data={
                                       'url': self.server_url + "/test_media_outer.html",
                                       'folder': target_folder.pk,
                                   },
                                   user=self.org_user)

        # verify that all images in src and srcset were found and captured
        expected_records = (
            # test_media_a.html
            ("test.wav", "audio/x-wav"), ("test2.wav", "audio/x-wav"),
            # test_media_b.html
            ("test.mp4", "video/mp4"), ("test2.mp4", "video/mp4"),
            # test_media_c.html
            ("test.swf", "application/vnd.adobe.flash.movie"), ("test2.swf", "application/vnd.adobe.flash.movie"), ("test3.swf", "application/vnd.adobe.flash.movie"),
            ("test1.jpg", "image/jpeg"), ("test2.png", "image/png"), ("test_fallback.jpg", "image/jpeg"),
            ("wide1.png", "image/png"), ("wide2.png", "image/png"), ("narrow.png", "image/png")
        )
        link = Link.objects.get(guid=obj['guid'])
        self.assertRecordsInWarc(link, expected_records=expected_records)


    #########################
    # File Archive Creation #
    #########################

    def test_should_create_archive_from_pdf_file(self):
        with open(os.path.join(TEST_ASSETS_DIR, 'target_capture_files', 'test.pdf'), 'rb') as test_file:
            obj = self.successful_post(self.list_url,
                                       format='multipart',
                                       data=dict(self.post_data.copy(), file=test_file),
                                       user=self.org_user)

            link = Link.objects.get(guid=obj['guid'])
            self.assertRecordsInWarc(link, upload=True)
            self.assertEqual(link.primary_capture.user_upload, True)

    def test_should_create_archive_from_jpg_file(self):
        with open(os.path.join(TEST_ASSETS_DIR, 'target_capture_files', 'test.jpg'), 'rb') as test_file:
            obj = self.successful_post(self.list_url,
                                       format='multipart',
                                       data=dict(self.post_data.copy(), file=test_file),
                                       user=self.org_user)

            link = Link.objects.get(guid=obj['guid'])
            self.assertRecordsInWarc(link, upload=True)
            self.assertEqual(link.primary_capture.user_upload, True)

    def test_should_reject_invalid_file(self):
        with open(os.path.join(TEST_ASSETS_DIR, 'target_capture_files', 'test.html'), 'rb') as test_file:
            obj = self.rejected_post(self.list_url,
                                     format='multipart',
                                     data=dict(self.post_data.copy(), file=test_file),
                                     user=self.org_user)
            self.assertIn(b'Invalid file', obj.content)

    @patch('api.serializers.requests', autospec=True)
    def test_should_reject_unsafe_file(self, requests):
        requests.post.return_value = type('MockResponse', (), {'ok': True, 'json': lambda x: {"safe": False, "reason": "some reason"}})()
        with open(os.path.join(TEST_ASSETS_DIR, 'target_capture_files', 'test.jpg'), 'rb') as test_file:
            obj = self.rejected_post(self.list_url,
                                     format='multipart',
                                     data=dict(self.post_data.copy(), file=test_file),
                                     user=self.org_user)
            self.assertIn(b'Validation failed', obj.content)
        self.assertEqual(requests.post.call_count, 1)


    ############
    # Deleting #
    ############

    def test_delete_detail(self):
        with self.serve_file('extra_capture_files/robots.txt'):
            obj = self.successful_post(self.list_url,
                                       data={'url': self.server_url + "/subdir/test.html"},
                                       user=self.org_user)

            new_link = Link.objects.get(guid=obj['guid'])
            new_link_url = "{0}/{1}".format(self.list_url, new_link.pk)
            self.successful_delete(new_link_url, user=self.org_user)

    def test_delete_bonus_link(self):
        # make a bonus link here, rather than messing with the fixtures
        bonus_link = Link(created_by=self.regular_user, bonus_link=True)
        bonus_link.save()
        bonus_link_url = "{0}/{1}".format(self.list_url, bonus_link.pk)

        # establish baseline
        links_remaining, _ , bonus_links = self.regular_user.get_links_remaining()
        self.assertEqual(links_remaining, 6)
        self.assertEqual(bonus_links, 0)

        # delete the bonus link
        self.successful_delete(bonus_link_url, user=self.regular_user)
        self.regular_user.refresh_from_db()

        # assertions
        links_remaining, links_remaining_period, bonus_links = self.regular_user.get_links_remaining()
        self.assertEqual(links_remaining, 6)
        self.assertEqual(bonus_links, 1)


    ####################
    # Regression Tests #
    ####################

    def test_should_accept_spaces_in_url(self):
        obj = self.successful_post(self.list_url,
                                   data={'url': self.server_url + "/test page.html?a b=c d#e f"},
                                   user=self.org_user)

        link = Link.objects.get(guid=obj['guid'])
        self.assertEqual(link.capture_job.status, 'completed')
        self.assertEqual(link.primary_capture.status, 'success')
