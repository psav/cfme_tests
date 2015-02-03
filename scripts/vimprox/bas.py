from threading import Lock
from collections import defaultdict
import curses
import sha
import time
import logging
logger = logging.getLogger('vimlog')

INVALIDATE = 60


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


class ConStats(object):
    def __init__(self):
        self.details = defaultdict(dict)
        self.detailsLock = Lock()
        self._cache = defaultdict(CacheHash)

    def display_details(self):
        self.win.clear()
        line = 0
        for ip in self.details:
            self.win.addstr(line, 0, "ip: {}".format(ip))
            line += 1
            for cid in self.details[ip]:
                self.win.addstr(line, 0, " con: {}, {}".format(cid,
                    self.details[ip][cid].soap_action))
                line += 1
            if line >= 14:
                break
        self.win.refresh()
        line = 0
        self.shash_win.clear()
        raw = 0
        cache = 0
        for shash in sorted(self._cache.items(),
                            key=lambda dictitem: dictitem[1].count, reverse=True):
            self.shash_win.addstr(
                line, 0, "{}: {}, {} [{}] ={}, {}=".format(
                    shash[1].count,
                    shash[0][:8],
                    shash[1].ips,
                    shash[1].soap_action,
                    shash[1].ratio['raw'],
                    shash[1].ratio['cache']))
            raw += shash[1].ratio['raw']
            cache += shash[1].ratio['cache']
            line += 1
            if line >= 29:
                break
        self.stdscr.addstr(60, 2, "raw: {} | cache: {}".format(raw, cache))
        self.stdscr.refresh()
        self.shash_win.refresh()

    def inc_con(self, ip, cid, action, data):
        self.detailsLock.acquire()
        shash = sha.sha(data).hexdigest()
        self._cache[shash].count += 1
        self._cache[shash].ips.add(ip)
        self._cache[shash].soap_action = action
        self._cache[shash].request = data
        self.details[ip][cid] = self._cache[shash]
        #self.display_details()

        if hasattr(self._cache[shash], 'response'):
            if self._cache[shash].response:
                if (time.time() - self._cache[shash].c_time) < INVALIDATE:
                    self._cache[shash].c_hit += 1
                    cache_return = self._cache[shash].response
                else:
                    logger.info('Cache invalid, need to recall {} method sorry {}'.format(
                        action, ip))
                    cache_return = None
            else:
                cache_return = None
        else:
            cache_return = None
        self.detailsLock.release()
        return cache_return

    def dec_con(self, ip, cid, size, data=None):
        self.detailsLock.acquire()
        if data:
            logger.info('Creating new cache for {} from ip {}'.format(
                self.details[ip][cid].soap_action, ip))
            self.details[ip][cid].c_time = time.time()
            self.details[ip][cid].response = data
            self.details[ip][cid].ratio['raw'] += size
        else:
            self.details[ip][cid].ratio['cache'] += size
        del self.details[ip][cid]
        self.detailsLock.release()
        #self.display_details()

    def ncurse(self):
        self.stdscr = curses.initscr()
        self.win = curses.newwin(15, 100, 4, 4)
        self.shash_win = curses.newwin(31, 200, 26, 4)
        #self.win.initscr()
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(1)
        self.stdscr.addstr(2, 2, ' VimProx v0.1')
        self.stdscr.refresh()

    def close_curse(self):
        curses.nocbreak()
        self.stdscr.keypad(0)
        curses.echo()

        curses.endwin()

con = ConStats()
import atexit
atexit.register(con.close_curse)
