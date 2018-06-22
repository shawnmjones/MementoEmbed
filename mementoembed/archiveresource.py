import re
import logging
# import inspect

import requests
import tldextract
import aiu

from urllib.parse import urlparse

from .favicon import get_favicon_from_google_service, \
    get_favicon_from_html

archive_collection_patterns = [
    "http://wayback.archive-it.org/([0-9]*)/[0-9]{14}/.*",
]

archive_collection_uri_prefixes = {
    "Archive-It": "https://archive-it.org/collections/{}"
}

archive_names = {
    "archive-it.org": "Archive-It",
    "archive.org": "Internet Archive",
    "webcitation.org": "WebCite",
    "archive.is": "archive.today"
}

class ArchiveResource:

    def __init__(self, urim, httpcache, working_directory, logger=None):

        self.urim = urim
        self.logger = logger or logging.getLogger(__name__)

        self.httpcache = httpcache
        self.working_directory=working_directory

        self.memento_archive_name = None
        self.memento_archive_domain = None
        self.memento_archive_scheme = None
        self.memento_archive_uri = None
        self.archive_favicon_uri = None

        self.archive_collection_uri = None
        self.archive_collection_id = None
        self.archive_collection_name = None

        # TODO: some kind of archive information cache

    @property
    def scheme(self):
        """
            Derives the scheme of the memento's archive URI.
        """

        if self.memento_archive_scheme == None:

            o = urlparse(self.urim)
            self.memento_archive_scheme = o.scheme

        return self.memento_archive_scheme

    @property
    def domain(self):
        """
            Derives the domain of the memento's archive URI.
        """

        if self.memento_archive_domain == None:
            self.memento_archive_domain = tldextract.extract(self.uri).registered_domain

        return self.memento_archive_domain

    @property
    def name(self):

        if self.memento_archive_name == None:

            if self.domain in archive_names:

                self.memento_archive_name = archive_names[self.domain]

            else:

                self.memento_archive_name = self.domain.upper()
        
        return self.memento_archive_name

    @property
    def favicon(self):

        # self.logger.debug("call stack: {}".format( inspect.stack() ))

        self.logger.debug("archive favicon uri: {}".format(self.archive_favicon_uri))

        # 1 try the HTML within the archive's web page for a favicon
        if self.archive_favicon_uri is None:
            
            self.logger.debug("attempting to acquire the archive favicon URI from HTML")

            r = self.httpcache.get(self.uri)

            self.archive_favicon_uri = get_favicon_from_html(r.text)

            if not self.httpcache.is_uri_good(self.archive_favicon_uri):
                self.archive_favicon_uri = None

        self.logger.debug("archive favicon after step 1: {}".format(self.archive_favicon_uri))

        # 2. try to construct the favicon URI and look for it on the live web
        if self.archive_favicon_uri is None:

            self.logger.debug("attempting to use the conventional favicon URI to find the archive favicon URI")

            candidate_favicon_uri = "{}://{}/favicon.ico".format(self.scheme, self.domain)

            r = self.httpcache.get(candidate_favicon_uri)

            if r.status_code == 200:

                # this is some protection against soft-404s
                if 'image/' in r.headers['content-type']:
                    self.archive_favicon_uri = candidate_favicon_uri

                if not self.httpcache.is_uri_good(self.archive_favicon_uri):
                    self.archive_favicon_uri = None

        self.logger.debug("archive favicon after step 2: {}".format(self.archive_favicon_uri))

        # 3. if all else fails, fall back to the Google favicon service
        if self.archive_favicon_uri is None:

            self.logger.debug("attempting to query the google favicon service for the archive favicon URI")

            self.archive_favicon_uri = get_favicon_from_google_service(
                self.httpcache, self.uri)

            self.logger.debug("during step 3, the archive favicon is: {}".format(self.archive_favicon_uri))

            if self.archive_favicon_uri is not None:

                if not self.httpcache.is_uri_good(self.archive_favicon_uri):
                    self.archive_favicon_uri = None

        self.logger.debug("discovered archive favicon at {}".format(self.archive_favicon_uri))

        self.logger.debug("archive favicon after step 3: {}".format(self.archive_favicon_uri))

        return self.archive_favicon_uri

    @property
    def uri(self):

        if self.memento_archive_uri == None:
            o = urlparse(self.urim)
            domain = tldextract.extract(self.urim).registered_domain

            self.memento_archive_uri = "{}://{}".format(o.scheme, domain)

        return self.memento_archive_uri

    @property
    def collection_name(self):

        if self.archive_collection_name == None:

            if self.collection_id:

                aic = aiu.ArchiveItCollection(
                    collection_id=self.collection_id,
                    logger=self.logger,
                    working_directory=self.working_directory
                    )

                self.archive_collection_name = aic.get_collection_name()

        return self.archive_collection_name

    @property
    def collection_id(self):

        if self.archive_collection_id == None:

            for pattern in archive_collection_patterns:
                m = re.match(pattern, self.urim)

                if m:
                    self.archive_collection_id = m.group(1)
                    break

        return self.archive_collection_id

    @property
    def collection_uri(self):

        if self.archive_collection_uri == None:

            if self.collection_id:

                try:
                    self.archive_collection_uri = archive_collection_uri_prefixes[self.name].format(
                        self.collection_id)
                except KeyError:
                    self.archive_collection_uri = None

        return self.archive_collection_uri