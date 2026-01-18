(() => {
  const canReceive = Boolean(window.MUTASI_DETAIL?.canReceive);
  const receiveAllButton = document.getElementById("receive-all");
  const printButton = document.getElementById("print-detail");
  const rows = Array.from(document.querySelectorAll(".detail-table tbody tr"));

  const parseNumber = (value) => {
    const raw = String(value ?? "").trim();
    if (!raw) {
      return 0;
    }
    const normalized = raw.replace(/\s+/g, "").replace(",", ".");
    const parsed = parseFloat(normalized);
    return Number.isNaN(parsed) ? 0 : parsed;
  };

  const formatNumber = (value) => {
    if (Number.isNaN(value)) {
      return "0";
    }
    const rounded = Math.round(value * 100) / 100;
    return Number.isInteger(rounded) ? `${rounded}` : rounded.toFixed(2);
  };

  const updateRow = (row) => {
    if (!row) {
      return;
    }
    const qtySent = parseNumber(row.dataset.qtySent);
    const input = row.querySelector(".receive-input");
    const diffEl = row.querySelector(".diff-value");
    const readonlyValue = row.querySelector(".readonly-value");
    const qtyReceived = input
      ? parseNumber(input.value)
      : parseNumber(readonlyValue?.textContent);
    const diff = Math.max(qtySent - qtyReceived, 0);
    if (diffEl) {
      diffEl.textContent = formatNumber(diff);
    }
    row.classList.toggle("row-missing", diff > 0);
  };

  if (canReceive) {
    rows.forEach((row) => {
      const input = row.querySelector(".receive-input");
      if (!input) {
        return;
      }
      input.addEventListener("input", () => updateRow(row));
    });

    if (receiveAllButton) {
      receiveAllButton.addEventListener("click", () => {
        rows.forEach((row) => {
          const input = row.querySelector(".receive-input");
          if (!input) {
            return;
          }
          const qtySent = parseNumber(row.dataset.qtySent);
          input.value = formatNumber(qtySent);
          updateRow(row);
        });
      });
    }
  }

  if (printButton) {
    printButton.addEventListener("click", () => {
      window.print();
    });
  }

  rows.forEach((row) => updateRow(row));
})();
