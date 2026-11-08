"""Role bundles seeded as Django groups.

Role names are deployment labels; application code must not branch on them.
Access is enforced with permissions. Object reach is enforced by the views
that own each model.

The bundles list only capabilities that map to a real code path. Permissions
without an enforcement point (``export_csv``, ``view_all_expenses``,
``approve_expense``) are not seeded.
"""

GROUPS_PERMISSIONS = {
    "Reception": [
        "core.add_patient", "core.change_patient", "core.view_patient",
        "core.add_visit", "core.view_visit", "core.start_visit",
        "core.add_payment", "core.view_payment",
        "core.add_visitservice", "core.view_visitservice",
    ],
    "Doctor": [
        "core.practice_medicine",
        "core.add_medicationorder", "core.change_medicationorder", "core.view_medicationorder",
        "core.add_medicalrecord", "core.change_medicalrecord", "core.view_medicalrecord",
    ],
    "Nurse": [
        "core.add_medicationadministration", "core.change_medicationadministration", "core.view_medicationadministration",
        "core.view_medicationorder",
        "core.view_operation",
        "core.add_operationconsumable", "core.change_operationconsumable", "core.view_operationconsumable",
    ],
    "Operations": [
        "core.view_patient",
        "core.add_operation", "core.change_operation", "core.view_operation",
        "core.start_visit",
    ],
    "Read-only": [
        "core.view_patient", "core.view_visit", "core.view_medicalrecord",
        "core.view_operation", "core.view_service", "core.view_visitservice",
    ],
    "Admin": [
        "core.view_auditlog",
        "core.add_patient", "core.change_patient", "core.delete_patient", "core.view_patient",
        "core.add_visit", "core.change_visit", "core.delete_visit", "core.view_visit",
        "core.add_payment", "core.change_payment", "core.delete_payment", "core.view_payment",
        "core.add_paymentmethod", "core.change_paymentmethod", "core.delete_paymentmethod", "core.view_paymentmethod",
        "core.add_service", "core.change_service", "core.delete_service", "core.view_service",
        "core.add_visitservice", "core.change_visitservice", "core.view_visitservice",
        "core.add_medicalrecord", "core.change_medicalrecord", "core.delete_medicalrecord", "core.view_medicalrecord",
        "core.practice_medicine",
        "core.add_medicationorder", "core.change_medicationorder", "core.view_medicationorder",
        "core.add_medicationadministration", "core.change_medicationadministration", "core.view_medicationadministration",
        "core.add_operation", "core.change_operation", "core.view_operation",
        "core.add_operationconsumable", "core.change_operationconsumable", "core.view_operationconsumable",
        "core.add_expense", "core.change_expense", "core.view_expense", "core.delete_expense",
        "core.add_expensecategory", "core.change_expensecategory", "core.delete_expensecategory", "core.view_expensecategory",
        "core.view_payment_ledger",
        "core.verify_payment",
        "inventory.manage_inventory",
        "core.manage_shift",
    ],
}

