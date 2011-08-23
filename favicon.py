import StringIO
import cherrypy
import gzip
import json
import memcache
import os, os.path
import re
#import signal
import subprocess
import urllib2
import urlparse

import globals

from BeautifulSoup import BeautifulSoup
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader
from logging import DEBUG, INFO, WARN, ERROR, Formatter
from time import time

# helper methods

def timeout_handler(signum, frame):
  raise TimeoutError()

def libmagic(string):
  '''Example out= '/dev/stdin: image/x-ico; charset=binary'
  mime = image/x-ico(n)
  Browsers want '''
  process = subprocess.Popen(globals.FILECOMMAND_BSD,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE)
  out, err = process.communicate(input=string)
  file, mime, charset = filter(lambda string: string, re.split("[\s:;]",out))

  if mime in "image/x-ico":
    mime = "image/x-icon"
  return mime

def gunzip(stream):
  '''Don't use for even moderately big files'''
  f = StringIO.StringIO(stream)
  output = gzip.GzipFile(fileobj=f).read()
  f.close()
  return output

# classes

class Icon(object):
  '''container for storing favicon'''
  def __init__(self, data=None, location=None, type=None):
    super(Icon, self).__init__()
    self.data = data
    self.location = location
    self.type = type

class TimeoutError(Exception):

  def __str__(self):
    return repr(TimeoutError)

class BaseHandler(object):
  '''decodes urls using a regex'''

  def __init__(self):
    super(BaseHandler, self).__init__()
    self.re = globals.RE_URLDECODE

  def htc(self, m):
    return chr(int(m.group(1), 16))

  def urldecode(self, url):
    return self.re.sub(self.htc, url)

class PrintFavicon(BaseHandler):

  def __init__(self):
    super(PrintFavicon, self).__init__()

    default_icon_data = self.open(globals.DEFAULT_FAVICON_LOC, time()).read()

    self.default_icon = Icon(data=default_icon_data,
        location=globals.DEFAULT_FAVICON_LOC, type='image/png')
    self.env = Environment(loader=FileSystemLoader(
      os.path.join(cherrypy.config['favicon.root'], 'templates')))
    self.mc = memcache.Client(['%(memcache.host)s:%(memcache.port)d' %
      cherrypy.config], debug=2)

    # Initialize counters
    for counter in ['requests', 'hits', 'defaults']:
      self.mc.add('counter-%s' % counter, '0')

  def open(self, url, start, headers=None):
    time_spent = int(time() - start)
    if time_spent >= globals.TIMEOUT:
      raise TimeoutError(time_spent)

    if not headers:
      headers = dict()
    headers.update(globals.HEADERS)

    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor())
    result = opener.open(urllib2.Request(url, headers=headers),
        timeout=min(globals.CONNECTION_TIMEOUT, globals.TIMEOUT - time_spent))
    cherrypy.log('%s =redirect=> %s' % (url, result.url), severity=DEBUG)

    return result

  def validateIcon(self, iconResponse):
    '''Figures out mimetype and whether to gunzip.
    Thrown through a bunch of validation tests.
    No real reason to be an instance method.
    returns Icon or None if error'''
    #too many try/catch blocks here?
    url = iconResponse.url
    code = iconResponse.getcode()

    if code != 200:
      cherrypy.log('URL:%s Unsuccessful response: %s' % (url, code), severity=WARN)

    icon = iconResponse.read()
    length = len(icon)

    if not length:
      cherrypy.log('URL:%s Content-Length=0' % url, severity=ERROR)
      return None

    try:
      contentType = libmagic(icon)
    except OSError as e:
      cherrypy.log('URL:%s Unexpected OSError: %s' % (url, e), severity=ERROR)
      return None

    if 'gzip' in contentType:
      cherrypy.log('URL:%s Type is gzip, unpacking...' % url, severity=WARN)
      icon = gunzip(icon)
      try:
        contentType = libmagic(icon)
      except OSError as e:
        cherrypy.log('URL:%s Unexpected OSError: %s' % (url, e), severity=ERROR)

    if contentType in globals.ICON_MIMETYPE_BLACKLIST:
      cherrypy.log('URL:%s Content-Type:%s blacklisted', severity=ERROR)
      return None

    if length < globals.MIN_ICON_LENGTH or length > globals.MAX_ICON_LENGTH:
      cherrypy.log('URL:%s Warning: favicon size:%d out of bounds' % \
          (url, length), severity=WARN)

    return Icon(data=icon, type=contentType)

  def iconAtRoot(self, domain, start):
    '''check for icon at [domain]/favicon.ico'''
    cherrypy.log('URL:%s/favicon.ico Searching...' % domain, severity=DEBUG)
    path = urlparse.urljoin(domain, '/favicon.ico')
    result = self.open(path, start)
    rootIcon = self.validateIcon(result)

    if rootIcon:
      cherrypy.log('URL:%s/favicon.ico Found' % domain, severity=INFO)
      rootIcon.location = path
      return rootIcon
    return None

  # Icon specified in page?
  def iconInPage(self, domain, path, start, refresh=True):
    '''check for icon in <link rel="icon"> tag
    Follow http-equiv meta-refreshes if necessary'''
    cherrypy.log('URL:%s searching for <link> tag' % path, severity=DEBUG)

    try:
      rootDomainPageResult = self.open(path, start)

      if rootDomainPageResult.getcode() == 200:
        pageSoup = BeautifulSoup(rootDomainPageResult.read())
        pageSoupIcon = pageSoup.find('link', rel=globals.RE_LINKTAG)

        if pageSoupIcon:
          pageIconHref = pageSoupIcon.get('href')

          if pageIconHref:
            pageIconPath = urlparse.urljoin(path, pageIconHref)
            cherrypy.log('Found embedded favicon link:%s for domain:%s' % \
                         (pageIconPath, domain), severity=DEBUG)

            cookies = rootDomainPageResult.headers.getheaders("Set-Cookie")
            headers = None
            if cookies:
              headers = {'Cookie': ';'.join(cookies)}

            pagePathFaviconResult = self.open(pageIconPath,
                                              start,
                                              headers=headers)

            pageIcon = self.validateIcon(pagePathFaviconResult)
            if pageIcon:
              cherrypy.log('Found favicon at:%s for domain:%s' % \
                           (pageIconPath, domain),
                           severity=DEBUG)

              self.cacheIcon(domain, pageIconPath)
              pageIcon.location = pageIconPath
              return pageIcon

        else:
          if refresh:
            for meta in pageSoup.findAll('meta'):
              if meta.get('http-equiv', '').lower() == 'refresh':
                match = re.search('url=([^;]+)',
                               meta.get('content', ''),
                               flags=re.IGNORECASE)

                if match:
                  refreshPath = urlparse.urljoin(rootDomainPageResult.geturl(),
                                        match.group(1)).strip()

                  cherrypy.log('Processing refresh directive:%s for domain:%s' % \
                               (refreshPath, domain),
                               severity=DEBUG)

                  icon = self.iconInPage(domain,
                                         refreshPath,
                                         start,
                                         refresh=False) or \
                         self.iconAtRoot(refreshPath,
                                         start)

                  return icon


          cherrypy.log('No link tag found:%s' % path, severity=DEBUG)

      else:
        cherrypy.log('Non-success response:%d for url:%s' % \
                     (rootDomainPageResult.getcode(), path),
                     severity=DEBUG)

    except Exception as e:
      cherrypy.log('Error extracting favicon from page:%s, err:%s' % \
                   (path, e), severity=ERROR)

  def cacheIcon(self, domain, location):
    '''Used to cache to self.mc'''
    key = globals.KEY_FORMAT % str(domain)
    cherrypy.log('key=%s, value=%s' % (key, location), severity=DEBUG)

    ret = self.mc.set(key, str(location),
        time = globals.MC_CACHE_TIME)
    if not ret:
      cherrypy.log('key=%s, value=%s : could not cache', severity=ERROR)

  def iconInCache(self, targetDomain, start):
    icon_loc = self.mc.get('icon_loc-%s' % targetDomain)
    if icon_loc:
      cherrypy.log('URL:%s cache hit, location=%s' % (targetDomain, icon_loc),
                   severity=DEBUG)

      if icon_loc == globals.DEFAULT_FAVICON_LOC:
        self.mc.incr('counter-hits')
        self.mc.incr('counter-defaults')
        cherrypy.response.headers['X-Cache'] = 'Hit'
        return self.default_icon

      else:
        try:
          iconResult = self.open(icon_loc, start)
          icon = self.validateIcon(iconResult)
        except TimeoutError as e:
          cherrypy.log("TimeoutError: %s" % e, severity=ERROR)
          return None

        if icon:
          self.mc.incr('counter-hits')
          cherrypy.response.headers['X-Cache'] = 'Hit'
          icon.location = icon_loc
          return icon
        else:
          cherrypy.log('URL:%s cached location no longer valid' % \
                       targetDomain,
                       severity=INFO)

  def writeIcon(self, icon):
    self.writeHeaders(icon)
    return icon.data

  def writeHeaders(self, icon, fmt='%a, %d %b %Y %H:%M:%S %z'):
    # MIME Type
    cherrypy.response.headers['Content-Type'] = icon.type

    # Set caching headers
    cherrypy.response.headers['Cache-Control'] = 'public, max-age=2592000'
    cherrypy.response.headers['Expires'] = \
                          (datetime.now() + timedelta(days=30)).strftime(fmt)

  def parentLoc(self, url):
    urlPieces = urlparse.urlparse(self.urldecode(url))
    if not urlPieces or not urlPieces.scheme or not urlPieces.netloc:
      raise cherrypy.HTTPError(400, 'Malformed URL:%s' % url)

    parts = urlPieces.netloc.split('.')
    if len(parts) > 2:
      parent = '.'.join(parts[1:])
    else:
      parent = None

    cherrypy.log('URL:%s, parent:%s' % (urlPieces.netloc, parent), severity=DEBUG)
    return parent

  def parse(self, url):
    # Get page path
    targetPath = self.urldecode(url)
    if not targetPath.startswith('http'):
      targetPath = 'http://%s' % targetPath
    cherrypy.log('URL:%s decoded' % targetPath, severity=DEBUG)

    # Split path to get domain
    targetURL = urlparse.urlparse(targetPath)
    if not targetURL or not targetURL.scheme or not targetURL.netloc:
      raise cherrypy.HTTPError(400, 'Malformed URL:%s' % url)

    targetDomain = '%s://%s' % (targetURL.scheme, targetURL.netloc)
    cherrypy.log('URL:%s, domain:%s' % (targetPath, targetDomain),
                 severity=DEBUG)

    return (targetPath, targetDomain)

  @cherrypy.expose
  def index(self):
    status = {'status': 'ok', 'counters': dict()}
    for counter in ['requests', 'hits', 'defaults']:
      status['counters'][counter] = self.mc.get('counter-%s' %counter)
    return json.dumps(status)

  @cherrypy.expose
  def test(self):
    topSites = open(os.path.join(cherrypy.config['favicon.root'],
                                 'topsites.txt'), 'r').read().split()
    template = self.env.get_template('test.html')
    return template.render(topSites=topSites)

  @cherrypy.expose
  def clear(self, url):
    cherrypy.log('Incoming cache invalidation request:%s' % url,
                 severity=DEBUG)

    targetPath, targetDomain = self.parse(str(url))
    self.mc.delete('icon_loc-%s' % targetDomain)

    cherrypy.log('Evicted cache entry for %s' % targetDomain, severity=INFO)

  @cherrypy.expose
  def s(self, url, skipCache='false'):
    start = time()

    if skipCache.lower() == 'true':
      skipCache = True
    else:
      skipCache = False

    cherrypy.log('Incoming request:%s (skipCache=%s)' % (url, skipCache),
                 severity=DEBUG)

    self.mc.incr('counter-requests')

    targetPath, targetDomain = self.parse(str(url))

    #follow redirect for targetDomain -- ought to be in a separate function,
    #just like self.parse()
    redirectedPath, redirectedDomain = targetPath, targetDomain
    parentDomain = targetDomain
    temp_opener = urllib2.build_opener()
    try:
      temp_result = temp_opener.open(urllib2.Request(targetDomain, headers=globals.HEADERS),
                timeout=globals.CONNECTION_TIMEOUT)
      if temp_result.url:
        redirectedPath, redirectedDomain = self.parse(str(temp_result.url))
        parentDomain = self.parentLoc(temp_result.url)
        if parentDomain is None:
          parentDomain = targetDomain
    except Exception as e:
      cherrypy.log('Url:%s - failed to load/redirect because of %s' % (url,e),
                severity=WARN)
    #end redirect setup

    #extra lines from previous --
    #last line is for sites like blogger.com at the time of this writing
    icon = (not skipCache and self.iconInCache(redirectedDomain, start)) or \
           self.iconInPage(redirectedDomain, redirectedPath, start) or \
           self.iconAtRoot(redirectedDomain, start) or \
           self.iconAtRoot(parentDomain, start) or \
           self.iconAtRoot(targetDomain, start)

    if not icon:
      cherrypy.log('Falling back to default icon for:%s' % targetDomain,
                   severity=DEBUG)

      self.cacheIcon(targetDomain, globals.DEFAULT_FAVICON_LOC)
      self.mc.incr('counter-defaults')
      icon = self.default_icon

    cherrypy.log('Time taken to process domain:%s %f' % \
                 (targetDomain, time() - start),
                 severity=INFO)

    return self.writeIcon(icon)


if __name__ == '__main__':
  config = os.path.join(os.getcwd(), 'dev.conf')

  cherrypy.config.update(config)
  cherrypy.config.update({'favicon.root': os.getcwd()})
  stream = cherrypy.log.error_log.handlers[0]
  FORMATTER = Formatter(fmt="FILE:%(filename)-12s FUNC:%(funcName)-16s"
        + " LINE:%(lineno)-4s %(levelname)-8s %(message)s")
  stream.setFormatter(FORMATTER)
  cherrypy.log.error_log.setLevel(DEBUG)
  stream.setLevel(DEBUG)

  cherrypy.quickstart(PrintFavicon(), config=config)

# vim: sts=2:sw=2:ts=2:tw=85
