import cherrypy
import os.path
import json

from re import compile, MULTILINE, IGNORECASE
from urlparse import urlparse, urljoin
from urllib import urlopen
from datetime import datetime, timedelta
from BeautifulSoup import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from memcache import Client

from traceback import print_exc

class BaseHandler(object):

  def __init__(self):
    super(BaseHandler, self).__init__()
    self.re = compile('%([0-9a-hA-H][0-9a-hA-H])', MULTILINE)
  
  def htc(self, m):
    return chr(int(m.group(1), 16))
  
  def urldecode(self, url):
    return self.re.sub(self.htc, url)


class PrintFavicon(BaseHandler):

  ICON_MIMETYPE_BLACKLIST = [
    'application/xml',
    'text/html',
  ]

  MIN_ICON_LENGTH = 100
  MAX_ICON_LENGTH = 20000
  EMPTY_ICON_LENGTH = 1150

  MC_CACHE_TIME = 2419200 # seconds (28 days)

  def __init__(self):
    super(PrintFavicon, self).__init__()
    
    with open('favicon.gif', 'r') as f:
      self.default_icon = f.read()
    
    self.loader = FileSystemLoader('templates')
    self.env = Environment(loader=self.loader)
    
    self.mc = Client(['127.0.0.1:11211'], debug=0)
    self._init_counters()

  def _init_counters(self):
    for counter in ['requests', 'hits', 'defaults']:
      self.mc.add('counter-%s' % counter, '0')

  def isValidIconResponse(self, iconResponse):
    if iconResponse.getcode() != 200:
      return (False, 'Recieved non-success code %d' % iconResponse.getcode())

    iconContentType = iconResponse.info().gettype()
    if iconContentType in PrintFavicon.ICON_MIMETYPE_BLACKLIST:
      return (False, 'Content-Type %s is blacklisted' % iconContentType)

    icon = iconResponse.read()
    iconLength = len(icon)
    if iconLength < PrintFavicon.MIN_ICON_LENGTH or iconLength > PrintFavicon.MAX_ICON_LENGTH:
      return (False, 'Length=%d exceeds allowed range' % iconLength)

    self.icon = icon
    self.cacheIcon(icon)

    return (True,)

  def iconAtRoot(self):
    rootIconPath = self.targetDomain + '/favicon.ico'
    try:
      rootDomainFaviconResult = urlopen(rootIconPath)
      if self.isValidIconResponse(rootDomainFaviconResult)[0]:
        return True
    except:
      print_exc()
      pass
    return False

  def iconInPage(self):
    try:
      rootDomainPageResult = urlopen(self.targetPath)
      if rootDomainPageResult.getcode() == 200:
        pageSoup = BeautifulSoup(rootDomainPageResult.read())
        pageSoupIcon = pageSoup.find('link', rel=compile('^(shortcut|icon|shortcut icon)$', IGNORECASE))
        if pageSoupIcon:
          pageIconHref = pageSoupIcon.get('href')
          if pageIconHref:
            pageIconPath = urljoin(self.targetPath, pageIconHref)
            pagePathFaviconResult = urlopen(pageIconPath)
            if self.isValidIconResponse(pagePathFaviconResult)[0]:
              return True
    except:
      print_exc()
      pass
    return False

  def cacheIcon(self, icon):
    self.mc.set('icon-%s' % self.targetDomain, icon, time=PrintFavicon.MC_CACHE_TIME)

  def iconInCache(self):
    icon = self.mc.get('icon-%s' % self.targetDomain)
    if icon:
      self.mc.incr('counter-hits')
      cherrypy.response.headers['X-Cache'] = 'Hit'
      if icon == 'DEFAULT':
        self.mc.incr('counter-defaults')
        self.icon = self.default_icon
      else:
        self.icon = icon
      return True
    return False

  def writeIcon(self):
    self.writeHeaders()
    return self.icon

  def writeHeaders(self, fmt='%a, %d %b %Y %H:%M:%S %z'):
    # MIME Type
    cherrypy.response.headers['Content-Type'] = 'image/x-icon'

    # Set caching headers
    cherrypy.response.headers['Cache-Control'] = 'public, max-age=2592000'
    cherrypy.response.headers['Expires'] = (datetime.now() + timedelta(days=30)).strftime(fmt)

  @cherrypy.expose
  def index(self):
    status = { 'status' : 'ok', 'counters' : {} }
    for counter in ['requests', 'hits', 'defaults']:
      status['counters'][counter] = self.mc.get('counter-%s' %counter)
    return json.dumps(status)

  @cherrypy.expose
  def test(self):
    topSites = []
    with open('topsites.txt', 'r') as f:
      for line in f:
        topSites.append(line.strip())

    template = self.env.get_template('test.html')
    return template.render(topSites=topSites)

  @cherrypy.expose
  def default(self, url):
    self.mc.incr('counter-requests')

    # Get page path
    self.targetPath = self.urldecode(url)

    # Split path to get domain
    targetURL = urlparse(self.targetPath)
    self.targetDomain =  '%s://%s' % (targetURL.scheme, targetURL.netloc)

    if not self.iconInCache():
      # Icon at [domain]/favicon.ico?
      if not self.iconAtRoot():
        # Icon specified in page?
        if not self.iconInPage():
          # Use default
          self.icon = self.default_icon 
          self.cacheIcon('DEFAULT')
          self.mc.incr('counter-defaults')

    return self.writeIcon()

def main():
  conf = os.path.join(os.path.dirname(__file__), 'favicon.conf')
  cherrypy.quickstart(PrintFavicon(), config=conf)

if __name__ == '__main__':
  main()
