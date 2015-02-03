from __future__ import unicode_literals
import requests
from flask import Flask, request, make_response
import multiprocessing
import gunicorn.app.base
from gunicorn.six import iteritems
from collections import defaultdict
from threading import Lock
import warnings
import logging
import sys
import sha
import time


INVALIDATE = 20
remote = "https://{}".format(sys.argv[1])

app = Flask(__name__)
app.debug = True


logging.basicConfig(filename='vimlog.log',
                    filemode='a',
                    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)
logging.info("vimlog")
logger = logging.getLogger('vimlog')
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.ERROR)
requests_log.propagate = True


class CacheHash(object):
    count = 0
    soap_action = None
    request = None
    response = None
    c_hit = 0
    c_time = 0.0

    def __init__(self):
        self.ratio = {'raw': 0, 'cache': 0}
        self.ips = set()
        self.lock = Lock()


class Response(object):
    def __init__(self, data, code, headers=None):
        self.data = data
        self.code = code
        self.headers = headers or {'content-type': 'text/xml; charset=utf-8',
                                   'connection': 'close',
                                   'cache-control': 'no-cache'}


class ConStats(object):
    def __init__(self):
        self.details = defaultdict(dict)
        self.detailsLock = Lock()
        self._cache = {}

    def update_cache(self, cache_obj):
        cache_obj.lock.acquire()
        req = self.vim_get(cache_obj.path, cache_obj.headers, cache_obj.request)
        cache_obj.response = req.text
        cache_obj.c_time = time.time()
        cache_obj.lock.release()

    def connection(self, ip, action, data):
        shash = sha.sha(data).hexdigest()
        if shash in self._cache:
            cache_obj = self._cache[shash]
            if (time.time() - cache_obj.c_time) < INVALIDATE:
                cache_obj.c_hit += 1
                cache_obj.lock.acquire()
                cache_obj.lock.release()
                response = Response(cache_obj.response, 200)
                logger.info('Cache {} valid, serving {} method for ip {}'.format(
                    shash, action, ip))
                return response
            else:
                cache_obj.lock.acquire()
                cache_obj.lock.release()
                if (time.time() - cache_obj.c_time) < INVALIDATE:
                    cache_obj.c_hit += 1
                    response = Response(cache_obj.response, 200)
                    logger.info('Cache {} updated, serving {} method for ip {}'.format(
                        shash, action, ip))
                    return response
                else:
                    logger.info('Cache {} invalid, need to recall {} method, sorry {}'.format(
                        shash, action, ip))
                    self.update_cache(cache_obj)
                    response = Response(cache_obj.response, 200)
                    logger.info('Cache {} updated, serving {} method for ip {}'.format(
                        shash, action, ip))
                    return response
        else:
            if action in ["RetrieveProperties", "RetrieveServiceContent"]:
                logger.info('Creating new item {} method for ip {}'.format(
                    action, ip))
                # Create the cache object
                self.detailsLock.acquire()
                cache_obj = CacheHash()
                self._cache[shash] = cache_obj
                self.detailsLock.release()
                cache_obj.lock.acquire()
                cache_obj.count += 1
                cache_obj.ips.add(ip)
                cache_obj.soap_action = action
                cache_obj.request = request.data
                cache_obj.path = request.path
                cache_obj.headers = request.headers
                cache_obj.c_time = time.time()
                req = self.vim_get(request.path, request.headers, request.data)
                cache_obj.response = req.text
                cache_obj.lock.release()
                response = Response(cache_obj.response, 200)
                return response
            else:
                logger.info('We do not cache {} method'.format(
                    action, ip))
                req = self.vim_get(request.path, request.headers, request.data)
                response = Response(req.text, req.status_code, req.headers)
                return response

    def vim_get(self, path, headers, data):
        logger.info('Going to vSphere...')
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            req = requests.post(remote + path,
                                headers=headers,
                                data=data, verify=False)
        logger.info('vSphere returned')
        return req

con = ConStats()


@app.route('/<path:path>', methods=['POST', 'GET'])
def vim_request(path):

    # Here we create a random connection id for use in the con tracking
    soap_action = request.headers.get('Soapaction', None)
    ip = request.headers.get('X-Forwarded-For', '127.0.0.1')

    conn = con.connection(ip, soap_action, request.data)

    resp = make_response(conn.data, conn.code)

    for header in conn.headers:
        if header == "transfer-encoding":
            continue
        resp.headers[header] = conn.headers[header]
    return resp


def number_of_workers():
    return (multiprocessing.cpu_count() * 2) + 1


class StandaloneApplication(gunicorn.app.base.BaseApplication):

    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super(StandaloneApplication, self).__init__()

    def load_config(self):
        config = dict([(key, value) for key, value in iteritems(self.options)
                       if key in self.cfg.settings and value is not None])
        for key, value in iteritems(config):
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


if __name__ == '__main__':

    #server_thread = threading.Thread(target=bas.con.ncurse)
    #server_thread.daemon = True
    #server_thread.start()

    options = {
        'bind': '%s:%s' % ('127.0.0.1', '8443'),
        'workers': 1,
        'threads': 4000,
        'timeout': 120,
    }
    StandaloneApplication(app, options).run()


# Flask app run
# if __name__ == '__main__':
#     app.run()
