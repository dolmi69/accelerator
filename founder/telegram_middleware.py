class TelegramFrameMiddleware:
    """Permit Telegram Web's iframe, while blocking other embedding sites."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.telegram_miniapp = True
        response = self.get_response(request)
        if not request.path.startswith("/admin/"):
            response.headers.pop("X-Frame-Options", None)
            response["Content-Security-Policy"] = "frame-ancestors 'self' https://web.telegram.org;"
        return response
