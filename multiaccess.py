from StringIO import StringIO
import multiprocessing
import os.path
import shutil
import signal
import subprocess
#import sys
import time
import urlparse

FAVICON_LOCAL_SEARCHQUERY = "http://localhost:8080/s/?url=%s&skipCache=true"
FAVICON_KIKIN_SEARCHQUERY = "http://fav.us.kikin.com/s/?url=%s&skipCache=true"
GOOGLE_SEARCHQUERY = "http://www.google.com/s2/favicons?domain=%s"

#http://code.activestate.com/recipes/307871-timing-out-function/
class TimedOutExc(Exception):
    def __init__(self, value = "Timed Out"):
        self.value = value
    def __str__(self):
        return repr(self.value)

def timed_out(timeout):
    def decorate(f):
        def handler(signum, frame):
            raise TimedOutExc()

        def new_f(*args, **kwargs):
            old = signal.signal(signal.SIGALRM, handler)
            signal.alarm(timeout)
            try:
                result = f(*args, **kwargs)
            finally:
                signal.signal(signal.SIGALRM, old)
            signal.alarm(0)
            return result

        new_f.func_name = f.func_name
        return new_f

    return decorate

def wrap_keyboard(f):
    def new_f(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except KeyboardInterrupt:
            return
    new_f.func_name = f.func_name
    return new_f

@timed_out(10)
def curl(url, file_out):
    process = subprocess.Popen(['curl', '-s', url], stdout=subprocess.PIPE,
            close_fds=True)
    out, err = process.communicate()
    out = StringIO(out)
    shutil.copyfileobj(out, file_out)
    signal.alarm(0)

def getfile(url, modelquery=FAVICON_LOCAL_SEARCHQUERY):
    urlpieces = urlparse.urlparse(url)

    modified_netloc = urlpieces.netloc.replace("://",".")
    searchquery = modelquery % urlpieces.netloc
    f = open("results/%s.ico" % modified_netloc, "wb")
    try:
        curl(searchquery, f)
        print "done with %s" % urlpieces.netloc
    except TimedOutExc:
        print "timed out: %s" % modified_netloc

def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

if __name__=='__main__':
    wordlist = [line.strip() for line in open("topsites.txt")]

    for path in ["results"]:
        if not os.path.exists(path):
            os.mkdir(path)

    p = multiprocessing.Pool(processes=10, initializer=init_worker)
    results = p.map_async(getfile, wordlist[:10], chunksize=10)
    try:
        while not results.ready():
            time.sleep(5)
    except KeyboardInterrupt:
        print 'caught KeyboardInterrupt'
        p.terminate()
        p.join()

# vim: sts=4:sw=4:ts=4:tw=85:cc=85
