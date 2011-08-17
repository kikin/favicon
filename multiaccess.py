from multiprocessing import Pool
from IPython.core.interactiveshell import getoutput
import urlparse
import os.path

#send multiple requests to localhost:8080/ with lines from a file
def f(param):
    modified_param = param.replace("://",".")
    searchquery = "curl -s http://localhost:8080/s/?url=%s&skipCache=true" % param
    #searchquery = "curl http://localhost:8080/s/?url=%s -o results/%s.ico" % (param, modified_param)
    f = open("results/%s.ico" % modified_param, "wb")
    result = getoutput(searchquery)
    f.write(result)
    f.close()
    print("done with %s" % param)

#send requests to google.com/s2/favicon
def g(param):
    modified_param = param.replace("://",".")
    pieces = urlparse.urlparse(param)
    searchquery = "curl http://www.google.com/s2/favicons?domain=%s -o results_s2/%s.ico" % (pieces.netloc, modified_param)
    #print "%s%s"  % (param.ljust(40,' '), pieces.netloc.rjust(20,' '))

    print getoutput( searchquery )
    print("done with %s" % param)

#send requests to getfavicon.com/?url=
def h(param):
    modified_param = param.replace("://",".")
    pieces = urlparse.urlparse(param)
    print pieces.netloc
    #searchquery = "curl http://www.getfavicon.org/results.php\?url\=%s/favicon.png  -o results_getfavicon/%s.ico" % (pieces.netloc, modified_param)
    searchquery = "curl http://www.getfavicon.org/?url=%s/favicon.png -o results_getfavicon/%s.ico >/dev/null" % (pieces.netloc, modified_param)
    print searchquery
    try:
        print getoutput( searchquery )
        print getoutput( "file results_getfavicon/%s.ico" % modified_param)
    except Exception:
        print "oh well"
    print("done with %s" % param)


#send requests to getfavicon.org

wordlist = [line.strip() for line in open("topsites.txt")]

if __name__=='__main__':
    for path in ["results_s2", "results", "results_getfavicon"]:
        if not os.path.exists(path):
            os.mkdir(path)

    p = Pool(processes=3)
    result = p.map(f, wordlist)

