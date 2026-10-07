from django.conf import settings
from module_catalog import normalize_modules
from .capabilities import editor, role, enabled
from .models import Notification
from .lab_viewer import launcher

def site(request):
    return {"project_site": settings.SITE, 'lab_launcher': launcher(), 'modules':{key:True for key in normalize_modules(settings.SITE.get('modules',[]))},
        'site_editor':editor(request.user), 'site_owner':role(request.user)=='owner',
        'unread_notifications':Notification.objects.filter(user=request.user,read=False).count() if request.user.is_authenticated and enabled('notifications') else 0}
