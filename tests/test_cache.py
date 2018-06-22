import unittest

from mementoembed.cache import get_http_response_from_cache_model, \
    get_multiple_http_responses_from_cache_model, \
    MementoSurrogateCacheConnectionFailure

class TestCache(unittest.TestCase):

    def test_response_cache(self):
        """
            Seeing as I do not want to require developers to have
            a running redis or an network connection to run tests,
            I'll just use mock objects here and model behavior.
        """

        urilist = [
            "http://example.com/a",
            "http://example.com/b",
            "http://example.com/c",
            "http://example.com/d",
            "http://example.com/e",
            "http://example.com/f",
        ]

        class mock_session:

            def get(self, uri):

                if uri[-1] == 'b':
                    raise MementoSurrogateCacheConnectionFailure("testing")

                return "{}~~~done".format(uri)

        class mock_cache_model:

            def __init__(self):
                self.mydict = {}

            def get(self, key):

                if key[-1] == 'c':

                    return "{}~~~cached".format(key)

                else:

                    if key in self.mydict:
                        return self.mydict[key] + "~~~cached"
                    else:
                        return None


            def set(self, key, value):

                if key not in self.mydict:
                    self.mydict[key] = {}

                self.mydict[key] = value

        fs = mock_session()
        fr = mock_cache_model()

        self.assertEqual(
            get_http_response_from_cache_model(fr, "http://example.com/c", fs),
            "http://example.com/c~~~cached"
        )

        self.assertEqual(
            get_http_response_from_cache_model(fr, "http://example.com/a", fs),
            "http://example.com/a~~~done"
        )

        self.assertEqual(
            get_http_response_from_cache_model(fr, "http://example.com/a", fs),
            "http://example.com/a~~~done~~~cached"
        )

        responses, errors = get_multiple_http_responses_from_cache_model(fr, urilist, fs)

        self.assertEqual(
            repr(errors["http://example.com/b"]),
            "MementoSurrogateCacheConnectionFailure('testing',)"
            )

        self.assertEqual(
            responses["http://example.com/e"],
            "http://example.com/e~~~done"
            )

        self.assertEqual(
            responses["http://example.com/d"],
            "http://example.com/d~~~done"
            )
