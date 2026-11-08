from django.contrib import admin

from .models import Consumable, Consumption, Location, Stock


@admin.register(Consumable)
class ConsumableAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'unit', 'category', 'service', 'active')
    list_filter = ('active', 'category')
    search_fields = ('name', 'sku')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'floor')
    list_filter = ('floor',)


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('consumable', 'location', 'quantity')
    list_filter = ('location',)
    search_fields = ('consumable__name',)
    raw_id_fields = ('consumable',)


@admin.register(Consumption)
class ConsumptionAdmin(admin.ModelAdmin):
    list_display = ('consumable', 'location', 'quantity', 'reason', 'created_at', 'created_by')
    list_filter = ('reason', 'location', 'created_at')
    search_fields = ('consumable__name', 'notes')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
