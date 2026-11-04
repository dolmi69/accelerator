TELEGRAM_ANCESTORS = "frame-ancestors 'self' https://web.telegram.org"


def allow_telegram_framing(policy):
    """Add Telegram Web as a permitted ancestor without dropping the page's own protections.

    Previews of generated sites and user files rely on `sandbox` and `connect-src 'none'`
    in their CSP. Replacing the whole header removed that isolation, so only the
    frame-ancestors directive changes. A page that forbids framing entirely keeps 'none'.
    """
    if not policy or not policy.strip():
        return TELEGRAM_ANCESTORS + ";"
    directives = [part.strip() for part in policy.split(";") if part.strip()]
    merged, found = [], False
    for directive in directives:
        if directive.split(None, 1)[0].lower() != "frame-ancestors":
            merged.append(directive)
            continue
        found = True
        merged.append(directive if "'none'" in directive.lower() else TELEGRAM_ANCESTORS)
    if not found:
        merged.append(TELEGRAM_ANCESTORS)
    return "; ".join(merged)


class TelegramFrameMiddleware:
    """Permit Telegram Web's iframe, while blocking other embedding sites."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.telegram_miniapp = True
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.headers.pop("X-Frame-Options", None)
            response["Content-Security-Policy"] = allow_telegram_framing(response.get("Content-Security-Policy"))
        return response
