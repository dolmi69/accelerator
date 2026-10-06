from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from founder.models import (
    BrunoTask, EvidenceEntry, ChatAttachment, ChatMessage, ChatSession, MascotState, PitchReport,
    MessageFeedback, ProjectReview, StartupMemory, StartupMetrics, StartupProfile, User,
)


@admin.register(User)
class FounderUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Профиль сообщества', {'fields': ('handle', 'display_name', 'occupation', 'bio', 'location', 'profile_website')}),
    )
    list_display = (*UserAdmin.list_display, 'handle')
    search_fields = (*UserAdmin.search_fields, 'handle', 'display_name')
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
admin.site.register(ProjectReview)


@admin.register(MessageFeedback)
class MessageFeedbackAdmin(admin.ModelAdmin):
    list_display = ('updated_at', 'rating', 'comment', 'message')
    list_filter = ('rating',)
    search_fields = ('comment', 'message__content')
    raw_id_fields = ('message',)
