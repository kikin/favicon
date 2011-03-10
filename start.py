import os.path
import sys

import atexit
import threading
import cherrypy

sys.stdout = sys.stderr

ROOT='/opt/favicon_env'

sys.path.append(os.path.join(ROOT, 'src'))
import favicon

from logging import handlers, DEBUG

# Remove the default FileHandlers if present.
cherrypy.log.error_file = ''

# Make a new RotatingFileHandler for the error log.
err_fname = getattr(cherrypy.log,
                    'rot_error_file',
                    os.path.join(ROOT, 'logs/errorLog'))

err_handler = handlers.TimedRotatingFileHandler(err_fname, 'midnight', 1, 7)
err_handler.setLevel(DEBUG)
err_handler.setFormatter(cherrypy._cplogging.logfmt)

cherrypy.log.error_log.addHandler(err_handler)

# Load config
config = os.path.join(ROOT, 'src/prod.conf')
cherrypy.config.update(config)
cherrypy.config.update({'favicon.root' : os.path.join(ROOT, 'src')})

if cherrypy.__version__.startswith('3.0') and cherrypy.engine.state == 0:
  cherrypy.engine.start(blocking=False)
  atexit.register(cherrypy.engine.stop)

application = cherrypy.Application(favicon.PrintFavicon(),
                                   script_name=None,
                                   config=config)

