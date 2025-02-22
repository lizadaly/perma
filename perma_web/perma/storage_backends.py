# alternate storage backends
import io as StringIO
import os

from django.core.files.storage import FileSystemStorage as DjangoFileSystemStorage
from django.core.files import File
from django.conf import settings
import django.dispatch

from storages.backends.s3boto3 import S3Boto3Storage
from storages.backends.azure_storage import AzureStorage
from whitenoise.storage import CompressedStaticFilesStorage

# used only for suppressing INFO logging in S3Boto3Storage
import logging


# documentation:
# providing_args=["instance", "path", "overwrite"])
file_saved = django.dispatch.Signal()


### Static files config

class StaticStorage(CompressedStaticFilesStorage):
    pass


### Media files config

class BaseMediaStorage:
    """
        This mixin provides some helper functions for working with files
        in both local disk and remote storage.
    """
    def store_file(self, file_object, file_path, overwrite=False, send_signal=True):
        """
            Given an open file_object ready for reading,
            and the file_path to store it to,
            save the file and return the new file name.

            File name will only change if file_path conflicts with an existing file.
            If overwrite=True, existing file will instead be deleted and overwritten.
        """

        if overwrite:
            if self.exists(file_path):
                self.delete(file_path)
        new_file_path = self.save(file_path, File(file_object))
        if send_signal:
            file_saved.send(sender=self.__class__, instance=self, path=new_file_path, overwrite=overwrite)
        return new_file_path.split('/')[-1]

    def store_data_to_file(self, data, file_path, overwrite=False, send_signal=True):
        file_object = StringIO.StringIO()
        file_object.write(data)
        file_object.seek(0)
        return self.store_file(file_object, file_path, overwrite=overwrite, send_signal=send_signal)

    def walk(self, top='/', topdown=False, onerror=None):
        """
            An implementation of os.walk() which uses the Django storage for
            listing directories.

            via https://gist.github.com/btimby/2175107
        """
        try:
            dirs, nondirs = self.listdir(top)
        except os.error as err:
            if onerror is not None:
                onerror(err)
            return

        if topdown:
            yield top, dirs, nondirs
        for name in dirs:
            new_path = os.path.join(top, name)
            for x in self.walk(new_path):
                yield x
        if not topdown:
            yield top, dirs, nondirs


class FileSystemMediaStorage(BaseMediaStorage, DjangoFileSystemStorage):
    pass


class S3MediaStorage(BaseMediaStorage, S3Boto3Storage):
    location = settings.MEDIA_ROOT
    # suppress boto3's INFO logging per https://github.com/boto/boto3/issues/521
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('botocore').setLevel(logging.WARNING)

    def get_object_parameters(self, name):
        """
        Upload warcs as content-type 'application/gzip' rather than
        content-type 'application/octet-stream' and content-encoding 'gzip'
        so that archives are fetched correctly by the playback service worker.
        See https://github.com/jschneier/django-storages/issues/917
        """
        params = super().get_object_parameters(name)
        if name.endswith('.gz'):
            params['ContentType'] = 'application/gzip'
            params['ContentEncoding'] = ''
        return params


class AzureMediaStorage(BaseMediaStorage, AzureStorage):
    location = settings.MEDIA_ROOT
    # suppress azure storage http logging
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)

    def get_object_parameters(self, name):
        """
        As above, modify stored archive content-type and content-encoding
        for proper playback.
        See https://github.com/jschneier/django-storages/issues/917
        """
        params = super().get_object_parameters(name)
        if name.endswith('.gz'):
            params['content_type'] = 'application/gzip'
            params['content_encoding'] = ''
        return params
