(function () {
    function selectForm(root) {
        if (!root) {
            return null;
        }
        if (root.matches && root.matches("#visitForm[data-visit-form='desktop']")) {
            return root;
        }
        return root.querySelector ? root.querySelector("#visitForm[data-visit-form='desktop']") : null;
    }

    function ensureDeleteInput(entry, index) {
        let deleteInput = entry.querySelector(`input[name="form-${index}-DELETE"]`);
        if (deleteInput) {
            return deleteInput;
        }

        deleteInput = document.createElement("input");
        deleteInput.type = "hidden";
        deleteInput.name = `form-${index}-DELETE`;
        deleteInput.id = `id_form-${index}-DELETE`;
        entry.appendChild(deleteInput);
        return deleteInput;
    }

    function ensurePatientOption(patientSelect) {
        const hasNewPatientOption = Array.from(patientSelect.options).some(function (option) {
            return option.value === "__NEW_PATIENT__";
        });

        if (hasNewPatientOption) {
            return;
        }

        const newOption = document.createElement("option");
        newOption.value = "__NEW_PATIENT__";
        newOption.text = "+ New patient";
        patientSelect.insertBefore(newOption, patientSelect.firstChild);
    }

    function initPatientSelect(patientSelect, newPatientUrl) {
        if (!patientSelect || !window.TomSelect) {
            return;
        }

        if (newPatientUrl) ensurePatientOption(patientSelect);

        if (patientSelect.tomselect) {
            return;
        }

        const tomSelect = new TomSelect(patientSelect, {
            create: false,
            sortField: { field: "text", direction: "asc" },
            dropdownParent: "body",
            dropdownClass: "ts-dropdown visit-screen-patient-dropdown",
            placeholder: "Select a patient",
            maxOptions: 100,
            onChange: function (value) {
                if (value === "__NEW_PATIENT__") {
                    window.location.href = newPatientUrl;
                }
            },
            render: {
                option: function (data, escape) {
                    if (data.value === "__NEW_PATIENT__") {
                        return `<div class="option new-patient-option">${escape(data.text)}</div>`;
                    }
                    return `<div class="option">${escape(data.text)}</div>`;
                },
                item: function (data, escape) {
                    return `<div>${escape(data.text)}</div>`;
                },
            },
        });

        // Tom Select copies source classes onto its outer wrapper; strip design-system
        // input classes there so only the inner control renders as the field surface.
        if (tomSelect.wrapper) {
            tomSelect.wrapper.classList.remove("form-control-core", "pwa-input");
        }
        patientSelect.setAttribute('aria-hidden', 'true');
        patientSelect.tabIndex = -1;
    }

    function createBillingRow(index, name, price, category, qty) {
        const priceNumber = parseFloat(price) || 0;
        const quantity = Math.max(1, parseInt(qty, 10) || 1);
        const row = document.createElement("article");
        const lineTotalValue = (priceNumber * quantity).toFixed(2);
        const categoryCopy = category === "MEDICINE" ? "Clinic medicine" : "Clinical service";

        row.className = "billing-item visit-summary-item";
        row.dataset.idx = String(index);
        row.dataset.price = String(price);
        row.dataset.qty = String(quantity);
        row.dataset.category = category;

        const main = document.createElement("div");
        main.className = "visit-summary-item-main";

        const copy = document.createElement("div");
        copy.className = "visit-summary-item-copy";

        const nameTitle = document.createElement("h3");
        nameTitle.className = "visit-summary-item-name";
        nameTitle.textContent = name;

        const caption = document.createElement("p");
        caption.className = "visit-summary-item-caption";
        caption.textContent = categoryCopy;

        copy.appendChild(nameTitle);
        copy.appendChild(caption);

        const priceValue = document.createElement("div");
        priceValue.className = "visit-summary-item-price line-total";
        priceValue.textContent = lineTotalValue;

        main.appendChild(copy);
        main.appendChild(priceValue);

        const meta = document.createElement("div");
        meta.className = "visit-summary-item-meta";

        const unitPrice = document.createElement("span");
        unitPrice.className = "visit-summary-item-unit-price";
        unitPrice.textContent = `Price ${priceNumber.toFixed(2)}`;

        const qtyWrap = document.createElement("label");
        qtyWrap.className = "visit-summary-qty";

        const qtyLabel = document.createElement("span");
        qtyLabel.className = "visually-hidden";
        qtyLabel.textContent = "Quantity";

        const qtyInput = document.createElement("input");
        qtyInput.type = "number";
        qtyInput.min = "1";
        qtyInput.value = String(quantity);
        qtyInput.className = "billing-qty-input visit-summary-qty-input";
        qtyInput.setAttribute("data-role", "quantity");
        qtyWrap.appendChild(qtyLabel);
        qtyWrap.appendChild(qtyInput);

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "billing-remove-btn visit-summary-remove-btn";
        removeButton.setAttribute("data-role", "remove");
        removeButton.setAttribute("aria-label", "Remove line");
        removeButton.textContent = "\u00d7";

        meta.appendChild(unitPrice);
        meta.appendChild(qtyWrap);
        meta.appendChild(removeButton);

        row.appendChild(main);
        row.appendChild(meta);
        return row;
    }

    function createHiddenEntry(index, id, price, qty, name, category) {
        const entry = document.createElement("div");
        entry.dataset.idx = String(index);
        entry.dataset.selected = "1";
        entry.dataset.nameDisplay = name;
        entry.dataset.price = String(price);
        entry.dataset.qty = String(qty);
        entry.dataset.category = category;

        [
            ["service", id],
            ["quantity", qty],
            ["DELETE", ""],
        ].forEach(function (pair) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = `form-${index}-${pair[0]}`;
            input.id = `id_form-${index}-${pair[0]}`;
            input.value = String(pair[1]);
            entry.appendChild(input);
        });

        return entry;
    }

    function init(root) {
        const form = selectForm(root);
        if (!form || form.dataset.jsReady === "true") {
            return;
        }

        form.dataset.jsReady = "true";

        const patientSelect = form.querySelector("#id_patient");
        const searchInput = form.querySelector("#serviceSearch");
        const formsetContainer = form.querySelector("#formsetContainer");
        const totalForms = form.querySelector("#id_form-TOTAL_FORMS");
        const billingBody = form.querySelector("#billingBody");
        const totalDisplay = form.querySelector("#totalDisplay");
        const emptyRow = form.querySelector("#emptyRow");
        const noResults = form.querySelector("#noResults");
        const servicesHeader = form.querySelector("#servicesHeader");
        const medicinesHeader = form.querySelector("#medicinesHeader");
        const servicesSubtotal = form.querySelector("#servicesSubtotal");
        const medicinesSubtotal = form.querySelector("#medicinesSubtotal");
        const servicesTotalDisplay = form.querySelector("#servicesTotal");
        const medicinesTotalDisplay = form.querySelector("#medicinesTotal");
        const summarySubtotalDisplay = form.querySelector("#summarySubtotalDisplay");
        const serviceTiles = Array.from(form.querySelectorAll(".service-tile"));

        if (
            !formsetContainer ||
            !totalForms ||
            !billingBody ||
            !totalDisplay ||
            !emptyRow ||
            !servicesHeader ||
            !medicinesHeader ||
            !servicesSubtotal ||
            !medicinesSubtotal ||
            !servicesTotalDisplay ||
            !medicinesTotalDisplay
        ) {
            return;
        }

        initPatientSelect(patientSelect, form.dataset.newPatientUrl);

        let formIndex = parseInt(totalForms.value, 10) || 0;

        function getActiveRows(category) {
            const selector = category
                ? `.billing-item[data-category="${category}"]:not(.deleted)`
                : ".billing-item:not(.deleted)";
            return Array.from(billingBody.querySelectorAll(selector));
        }

        function insertRow(row) {
            const category = row.dataset.category;
            const rows = getActiveRows(category);
            const anchor = category === "SERVICE" ? servicesHeader : medicinesHeader;

            if (rows.length > 0) {
                rows[rows.length - 1].insertAdjacentElement("afterend", row);
                return;
            }

            anchor.insertAdjacentElement("afterend", row);
        }

        function updateTotals() {
            let servicesTotal = 0;
            let medicinesTotal = 0;

            getActiveRows().forEach(function (row) {
                const price = parseFloat(row.dataset.price) || 0;
                const qty = parseInt(row.dataset.qty, 10) || 1;
                const lineTotal = price * qty;

                if (row.dataset.category === "SERVICE") {
                    servicesTotal += lineTotal;
                } else {
                    medicinesTotal += lineTotal;
                }
            });

            const combinedTotal = servicesTotal + medicinesTotal;

            servicesTotalDisplay.textContent = servicesTotal.toFixed(2);
            medicinesTotalDisplay.textContent = medicinesTotal.toFixed(2);
            if (summarySubtotalDisplay) {
                summarySubtotalDisplay.textContent = combinedTotal.toFixed(2);
            }
            totalDisplay.textContent = servicesTotal.toFixed(2);

            const hasServices = getActiveRows("SERVICE").length > 0;
            const hasMedicines = getActiveRows("MEDICINE").length > 0;

            servicesHeader.classList.toggle('is-hidden', !hasServices);
            medicinesHeader.classList.toggle('is-hidden', !hasMedicines);
            servicesSubtotal.classList.toggle('is-hidden', !hasServices);
            medicinesSubtotal.classList.toggle('is-hidden', !hasMedicines);
            emptyRow.hidden = hasServices || hasMedicines;
        }

        function addService(id, name, price, category) {
            const entry = createHiddenEntry(formIndex, id, price, 1, name, category);
            formsetContainer.appendChild(entry);
            insertRow(createBillingRow(formIndex, name, price, category, 1));
            formIndex += 1;
            totalForms.value = String(formIndex);
            updateTotals();
        }

        serviceTiles.forEach(function (tile) {
            tile.addEventListener("click", function () {
                addService(tile.dataset.id, tile.dataset.nameDisplay || "", tile.dataset.price, tile.dataset.category);
                if (searchInput) {
                    searchInput.value = "";
                    serviceTiles.forEach(function (item) {
                        item.hidden = false;
                    });
                    if (noResults) {
                        noResults.style.display = "none";
                    }
                    searchInput.focus();
                }
            });
        });

        if (searchInput && noResults) {
            searchInput.addEventListener("input", function () {
                const term = searchInput.value.toLowerCase().trim();
                let visible = 0;

                serviceTiles.forEach(function (tile) {
                    const matches = !term || (tile.dataset.name || "").includes(term);
                    tile.hidden = !matches;
                    if (matches) {
                        visible += 1;
                    }
                });

                noResults.style.display = visible > 0 ? "none" : "block";
            });
        }

        billingBody.addEventListener("input", function (event) {
            if (!(event.target instanceof HTMLInputElement) || event.target.dataset.role !== "quantity") {
                return;
            }

            const row = event.target.closest(".billing-item");
            if (!row) {
                return;
            }

            const index = row.dataset.idx;
            const quantity = Math.max(1, parseInt(event.target.value, 10) || 1);
            const quantityInput = formsetContainer.querySelector(`input[name="form-${index}-quantity"]`);
            const lineTotal = row.querySelector(".line-total");

            row.dataset.qty = String(quantity);
            event.target.value = String(quantity);

            if (quantityInput) {
                quantityInput.value = String(quantity);
            }
            if (lineTotal) {
                lineTotal.textContent = ((parseFloat(row.dataset.price) || 0) * quantity).toFixed(2);
            }

            updateTotals();
        });

        billingBody.addEventListener("click", function (event) {
            const removeButton = event.target.closest("[data-role='remove']");
            if (!removeButton) {
                return;
            }

            const row = removeButton.closest(".billing-item");
            if (!row) {
                return;
            }

            const index = row.dataset.idx;
            const entry = formsetContainer.querySelector(`[data-idx="${index}"]`);

            if (entry) {
                ensureDeleteInput(entry, index).value = "on";
                entry.dataset.selected = "0";
            }

            row.classList.add("deleted");
            row.hidden = true;
            updateTotals();
        });

        Array.from(formsetContainer.querySelectorAll("[data-idx]")).forEach(function (entry) {
            if (entry.dataset.selected !== "1") {
                return;
            }

            const index = parseInt(entry.dataset.idx, 10);
            const tile = serviceTiles.find(item => item.dataset.id === entry.dataset.serviceId);
            ensureDeleteInput(entry, index);
            insertRow(
                createBillingRow(
                    index,
                    entry.dataset.nameDisplay || tile?.dataset.nameDisplay || "Unavailable service",
                    entry.dataset.price || tile?.dataset.price || "0",
                    entry.dataset.category || tile?.dataset.category || "SERVICE",
                    entry.dataset.qty || "1"
                )
            );
        });

        updateTotals();
    }

    function handleKeydown(event) {
        const form = selectForm(document);
        if (!form) {
            return;
        }

        const searchInput = form.querySelector("#serviceSearch");
        const cancelUrl = form.dataset.cancelUrl;

        if (event.key === "/" && !event.target.closest('input, textarea, select, [contenteditable]')) {
            event.preventDefault();
            if (searchInput) {
                searchInput.focus();
            }
            return;
        }

        if (event.key === "Enter" && event.ctrlKey) {
            event.preventDefault();
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
            } else {
                form.submit();
            }
            return;
        }

        if (event.key === "Escape" && cancelUrl && !document.body.classList.contains('navigation-open') && !document.querySelector('.modal.show, .ts-dropdown:not([style*="display: none"])')) {
            window.location.href = cancelUrl;
            return;
        }

        if (event.key === "Enter" && document.activeElement === searchInput) {
            event.preventDefault();
            const firstVisible = form.querySelector(".service-tile:not([hidden])");
            if (firstVisible) {
                firstVisible.click();
            }
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        init(document);
    });

    document.body.addEventListener("htmx:load", function (event) {
        init(event.target);
    });

    if (!document.body.dataset.visitFormShortcutsBound) {
        document.body.dataset.visitFormShortcutsBound = "true";
        document.addEventListener("keydown", handleKeydown);
    }
})();
