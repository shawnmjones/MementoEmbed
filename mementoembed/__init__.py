import sys
import os
import json
import logging
import traceback

import htmlmin
import dicttoxml
import redis
import requests
import requests_cache

from time import strftime

from redis import RedisError
from flask import Flask, request, render_template, make_response
from flask.logging import default_handler
from requests.exceptions import Timeout, TooManyRedirects, \
    ChunkedEncodingError, ContentDecodingError, StreamConsumedError, \
    URLRequired, MissingSchema, InvalidSchema, InvalidURL, \
    UnrewindableBodyError, ConnectionError

from .cachesession import CacheSession
from .mementosurrogate import MementoSurrogate
from .mementoresource import NotAMementoError, MementoParsingError
from .textprocessing import TextProcessingError
from .version import __useragent__

rootlogger = logging.getLogger(__name__)

__all__ = [
    "MementoSurrogate"
    ]

class MementoEmbedException(Exception):
    pass

class URIMFilter(logging.Filter):

    def filter(self, record):
        record.urim = "No request"

        try:
            record.urim = request.args.get("url")
        except RuntimeError as e:
            # just use the defalt message if the flask request object isn't set
            pass
        
        return True

def setup_cache(config):

    if 'CACHEENGINE' in config:

        if config['CACHEENGINE'] == 'Redis':

            if 'CACHEHOST' in config:
                dbhost = config['CACHEHOST']
            else:
                dbhost = "localhost"

            if 'CACHEPORT' in config:
                dbport = config['CACHEPORT']
            else:
                dbport = "6379"

            if 'CACHEDB' in config:
                dbno = config['CACHEDB']
            else:
                dbno = "0"

            if 'CACHE_EXPIRETIME' in config:
                expiretime = config['CACHE_EXPIRETIME']
            else:
                expiretime = 7 * 24 * 60 * 60

            rootlogger.info("Setting up Redis as cache engine with host={}, "
                "port={}, database number={}, and expiretime={}".format(
                    dbhost, dbport, dbno, expiretime
                ))

            rconn = redis.StrictRedis(host=dbhost, port=dbport, db=dbno)

            requests_cache.install_cache('mementoembed', backend='redis', 
                expire_after=expiretime, connection=rconn)

        elif config['CACHEENGINE'] == 'SQLite':

            if 'CACHEFILE' in config:
                cachefile = config['CACHEFILE']
            else:
                cachefile = "mementoembed"

            rootlogger.info("Setting up SQLite as cache engine with "
                "file named {}".format(cachefile))

            requests_cache.install_cache(cachefile)

        else:

            rootlogger.info("With no other supported cache engines detected, "
                "setting up SQLite as cache engine with file named 'mementoembed'")

            requests_cache.install_cache('mementoembed_cache')

    else:
        requests_cache.install_cache('mementoembed')

def get_requests_timeout(config):

    if 'REQUEST_TIMEOUT' in config:
        return float(config['REQUEST_TIMEOUT'])
    else:
        return 20
        
def setup_logging_config(config, applogger):

    logfile = None

    if 'APPLICATION_LOGLEVEL' in config:
        loglevel = eval(config['APPLICATION_LOGLEVEL'])

    else:
        loglevel = logging.INFO

    if 'APPLICATION_LOGFILE' in config:
        logfile = config['APPLICATION_LOGFILE']

    formatter = logging.Formatter('[%(asctime)s] - %(name)s - %(levelname)s - [ %(urim)s ]: %(message)s')

    print("loglevel is {}".format(loglevel))

    # default_handler.addFilter(URIMFilter())

    # this formatter must be set here to work
    # default_handler.setFormatter(formatter)
    
    rootlogger.setLevel(loglevel)
    # rootlogger.addHandler(default_handler)
    
    ch = logging.StreamHandler()
    ch.addFilter(URIMFilter())
    rootlogger.addHandler(ch)

    if logfile is not None:

        fh = logging.FileHandler(logfile)
        fh.setLevel(loglevel)
        fh.setFormatter(formatter)
        rootlogger.addHandler(fh)

    if 'ACCESS_LOGFILE' in config:
        logfile = config['ACCESS_LOGFILE']
        handler = logging.FileHandler(logfile)
        applogger.addHandler(handler)

    rootlogger.info("logging with level {}".format(loglevel))
    rootlogger.info("logging to logfile {}".format(logfile))

def create_app():

    app = Flask(__name__, instance_relative_config=True)

    app.config.from_object('config.default')
    app.config.from_pyfile('application.cfg', silent=True)
    app.config.from_json("/etc/mementoembed.json", silent=True)

    setup_logging_config(app.config, app.logger)
    setup_cache(app.config)

    timeout = get_requests_timeout(app.config)

    rootlogger.info("loading Flask app for {}".format(app.name))
    rootlogger.info("requests timeout is set to {}".format(timeout))

    @app.after_request
    def after_request(response):

        ts = strftime('[%d/%b/%Y:%H:%M:%S %z]')
        
        # pylint: disable=no-member
        app.logger.info(
            '%s - - %s %s %s %s',
            request.remote_addr,
            ts,
            request.method,
            request.full_path,
            response.status
        )

        return response

    #pylint: disable=unused-variable
    @app.route('/', methods=['GET', 'HEAD'])
    def front_page():
        return render_template('index.html')

    @app.route('/services/oembed', methods=['GET', 'HEAD'])
    def oembed_endpoint():

        try:
            urim = request.args.get("url")
            responseformat = request.args.get("format")

            rootlogger.info("starting surrogate oembed creation process for URI-M {}".format(urim))

            # JSON is the default
            if responseformat == None:
                responseformat = "json"

            if responseformat != "json":
                if responseformat != "xml":
                    return "The provider cannot return a response in the requested format.", 501

            rootlogger.debug("output format will be: {}".format(responseformat))
            
            httpcache = CacheSession(
                timeout=timeout,
                user_agent=__useragent__,
                starting_uri=urim
                )
        
            s = MementoSurrogate(
                urim,
                httpcache
            )

            output = {}

            output["type"] = "rich"
            output["version"] = "1.0"

            output["url"] = urim
            output["provider_name"] = s.archive_name
            output["provider_uri"] = s.archive_uri

            urlroot = request.url_root
            urlroot = urlroot if urlroot[-1] != '/' else urlroot[0:-1]

            rootlogger.info("generating oEmbed output for {}".format(urim))
            output["html"] = htmlmin.minify( render_template(
                "social_card.html",
                urim = urim,
                urir = s.original_uri,
                image = s.striking_image,
                archive_uri = s.archive_uri,
                archive_favicon = s.archive_favicon,
                archive_collection_id = s.collection_id,
                archive_collection_uri = s.collection_uri,
                archive_collection_name = s.collection_name,
                archive_name = s.archive_name,
                original_favicon = s.original_favicon,
                original_domain = s.original_domain,
                original_link_status = s.original_link_status,
                surrogate_creation_time = s.creation_time,
                memento_datetime = s.memento_datetime,
                me_title = s.title,
                me_snippet = s.text_snippet,
                server_domain = urlroot
            ), remove_empty_space=True, 
            remove_optional_attribute_quotes=False )

            output["width"] = 500

            #TODO: fix this to the correct height!
            output["height"] = 200

            if responseformat == "json":
                response = make_response(json.dumps(output, indent=4))
                response.headers['Content-Type'] = 'application/json'
            else:
                response = make_response( dicttoxml.dicttoxml(output, custom_root='oembed') )
                response.headers['Content-Type'] = 'text/xml'

            rootlogger.info("returning {} oEmbed output for {}".format(responseformat, urim))

        except NotAMementoError as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning(
                "URI-M {} does not appear to be a memento, details: {}, http status: {}, headers: {}".format(
                    urim, e.original_exception, e.response.status_code, e.response.headers, ))

            e2 = e.original_exception
            return json.dumps({
                "content":
                    render_template(
                    'make_your_own_memento.html',
                    urim = urim
                    ),
                "error":
                    "Not a memento",
                "error details": repr(traceback.format_exc())
                }), 404

        except (Timeout, ConnectionError) as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("The server for URI-M {} could not be reached, details: {}".format(urim, e))

            return json.dumps({
                "content": "MementoEmbed could not reach the server to download {}".format(urim),
                "error": "MementoEmbed timed out trying to acquire {} from the server".format(urim),
                "error details": repr(traceback.format_exc())
            }, indent=4), 504

        except (TooManyRedirects, ChunkedEncodingError, ContentDecodingError, StreamConsumedError) as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("Problems were encountered acquiring URI-M {}: {}".format(urim, e))

            return json.dumps({
                "content": "MementoEmbed could not download {}".format(urim),
                "error": "MementoEmbed did not timeout, but had problems downloading {}".format(urim),
                "error details": repr(traceback.format_exc())
            }, indent=4), 502

        except (URLRequired, MissingSchema, InvalidSchema, InvalidURL) as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("An unsupported/invalid URI related to {} was submitted, details: {}".format(urim, e))

            return json.dumps({
                "content": "The URI-M {} is not valid".format(urim),
                "error": "MementoEmbed encountered problems processing {}".format(urim),
                "error details": repr(traceback.format_exc())
            }, indent=4), 400

        except UnrewindableBodyError as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("A network issue occurred with URI-M {}, details: {}".format(urim, e))

            return json.dumps({
                "content": "MementoEmbed had problems extracting content for URI-M {}".format(urim),
                "error": "MementoEmbed had problems extracting content for URI-M {}".format(urim),
                "error details": repr(traceback.format_exc())
            }, indent=4), 500

        except (TextProcessingError, MementoParsingError) as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("Memento parsing failed for URI-M {}, details: {}".format(urim, e))

            return json.dumps({
                "content": "MementoEmbed could not process the text at URI-M<br /> {} <br />Are you sure this is an HTML page?".format(urim),
                "error": "MementoEmbed could not parse the text at URI-M {}".format(urim),
                "error details": repr(traceback.format_exc())
            }, indent=4), 500

        except RedisError as e:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("Redis Error has occurred with URI-M {}, details: {}".format(urim, e))

            return json.dumps({
                "content": "MementoEmbed could not connect to its database cache, please contact the system owner.",
                "error": "A Redis Error has occurred with MementoEmbed.",
                "error details": repr(traceback.format_exc())
            }, indent=4), 500

        except Exception:

            requests_cache.get_cache().delete_url(urim)

            rootlogger.warning("An unexpected Exception has been raised for URI-M {}, details: {}".format(urim, traceback.format_exc()))

            return json.dumps({
                "content": "An unforeseen error has occurred with MementoEmbed, please contact the system owner.",
                "error": "A generic exception was caught by MementoEmbed. Please check the log.",
                "error details": repr(traceback.format_exc())
            }, indent=4), 500


        return response

    return app
