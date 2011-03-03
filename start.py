import os
import sys

import atexit
import threading
import cherrypy

sys.stdout = sys.stderr

ROOT='/opt/favicon_env/src'

sys.path.append(ROOT)
import favicon

conf = os.path.join(ROOT, 'prod.conf')
cherrypy.config.update(conf)
cherrypy.config.update({'favicon.root' : ROOT})

if cherrypy.__version__.startswith('3.0') and cherrypy.engine.state == 0:
  cherrypy.engine.start(blocking=False)
  atexit.register(cherrypy.engine.stop)

application = cherrypy.Application(favicon.PrintFavicon(), script_name=None, config=None)
