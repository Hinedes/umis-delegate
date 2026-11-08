from django.contrib import admin

from .models import (
    AuditLog,
    Expense,
    ExpenseCategory,
    MedicalRecord,
    MedicationAdministration,
    MedicationOrder,
    Operation,
    OperationConsumable,
    Patient,
    Payment,
    PaymentMethod,
    Shift,
    Visit,
)


@admin.register(MedicationOrder)
class MedicationOrderAdmin(admin.ModelAdmin):
    list_display = ('prescribed_at', 'visit', 'consumable', 'total_quantity', 'prescribed_by', 'cancelled_at')
    list_filter = ('cancelled_at',)
    search_fields = ('visit__patient__full_name', 'consumable__name')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MedicationAdministration)
class MedicationAdministrationAdmin(admin.ModelAdmin):
    list_display = ('administered_at', 'order', 'quantity_given', 'administered_by', 'voided_at')
    list_filter = ('voided_at',)
    search_fields = ('order__visit__patient__full_name',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ('operation_datetime', 'patient', 'operation_type', 'performed_by_user')
    search_fields = ('patient__full_name', 'operation_type')
    date_hierarchy = 'operation_datetime'


@admin.register(OperationConsumable)
class OperationConsumableAdmin(admin.ModelAdmin):
    list_display = ('operation', 'consumable', 'quantity', 'used_at', 'used_by')
    date_hierarchy = 'used_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('opened_at', 'closed_at', 'floor', 'status', 'opening_cash', 'closing_cash', 'cash_difference')
    list_filter = ('status', 'floor')
    date_hierarchy = 'opened_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'active', 'order')
    list_filter = ('active',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('date', 'category', 'amount', 'recipient', 'floor', 'visit_service')
    list_filter = ('category', 'floor')
    search_fields = ('recipient', 'explanation')
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'kind', 'active', 'order')
    list_filter = ('kind', 'active')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_datetime', 'visit', 'type', 'amount', 'method', 'status')
    list_filter = ('type', 'status', 'method__kind')
    search_fields = ('visit__patient__full_name',)
    date_hierarchy = 'payment_datetime'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'phone', 'floor', 'created_at')
    list_filter = ('floor',)
    search_fields = ('full_name', 'phone', 'national_id')


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ('patient', 'record_type', 'doctor', 'record_datetime')
    list_filter = ('record_type',)
    search_fields = ('patient__full_name',)


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ('visit_datetime', 'patient', 'assigned_doctor', 'floor', 'status')
    list_filter = ('status', 'floor', 'visit_type')
    search_fields = ('patient__full_name',)
    date_hierarchy = 'visit_datetime'


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'actor_label', 'action', 'object_type', 'object_id', 'source')
    list_filter = ('action', 'source', 'created_at')
    search_fields = ('actor_label', 'object_type', 'object_id', 'description')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

