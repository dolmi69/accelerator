from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from founder.models import (
    BrunoTask, EvidenceEntry, ChatAttachment, ChatMessage, ChatSession, MascotState, PitchReport,
    StartupMemory, StartupMetrics, StartupProfile, User,
)


admin.site.register(User, UserAdmin)
admin.site.register(StartupProfile)
admin.site.register(StartupMetrics)
admin.site.register(MascotState)
admin.site.register(ChatSession)
admin.site.register(ChatMessage)
admin.site.register(ChatAttachment)
admin.site.register(StartupMemory)
admin.site.register(PitchReport)


admin.site.register(BrunoTask)
admin.site.register(EvidenceEntry)
