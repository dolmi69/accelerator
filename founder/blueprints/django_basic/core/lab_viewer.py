"""Optional local laboratory chrome; downloaded sites have no launcher context."""
import json
from urllib.parse import urlsplit

from django.conf import settings


def launcher():
    if not settings.DEBUG:
        return None
    try:
        data = json.loads((settings.BASE_DIR / '.lab-viewer.json').read_text())
        origin = data['origin']
        path = data['return_path']
        parsed = urlsplit(origin)
        parsed.port  # Reject malformed ports before using the value in a header.
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1'}
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or not path.startswith(f"/startups/{settings.SITE['project_id']}/lab/?version=")
                or any(c in origin + path for c in '\r\n\\')):
            return None
        return {'origin': origin, 'url': origin + path}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def ancestors():
    context = launcher()
    return "'self' " + context['origin'] if context else "'none'"
