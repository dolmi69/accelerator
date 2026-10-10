from django.contrib import admin

from payments.models import Balance, Invoice


@admin.register(Balance)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ("user", "amount", "updated_at")
    search_fields = ("user__username", "user__email")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "order_id", "user", "status", "amount_expected", "amount_received",
        "asset", "created_at", "paid_at",
    )
    list_filter = ("status", "asset", "network")
    search_fields = ("order_id", "address", "txid", "user__username")
    readonly_fields = ("id", "created_at", "paid_at")
