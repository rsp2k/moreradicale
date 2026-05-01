"""
moreradicale WSGI file (mod_wsgi and uWSGI compliant).

"""

import os
from moreradicale import application

# set an environment variable
os.environ.setdefault('SERVER_GATEWAY_INTERFACE', 'Web')
