(() => {
  const splitNames = (value) =>
    String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item);

  const parseDecimal = (value) => {
    const raw = String(value ?? "").trim();
    if (!raw) {
      return 0;
    }
    const normalized = raw.replace(/\s+/g, "").replace(",", ".");
    const parsed = parseFloat(normalized);
    return Number.isNaN(parsed) ? 0 : parsed;
  };

  const escapeHtml = (value) =>
    String(value).replace(/[&<>"']/g, (char) => {
      const map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return map[char] || char;
    });

  const stripHtml = (value) =>
    String(value || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const extractMainText = (value) => {
    const raw = String(value || "");
    const match = raw.match(/ac-item-main[^>]*>(.*?)<\/div>/i);
    if (match && match[1]) {
      return stripHtml(match[1]);
    }
    return stripHtml(raw);
  };

  const normalizeKey = (value) => extractMainText(value).toLowerCase();

  const toColumnIndex = (value) => {
    if (typeof value === "number") {
      return value;
    }
    const text = String(value || "");
    if (/^\d+$/.test(text)) {
      return Number(text);
    }
    if (/^[A-Za-z]+\d+$/.test(text)) {
      const letters = text.replace(/\d+/g, "");
      return toColumnIndex(letters);
    }
    if (/^[A-Za-z]+$/.test(text)) {
      let index = 0;
      const upper = text.toUpperCase();
      for (let i = 0; i < upper.length; i += 1) {
        index = index * 26 + (upper.charCodeAt(i) - 64);
      }
      return index - 1;
    }
    return NaN;
  };

  const buildEmptyRows = (count) =>
    Array.from({ length: count }, () => ["", "", "", "", ""]);

  const showAlert = (alertBox, message) => {
    if (!alertBox) {
      return;
    }
    alertBox.textContent = message;
    alertBox.classList.remove("hidden");
  };

  const clearAlert = (alertBox) => {
    if (!alertBox) {
      return;
    }
    alertBox.textContent = "";
    alertBox.classList.add("hidden");
  };

  const tagState = new Map();

  const normalizeTag = (value) => String(value || "").trim();

  const renderTags = (container) => {
    const input = container.querySelector(".tag-entry");
    const hidden = container.querySelector('input[type="hidden"]');
    if (!input || !hidden) {
      return;
    }
    container.querySelectorAll(".tag-chip").forEach((chip) => chip.remove());
    const tags = tagState.get(container) || [];
    tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.dataset.value = tag;
      chip.innerHTML = `
        <span>${escapeHtml(tag)}</span>
        <button type="button" class="tag-remove" aria-label="Hapus ${escapeHtml(tag)}">x</button>
      `;
      container.insertBefore(chip, input);
    });
    hidden.value = tags.join(", ");
  };

  const addTags = (container, rawValue) => {
    const input = container.querySelector(".tag-entry");
    const parts = String(rawValue || "")
      .split(",")
      .map((part) => normalizeTag(part))
      .filter((part) => part);
    if (!parts.length) {
      return;
    }
    const current = tagState.get(container) || [];
    parts.forEach((part) => {
      const exists = current.some(
        (item) => item.toLowerCase() === part.toLowerCase()
      );
      if (!exists) {
        current.push(part);
      }
    });
    tagState.set(container, current);
    if (input) {
      input.value = "";
    }
    renderTags(container);
  };

  const removeTag = (container, value) => {
    const current = tagState.get(container) || [];
    const next = current.filter(
      (item) => item.toLowerCase() !== String(value || "").toLowerCase()
    );
    tagState.set(container, next);
    renderTags(container);
  };

  const setupTagInput = (container) => {
    const input = container.querySelector(".tag-entry");
    if (!input) {
      return;
    }
    tagState.set(container, []);
    renderTags(container);

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === ",") {
        event.preventDefault();
        addTags(container, input.value);
      } else if (event.key === "Backspace" && !input.value) {
        const current = tagState.get(container) || [];
        if (current.length) {
          current.pop();
          tagState.set(container, current);
          renderTags(container);
        }
      }
    });

    input.addEventListener("blur", () => {
      if (input.value.trim()) {
        addTags(container, input.value);
      }
    });

    container.addEventListener("click", (event) => {
      const removeButton = event.target.closest(".tag-remove");
      if (!removeButton) {
        return;
      }
      const chip = removeButton.closest(".tag-chip");
      if (!chip) {
        return;
      }
      removeTag(container, chip.dataset.value || "");
    });
  };

  const flushTagInputs = (containers) => {
    containers.forEach((container) => {
      const input = container.querySelector(".tag-entry");
      if (input && input.value.trim()) {
        addTags(container, input.value);
      }
    });
  };

  const focusCell = (instance, col, row) => {
    if (typeof instance.setSelectedCell === "function") {
      instance.setSelectedCell(col, row, true);
      return;
    }
    if (typeof instance.updateSelectionFromCoords === "function") {
      instance.updateSelectionFromCoords(col, row, col, row);
    }
  };

  const toCellName = (col, row) => {
    let index = Number(col) + 1;
    let letters = "";
    while (index > 0) {
      const rem = (index - 1) % 26;
      letters = String.fromCharCode(65 + rem) + letters;
      index = Math.floor((index - 1) / 26);
    }
    return `${letters}${Number(row) + 1}`;
  };

  const getCellValue = (instance, col, row) => {
    if (!instance) {
      return "";
    }
    if (typeof instance.getValueFromCoords === "function") {
      return instance.getValueFromCoords(col, row);
    }
    if (typeof instance.getValue === "function") {
      return instance.getValue(toCellName(col, row));
    }
    return "";
  };

  const setCellValue = (instance, col, row, value) => {
    if (!instance) {
      return;
    }
    if (typeof instance.setValueFromCoords === "function") {
      instance.setValueFromCoords(col, row, value, true);
      return;
    }
    if (typeof instance.setValue === "function") {
      instance.setValue(toCellName(col, row), value);
    }
  };

  const copyRemarksFromAbove = (instance, rowIndex) => {
    if (!instance || rowIndex <= 0) {
      return;
    }
    const current = String(getCellValue(instance, 4, rowIndex) || "").trim();
    if (current) {
      return;
    }
    const prev = String(getCellValue(instance, 4, rowIndex - 1) || "").trim();
    if (prev) {
      setCellValue(instance, 4, rowIndex, prev);
    }
  };

  const initBeritaAcaraForm = (options) => {
    const form = document.getElementById(options.formId || "");
    if (!form) {
      return;
    }

    const spreadsheetEl = document.getElementById(options.spreadsheetId || "");
    const spreadsheetFactory = window.jspreadsheet || window.jexcel;
    if (!spreadsheetEl || !spreadsheetFactory) {
      return;
    }

    const productsUrl = options.productsUrl || form.dataset.productsUrl;
    const purposesUrl = options.purposesUrl || form.dataset.purposesUrl;
    const submitUrl = options.submitUrl || form.action;
    const previewUrl = options.previewUrl || "/berita-acara/preview";

    const purposeSelect = document.getElementById("purpose_id");
    const purposeNameInput = document.getElementById("purpose_name");
    const outletIdInput = document.getElementById("outlet_id");
    const outletNameInput = document.getElementById("outlet_name_hidden");
    const itemsLoading = document.getElementById("items-loading");
    const alertBox = document.getElementById("form-alert");
    const tagInputs = Array.from(document.querySelectorAll(".tag-input"));
    const printButton = document.getElementById("print-button");
    const addRowButton = document.getElementById("add-row-button");
    const addRowCountInput = document.getElementById("add-row-count");

    const pdfModal = document.getElementById("pdf-modal");
    const pdfCanvas = document.getElementById("pdf-canvas");
    const pdfStatus = document.getElementById("pdf-status");
    const pdfDownload = document.getElementById("pdf-download");

    let productIndexByCode = new Map();
    let productIndexByName = new Map();
    let productList = [];
    let spreadsheet = null;
    let isNormalizingQty = false;
    let isSyncingRemarks = false;

    const loadPurposes = async () => {
      if (!purposeSelect) {
        return;
      }
      try {
        const resp = await fetch(purposesUrl);
        if (!resp.ok) {
          throw new Error("Request gagal");
        }
        const data = await resp.json();
        const purposes = Array.isArray(data) ? data : [];
        purposeSelect.innerHTML = "";
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Pilih purpose";
        purposeSelect.appendChild(placeholder);
        purposes.forEach((purpose) => {
          const option = document.createElement("option");
          option.value = String(purpose.id || "");
          option.textContent = String(purpose.name || "");
          purposeSelect.appendChild(option);
        });
        if (purposes.length === 1) {
          purposeSelect.value = String(purposes[0].id || "");
        }
        if (purposeNameInput) {
          const selected = purposeSelect.options[purposeSelect.selectedIndex];
          purposeNameInput.value = selected ? selected.textContent || "" : "";
        }
      } catch (error) {
        purposeSelect.innerHTML = "";
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Gagal memuat purpose";
        purposeSelect.appendChild(option);
      }
    };

    let productLoadingOverlay = document.getElementById("product-loading-overlay");
    let productLoadingMessage = document.getElementById("product-loading-message");
    let productLoadingBound = false;
    let productLoadingTimer = null;
    let productLoadingFadeTimer = null;

    const clearProductLoadingTimers = () => {
      if (productLoadingTimer) {
        window.clearTimeout(productLoadingTimer);
        productLoadingTimer = null;
      }
      if (productLoadingFadeTimer) {
        window.clearTimeout(productLoadingFadeTimer);
        productLoadingFadeTimer = null;
      }
    };

    const ensureProductLoadingOverlay = () => {
      if (productLoadingOverlay && productLoadingMessage) {
        if (!productLoadingBound) {
          const closeButton = productLoadingOverlay.querySelector(
            "#product-loading-close"
          );
          if (closeButton) {
            closeButton.addEventListener("click", () => {
              closeProductLoadingOverlay();
            });
          }
          productLoadingBound = true;
        }
        return true;
      }
      const existing = document.getElementById("product-loading-overlay");
      if (existing) {
        productLoadingOverlay = existing;
        productLoadingMessage = existing.querySelector("#product-loading-message");
        const closeButton = existing.querySelector("#product-loading-close");
        if (closeButton && !productLoadingBound) {
          closeButton.addEventListener("click", () => {
            closeProductLoadingOverlay();
          });
          productLoadingBound = true;
        }
        return Boolean(productLoadingOverlay && productLoadingMessage);
      }
      if (!document.body) {
        return false;
      }
      const overlay = document.createElement("div");
      overlay.id = "product-loading-overlay";
      overlay.className = "loading-overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.hidden = true;
      overlay.innerHTML = `
        <div class="loading-card">
          <div class="icon-circle loading-icon" aria-hidden="true">
            <div class="loading-spinner"></div>
          </div>
          <div class="content-text">
            <h3 class="loading-title">Memuat Daftar Produk</h3>
            <p class="loading-text" id="product-loading-message" aria-live="polite">
              Sedang Menarik Product Odoo dan ESB...
            </p>
          </div>
          <div class="loading-actions">
            <button type="button" class="btn-continue loading-close" id="product-loading-close">
              Tutup
            </button>
          </div>
          <div class="progress-bar-container">
            <div class="progress-bar-fill loading-progress"></div>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      productLoadingOverlay = overlay;
      productLoadingMessage = overlay.querySelector("#product-loading-message");
      const closeButton = overlay.querySelector("#product-loading-close");
      if (closeButton) {
        closeButton.addEventListener("click", () => {
          closeProductLoadingOverlay();
        });
        productLoadingBound = true;
      }
      return Boolean(productLoadingOverlay && productLoadingMessage);
    };

    const setProductLoadingMessage = (message) => {
      if (!ensureProductLoadingOverlay()) {
        return;
      }
      if (!productLoadingMessage) {
        return;
      }
      productLoadingMessage.textContent = message;
    };

    const showProductLoadingOverlay = (message) => {
      if (!ensureProductLoadingOverlay()) {
        return;
      }
      if (!productLoadingOverlay) {
        return;
      }
      clearProductLoadingTimers();
      productLoadingOverlay.classList.remove("fade-out", "ready");
      if (message) {
        setProductLoadingMessage(message);
      }
      if (productLoadingOverlay.hasAttribute("hidden")) {
        productLoadingOverlay.hidden = false;
      }
      productLoadingOverlay.hidden = false;
      document.body.classList.add("overlay-open");
      if (form) {
        form.classList.add("is-busy");
        form.setAttribute("aria-busy", "true");
        if ("inert" in form) {
          form.inert = true;
        }
      }
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    };

    const closeProductLoadingOverlay = () => {
      if (!ensureProductLoadingOverlay()) {
        return;
      }
      if (!productLoadingOverlay) {
        return;
      }
      clearProductLoadingTimers();
      productLoadingOverlay.classList.add("fade-out");
      productLoadingFadeTimer = window.setTimeout(() => {
        productLoadingOverlay.hidden = true;
        productLoadingOverlay.classList.remove("fade-out", "ready");
        document.body.classList.remove("overlay-open");
        if (form) {
          form.classList.remove("is-busy");
          form.removeAttribute("aria-busy");
          if ("inert" in form) {
            form.inert = false;
          }
        }
      }, 300);
    };

    const completeProductLoadingOverlay = (message) => {
      if (!ensureProductLoadingOverlay()) {
        return;
      }
      if (!productLoadingOverlay) {
        return;
      }
      if (message) {
        setProductLoadingMessage(message);
      }
      productLoadingOverlay.classList.add("ready");
      productLoadingTimer = window.setTimeout(() => {
        closeProductLoadingOverlay();
      }, 2000);
    };

    const buildProductSource = (products) =>
      products.map((product) => {
        const name = String(product.name || "");
        const code = String(product.default_code || "-");
        const uom = String(product.uom_name || "-");
        const key = String(product.default_code || "").trim() || name;
        return {
          id: key,
          name: name || key,
          title: `${code} - ${uom}`,
        };
      });

    const applyProducts = (products) => {
      productList = products;
      productIndexByCode = new Map(
        products
          .filter((product) => String(product.default_code || "").trim())
          .map((product) => [normalizeKey(String(product.default_code || "")), product])
      );
      productIndexByName = new Map(
        products.map((product) => [normalizeKey(String(product.name || "")), product])
      );
      if (
        spreadsheet &&
        spreadsheet.options &&
        Array.isArray(spreadsheet.options.columns) &&
        spreadsheet.options.columns[0]
      ) {
        spreadsheet.options.columns[0].source = buildProductSource(products);
        if (typeof spreadsheet.refresh === "function") {
          spreadsheet.refresh();
        }
      }
    };

    const fetchProductsWithTimeout = async (timeoutMs = 3000) => {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), timeoutMs);
      try {
        const resp = await fetch(productsUrl, { signal: controller.signal });
        if (!resp.ok) {
          throw new Error("Request gagal");
        }
        const data = await resp.json();
        const products = Array.isArray(data) ? data : [];
        return {
          products,
          cacheState:
            (resp.headers.get("X-Products-Cache") || "miss").toLowerCase(),
          completeness: (
            resp.headers.get("X-Products-Completeness") || "esb_only"
          ).toLowerCase(),
        };
      } finally {
        window.clearTimeout(timer);
      }
    };

    let productAutoRefreshScheduled = false;
    const loadProducts = async ({
      timeoutMs = 3000,
      allowRetry = true,
      useOverlay = false,
    } = {}) => {
      if (useOverlay) {
        showProductLoadingOverlay("Sedang Menarik Product Odoo dan ESB...");
      }
      if (itemsLoading) {
        itemsLoading.textContent = "Memuat daftar produk...";
      }
      try {
        const { products, cacheState, completeness } = await fetchProductsWithTimeout(
          timeoutMs
        );
        applyProducts(products);
        if (itemsLoading) {
          itemsLoading.textContent = `Produk tersedia: ${products.length} item (${completeness}, ${cacheState}).`;
        }
        if (useOverlay) {
          completeProductLoadingOverlay(
            `Produk berhasil dimuat (${products.length} item).`
          );
        }
        if (
          allowRetry &&
          !productAutoRefreshScheduled &&
          (completeness === "odoo_only" || completeness === "esb_only")
        ) {
          productAutoRefreshScheduled = true;
          window.setTimeout(() => {
            loadProducts({
              timeoutMs,
              allowRetry: false,
              useOverlay: false,
            }).finally(() => {
              productAutoRefreshScheduled = false;
            });
          }, 2500);
        }
        return products;
      } catch (error) {
        const isTimeout = error && error.name === "AbortError";
        if (itemsLoading) {
          itemsLoading.textContent = isTimeout
            ? "Load awal produk timeout (3 detik), memakai data sementara."
            : "Gagal memuat produk.";
        }
        if (useOverlay) {
          setProductLoadingMessage(
            isTimeout
              ? "Timeout 3 detik. Data akan disegarkan di background."
              : "Gagal memuat daftar produk."
          );
          closeProductLoadingOverlay();
        }
        if (allowRetry && !productAutoRefreshScheduled) {
          productAutoRefreshScheduled = true;
          window.setTimeout(() => {
            loadProducts({
              timeoutMs,
              allowRetry: false,
              useOverlay: false,
            }).finally(() => {
              productAutoRefreshScheduled = false;
            });
          }, 2500);
        }
        return productList;
      }
    };

    const resolveProduct = (value, cell) => {
      const key = normalizeKey(value);
      if (key && productIndexByCode.has(key)) {
        return productIndexByCode.get(key);
      }
      if (key && productIndexByName.has(key)) {
        return productIndexByName.get(key);
      }
      if (key) {
        const byCode = productIndexByCode.get(key.replace(/\s+/g, ""));
        if (byCode) {
          return byCode;
        }
      }
      if (/^\d+$/.test(key) && productList.length) {
        const idx = Number(key);
        if (idx >= 0 && idx < productList.length) {
          return productList[idx];
        }
      }
      const cellText = cell ? normalizeKey(cell.textContent) : "";
      if (cellText && productIndexByName.has(cellText)) {
        return productIndexByName.get(cellText);
      }
      return null;
    };

    const resolveGrid = (instance) => {
      if (instance && (instance.setValueFromCoords || instance.setValue)) {
        return instance;
      }
      if (spreadsheet && (spreadsheet.setValueFromCoords || spreadsheet.setValue)) {
        return spreadsheet;
      }
      return instance || spreadsheet || null;
    };

    const getGridData = (instance) => {
      const grid = resolveGrid(instance);
      if (!grid) {
        return [];
      }
      if (typeof grid.getData === "function") {
        return grid.getData();
      }
      if (typeof grid.getJson === "function") {
        return grid.getJson();
      }
      if (Array.isArray(grid.data)) {
        return grid.data;
      }
      return [];
    };

    const syncRow = (instance, rowIndex, rawValue, cell) => {
      const grid = resolveGrid(instance);
      if (!grid) {
        return;
      }
      let value = rawValue;
      if (value === undefined) {
        value = getCellValue(grid, 0, rowIndex);
      }
      const product = resolveProduct(value, cell);
      if (product) {
        const codeValue = String(product.default_code || "").trim();
        if (codeValue && normalizeKey(value) !== codeValue.toLowerCase()) {
          setCellValue(grid, 0, rowIndex, codeValue);
        }
        setCellValue(grid, 1, rowIndex, product.default_code || "");
        setCellValue(grid, 2, rowIndex, product.uom_name || "");
        copyRemarksFromAbove(grid, rowIndex);
        focusCell(grid, 3, rowIndex);
      } else {
        setCellValue(grid, 1, rowIndex, "");
        setCellValue(grid, 2, rowIndex, "");
      }
    };

    const jumpAfterRemarks = (instance, col, row) => {
      const grid = resolveGrid(instance);
      if (!grid) {
        return;
      }
      if (toColumnIndex(col) !== 4) {
        return;
      }
      const rowIndex = Number(row);
      if (Number.isNaN(rowIndex)) {
        return;
      }
      const data = getGridData(grid);
      if (
        rowIndex >= data.length - 1 &&
        grid &&
        typeof grid.insertRow === "function"
      ) {
        grid.insertRow();
      }
      const nextRow = Math.min(rowIndex + 1, getGridData(grid).length - 1);
      window.setTimeout(() => {
        focusCell(grid || instance, 0, nextRow);
      }, 0);
    };

    const initSpreadsheet = () => {
      spreadsheet = spreadsheetFactory(spreadsheetEl, {
        data: buildEmptyRows(6),
        minDimensions: [5, 6],
        columns: [
          {
            type: "dropdown",
            title: "Nama Item",
            width: 300,
            source: [],
            autocomplete: true,
          },
          { type: "text", title: "Kode Item", width: 140, readOnly: true },
          { type: "text", title: "UoM", width: 120, readOnly: true },
          {
            type: "numeric",
            title: "Qty",
            width: 100,
            decimal: ",",
          },
          {
            type: "text",
            title: "Remarks",
            width: 220,
          },
        ],
        allowInsertRow: true,
        allowDeleteRow: true,
        allowInsertColumn: false,
        allowDeleteColumn: false,
        tableOverflow: true,
        tableHeight: "320px",
        onbeforechange: (instance, cell, x, y, value) => {
          const colIndex = toColumnIndex(x);
          if (colIndex === 3) {
            const raw = String(value ?? "").trim();
            if (!raw) {
              return value;
            }
            const normalized = raw.replace(/\s+/g, "").replace(/\./g, ",");
            if (!/^\d+(,\d+)?$/.test(normalized)) {
              showAlert(alertBox, "Qty hanya boleh angka dengan desimal koma.");
              return false;
            }
            if (normalized !== raw) {
              return normalized;
            }
            return value;
          }
          if (colIndex === 0) {
            window.setTimeout(() => {
              syncRow(resolveGrid(instance), Number(y), value, cell);
            }, 0);
          }
          return value;
        },
        updateTable: (instance, cell, x, y, value) => {
          const colIndex = toColumnIndex(x);
          if (colIndex === 0) {
            const product = resolveProduct(value, cell);
            if (product) {
              cell.textContent = product.name || "";
            } else {
              cell.textContent = stripHtml(value || "");
            }
          }
        },
        onchange: (instance, cell, x, y, value) => {
          const colIndex = toColumnIndex(x);
          if (colIndex === 0) {
            syncRow(resolveGrid(instance), Number(y), value, cell);
          }
          if (colIndex === 3) {
            const grid = resolveGrid(instance);
            const rowIndex = Number(y);
            if (!isNormalizingQty) {
              const raw = String(value ?? "").trim();
              if (raw) {
                const normalized = raw.replace(/\s+/g, "").replace(/\./g, ",");
                if (!/^\d+(,\d+)?$/.test(normalized)) {
                  showAlert(alertBox, "Qty hanya boleh angka dengan desimal koma.");
                  setCellValue(grid, 3, rowIndex, "");
                } else if (normalized !== raw) {
                  isNormalizingQty = true;
                  setCellValue(grid, 3, rowIndex, normalized);
                  isNormalizingQty = false;
                }
              }
            }
            window.setTimeout(() => {
              focusCell(grid || instance, 4, rowIndex);
            }, 0);
          }
          if (colIndex === 4 && !isSyncingRemarks) {
            const grid = resolveGrid(instance);
            const data = getGridData(grid);
            const rowIndex = Number(y);
            const remarksValue = String(value ?? "");
            if (
              rowIndex >= data.length - 1 &&
              grid &&
              typeof grid.insertRow === "function"
            ) {
              grid.insertRow();
            }
            const nextRow = Math.min(
              rowIndex + 1,
              getGridData(grid).length - 1
            );
            isSyncingRemarks = true;
            setCellValue(grid, 4, nextRow, remarksValue);
            isSyncingRemarks = false;
            window.setTimeout(() => {
              focusCell(grid || instance, 0, nextRow);
            }, 0);
          }
        },
        oneditionend: (instance, cell, x, y) => {
          jumpAfterRemarks(instance, x, y);
        },
        onafterchanges: (instance, changes) => {
          if (!Array.isArray(changes)) {
            return;
          }
          changes.forEach((change) => {
            const col = toColumnIndex(change[0]);
            const row = Number(change[1]);
            if (col === 0) {
              syncRow(resolveGrid(instance), row, change[2]);
            }
          });
        },
      });
    };

    const buildPayload = () => {
      flushTagInputs(tagInputs);
      const noForm = form.querySelector("#no_form");
      const dibuat = form.querySelector("#dibuat_oleh");
      const disetujui = form.querySelector("#disetujui_oleh");
      const mengetahui = form.querySelector("#mengetahui_oleh");

      if (!spreadsheet) {
        showAlert(alertBox, "Spreadsheet belum siap.");
        return null;
      }

      const rawData = getGridData(spreadsheet);
      const items = rawData
        .map((row) => {
          const rawKey = String(row[0] || "").trim();
          const product =
            productIndexByCode.get(rawKey.toLowerCase()) ||
            productIndexByName.get(rawKey.toLowerCase()) ||
            productIndexByName.get(normalizeKey(rawKey));
          const name = product ? String(product.name || "") : rawKey;
          const code = String(row[1] || product?.default_code || "").trim();
          const uom = String(row[2] || product?.uom_name || "").trim();
          const qty = parseDecimal(row[3]);
          const remarks = String(row[4] || "").trim();
          return {
            product_id: product ? product.id || "" : "",
            nama_item: name,
            kode_item: code,
            uom,
            qty,
            remarks,
          };
        })
        .filter((item) => item.nama_item || item.qty > 0);

      const errors = [];
      if (!noForm || !noForm.value.trim()) {
        errors.push("No Form");
      }
      if (!purposeSelect || !purposeSelect.value) {
        errors.push("Purpose");
      }
      if (!outletIdInput || !outletIdInput.value) {
        errors.push("Outlet");
      }
      if (!dibuat || splitNames(dibuat.value).length === 0) {
        errors.push("Dibuat Oleh");
      }
      if (!disetujui || splitNames(disetujui.value).length === 0) {
        errors.push("Disetujui Oleh");
      }
      if (!mengetahui || splitNames(mengetahui.value).length === 0) {
        errors.push("Mengetahui Oleh");
      }
      if (items.length === 0) {
        errors.push("Minimal 1 item");
      } else {
        const validItems = items.every((item) => item.nama_item && item.qty > 0);
        if (!validItems) {
          errors.push("Lengkapi Nama Item dan Qty di semua baris");
        }
      }

      if (errors.length > 0) {
        showAlert(alertBox, `Lengkapi dulu: ${errors.join(", ")}`);
        return null;
      }

      const header = {
        no_form: noForm.value.trim(),
        purpose_id: purposeSelect.value,
        purpose_name: purposeNameInput ? purposeNameInput.value : "",
        outlet_id: outletIdInput ? outletIdInput.value : "",
        outlet_name: outletNameInput ? outletNameInput.value : "",
        dibuat_oleh: splitNames(dibuat ? dibuat.value : ""),
        disetujui_oleh: splitNames(disetujui ? disetujui.value : ""),
        mengetahui_oleh: splitNames(mengetahui ? mengetahui.value : ""),
      };

      return { header, items };
    };

    const submitPayload = async () => {
      clearAlert(alertBox);
      const payload = buildPayload();
      if (!payload) {
        return;
      }
      try {
        const resp = await fetch(submitUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(payload),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          throw new Error(data.error || "Gagal menyimpan data.");
        }
        if (data.redirect_url) {
          window.location.href = data.redirect_url;
          return;
        }
        window.location.href = "/berita-acara?status=success&message=Data%20berhasil%20disimpan.";
      } catch (error) {
        showAlert(alertBox, error.message || "Gagal menyimpan data.");
      }
    };

    let currentPdfUrl = "";

    const setPdfStatus = (message) => {
      if (!pdfStatus) {
        return;
      }
      pdfStatus.textContent = message;
      pdfStatus.style.display = "block";
    };

    const hidePdfStatus = () => {
      if (!pdfStatus) {
        return;
      }
      pdfStatus.style.display = "none";
    };

    const clearPdfCanvas = () => {
      if (!pdfCanvas) {
        return;
      }
      const context = pdfCanvas.getContext("2d");
      context.clearRect(0, 0, pdfCanvas.width, pdfCanvas.height);
      pdfCanvas.width = 1;
      pdfCanvas.height = 1;
    };

    const openPdfModal = () => {
      if (!pdfModal) {
        return;
      }
      pdfModal.classList.remove("hidden");
      pdfModal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
    };

    const closePdfModal = () => {
      if (!pdfModal) {
        return;
      }
      pdfModal.classList.add("hidden");
      pdfModal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("modal-open");
      clearPdfCanvas();
      if (pdfDownload) {
        pdfDownload.removeAttribute("href");
        pdfDownload.removeAttribute("download");
      }
      if (currentPdfUrl) {
        URL.revokeObjectURL(currentPdfUrl);
        currentPdfUrl = "";
      }
    };

    const renderPdfPreview = async (base64) => {
      const pdfjsLib = window["pdfjs-dist/build/pdf"];
      if (!pdfjsLib) {
        setPdfStatus("Gagal memuat PDF preview.");
        return;
      }
      pdfjsLib.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js";

      const raw = atob(base64);
      const uint8Array = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) {
        uint8Array[i] = raw.charCodeAt(i);
      }
      const loadingTask = pdfjsLib.getDocument({ data: uint8Array });
      const pdf = await loadingTask.promise;
      const page = await pdf.getPage(1);

      const canvas = pdfCanvas;
      if (!canvas) {
        return;
      }
      const context = canvas.getContext("2d");
      const containerWidth = canvas.parentElement
        ? canvas.parentElement.clientWidth - 24
        : 800;
      const viewport = page.getViewport({ scale: 1 });
      const scale = Math.min(1.4, Math.max(0.6, containerWidth / viewport.width));
      const scaledViewport = page.getViewport({ scale });
      canvas.height = scaledViewport.height;
      canvas.width = scaledViewport.width;
      await page.render({ canvasContext: context, viewport: scaledViewport }).promise;
    };

    const previewPayload = async () => {
      clearAlert(alertBox);
      const payload = buildPayload();
      if (!payload) {
        return;
      }
      openPdfModal();
      setPdfStatus("Memuat pratinjau PDF...");
      clearPdfCanvas();
      try {
        const response = await fetch(previewUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          if (response.status === 401) {
            const nextUrl = encodeURIComponent(
              `${window.location.pathname}${window.location.search}`
            );
            window.location.href = `/login?next=${nextUrl}`;
            return;
          }
          const responsePayload = await response.json().catch(() => ({}));
          throw new Error(responsePayload.error || "Gagal membuat preview PDF.");
        }
        const payloadData = await response.json();
        if (!payloadData || !payloadData.pdf_base64) {
          throw new Error("Data preview PDF tidak tersedia.");
        }
        const base64 = payloadData.pdf_base64;
        const fileName = payloadData.pdf_file_name || "BeritaAcara.pdf";

        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) {
          bytes[i] = binary.charCodeAt(i);
        }
        const blob = new Blob([bytes], { type: "application/pdf" });
        if (currentPdfUrl) {
          URL.revokeObjectURL(currentPdfUrl);
        }
        currentPdfUrl = URL.createObjectURL(blob);
        if (pdfDownload) {
          pdfDownload.href = currentPdfUrl;
          pdfDownload.download = fileName;
        }
        await renderPdfPreview(base64);
        hidePdfStatus();
      } catch (error) {
        setPdfStatus(error.message || "Gagal memuat pratinjau PDF.");
      }
    };

    if (purposeSelect) {
      purposeSelect.addEventListener("change", () => {
        if (purposeNameInput) {
          const selected = purposeSelect.options[purposeSelect.selectedIndex];
          purposeNameInput.value = selected ? selected.textContent || "" : "";
        }
      });
    }

    tagInputs.forEach((container) => setupTagInput(container));

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitPayload();
    });

    if (printButton) {
      printButton.addEventListener("click", () => {
        previewPayload();
      });
    }

    if (addRowButton) {
      addRowButton.addEventListener("click", () => {
        if (spreadsheet && typeof spreadsheet.insertRow === "function") {
          const grid = resolveGrid(spreadsheet);
          const data = getGridData(grid);
          const startIndex = data.length;
          const prevRow = Math.max(startIndex - 1, 0);
          const prevRemarks = getCellValue(grid, 4, prevRow);
          let count = parseInt(addRowCountInput?.value || "1", 10);
          if (Number.isNaN(count) || count <= 0) {
            count = 1;
          }
          count = Math.min(Math.max(count, 1), 200);
          const beforeLen = getGridData(grid).length;
          let inserted = false;
          if (grid.insertRow && grid.insertRow.length >= 2) {
            try {
              grid.insertRow(startIndex, count);
              inserted = true;
            } catch (error) {
              inserted = false;
            }
          }
          const afterAttemptLen = getGridData(grid).length;
          let needed = count - Math.max(afterAttemptLen - beforeLen, 0);
          if (!inserted || needed > 0) {
            while (needed > 0) {
              grid.insertRow();
              needed -= 1;
            }
          }
          const lastRow = getGridData(grid).length - 1;
          for (let i = startIndex; i <= lastRow; i += 1) {
            setCellValue(grid, 4, i, prevRemarks || "");
          }
          focusCell(grid, 0, startIndex);
        }
      });
    }

    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) {
        closePdfModal();
      }
    });

    loadPurposes();
    initSpreadsheet();
    loadProducts({
      timeoutMs: 3000,
      allowRetry: true,
      useOverlay: false,
    });
  };

  window.initBeritaAcaraForm = initBeritaAcaraForm;
})();
