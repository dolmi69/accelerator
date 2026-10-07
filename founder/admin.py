from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from founder.models import (
    BrunoTask, EvidenceEntry, ChatAttachment, ChatMessage, ChatSession, MascotState, PitchReport,
    MessageFeedback, ProjectReview, StartupMemory, StartupMetrics, StartupProfile, User, LabAIUsage,
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


@admin.register(LabAIUsage)
class LabAIUsageAdmin(admin.ModelAdmin):
    """Audit trail is read-only; budget adjustments need explicit reconciliation."""
    list_display = ('created_at', 'user', 'operation', 'status', 'input_tokens', 'output_tokens', 'accounted_rub')
    list_filter = ('status', 'operation', 'model')
    readonly_fields = tuple(field.name for field in LabAIUsage._meta.fields)
    actions = None

    @admin.display(description='Учтено, ₽')
    def accounted_rub(self, obj):
        from decimal import Decimal
        return Decimal(obj.accounted_micro_rub) / 1_000_000

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
