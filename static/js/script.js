(() => {
  const form = document.getElementById("mutasi-form");
  if (!form) {
    return;
  }

  const outletNameInput = document.getElementById("outlet_pengirim_name");
  const outletIdInput = document.getElementById("outlet_pengirim");
  const outletReceiverNameInput = document.getElementById("outlet_penerima_name");
  const outletReceiverIdInput = document.getElementById("outlet_penerima");
  const itemsBody = document.getElementById("items-body");
  const itemsInput = document.getElementById("items-json");
  const alertBox = document.getElementById("form-alert");
  const resetButton = document.getElementById("reset-button");
  const itemsLoading = document.getElementById("items-loading");
  const rowTemplate = document.getElementById("item-row-template");
  const tagInputs = Array.from(document.querySelectorAll(".tag-input"));
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file_upload");
  const uploadPreview = document.getElementById("upload-preview");
  const printButton = document.getElementById("print-button");
  const pdfModal = document.getElementById("pdf-modal");
  const pdfCanvas = document.getElementById("pdf-canvas");
  const pdfStatus = document.getElementById("pdf-status");
  const pdfDownload = document.getElementById("pdf-download");
  const maxUploadMb = parseInt(form.dataset.maxUpload || "200", 10);

  const outlets = Array.isArray(window.OUTLETS) ? window.OUTLETS : [];
  const outletContexts = [];
  const productCache = new Map();
  let currentProducts = [];
  const isOutletLocked = outletNameInput?.dataset.locked === "true";

  const splitNames = (value) =>
    value
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
    const value = String(rawValue || "");
    const parts = value
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

  const flushTagInputs = () => {
    tagInputs.forEach((container) => {
      const input = container.querySelector(".tag-entry");
      if (input && input.value.trim()) {
        addTags(container, input.value);
      }
    });
  };

  const resetTagInputs = () => {
    tagInputs.forEach((container) => {
      tagState.set(container, []);
      const input = container.querySelector(".tag-entry");
      if (input) {
        input.value = "";
      }
      renderTags(container);
    });
  };

  const updateUploadPreview = (file) => {
    if (!uploadPreview) {
      return;
    }
    uploadPreview.innerHTML = "";
    if (!file) {
      return;
    }
    if (file.type && file.type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = "Preview file";
      uploadPreview.appendChild(img);
    } else {
      const label = document.createElement("div");
      label.textContent = `File terpilih: ${file.name}`;
      uploadPreview.appendChild(label);
    }
  };

  const setupDropZone = () => {
    if (!dropZone || !fileInput) {
      return;
    }
    dropZone.addEventListener("click", () => {
      fileInput.click();
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.classList.remove("dragover");
      });
    });

    dropZone.addEventListener("drop", (event) => {
      const files = event.dataTransfer?.files;
      if (!files || !files.length) {
        return;
      }
      const file = files[0];
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      fileInput.files = dataTransfer.files;
      updateUploadPreview(file);
    });

    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      updateUploadPreview(file);
    });
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

  const showAlert = (message) => {
    if (!alertBox) {
      return;
    }
    alertBox.textContent = message;
    alertBox.classList.remove("hidden");
  };

  const clearAlert = () => {
    if (!alertBox) {
      return;
    }
    alertBox.textContent = "";
    alertBox.classList.add("hidden");
  };

  const setActiveSuggestion = (items, activeIndex) => {
    items.forEach((item, index) => {
      item.classList.toggle("active", index === activeIndex);
    });
  };

  const hideList = (list, input) => {
    if (!list) {
      return;
    }
    list.hidden = true;
    list.innerHTML = "";
    if (input) {
      input.dataset.acIndex = "-1";
    }
  };

  const renderOutletSuggestions = (ctx, rawQuery) => {
    if (!ctx || !ctx.list) {
      return;
    }
    const query = String(rawQuery || "").trim().toLowerCase();
    const matches = outlets
      .filter((outlet) => {
        const name = String(outlet?.name || "").toLowerCase();
        return !query || name.includes(query);
      })
      .slice(0, 50);

    if (!matches.length) {
      hideList(ctx.list, ctx.input);
      return;
    }

    ctx.list.innerHTML = "";
    matches.forEach((outlet) => {
      const item = document.createElement("div");
      item.className = "ac-item";
      item.dataset.id = String(outlet.id ?? "");
      item.dataset.name = String(outlet.name ?? "");
      item.innerHTML = `<div class="ac-item-main">${escapeHtml(outlet.name ?? "")}</div>`;
      ctx.list.appendChild(item);
    });
    ctx.list.hidden = false;
    ctx.input.dataset.acIndex = "-1";
  };

  const selectOutlet = (ctx, outletId, outletName) => {
    if (!ctx || !ctx.input || !ctx.hidden) {
      return;
    }
    ctx.input.value = outletName || "";
    ctx.hidden.value = outletId || "";
    hideList(ctx.list, ctx.input);
  };

  const handleOutletKeydown = (event, ctx) => {
    if (!ctx || !ctx.list || ctx.list.hidden) {
      return;
    }
    const items = Array.from(ctx.list.querySelectorAll(".ac-item"));
    if (!items.length) {
      return;
    }
    let idx = parseInt(ctx.input.dataset.acIndex || "-1", 10);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      idx = (idx + 1 + items.length) % items.length;
      ctx.input.dataset.acIndex = String(idx);
      setActiveSuggestion(items, idx);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      idx = (idx - 1 + items.length) % items.length;
      ctx.input.dataset.acIndex = String(idx);
      setActiveSuggestion(items, idx);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const selected = items[Math.max(idx, 0)];
      if (selected) {
        selectOutlet(ctx, selected.dataset.id, selected.dataset.name);
        if (ctx.onSelect) {
          ctx.onSelect(selected.dataset.id);
        }
      }
    } else if (event.key === "Escape") {
      hideList(ctx.list, ctx.input);
    }
  };

  const setupOutletAutocomplete = (inputEl, hiddenEl, onSelect) => {
    if (!inputEl || !hiddenEl) {
      return null;
    }
    const wrap = inputEl.closest(".ac-wrap");
    const list = wrap ? wrap.querySelector(".ac-list") : null;
    if (!list) {
      return null;
    }
    const ctx = { input: inputEl, hidden: hiddenEl, list, onSelect };
    outletContexts.push(ctx);

    inputEl.addEventListener("input", () => {
      hiddenEl.value = "";
      renderOutletSuggestions(ctx, inputEl.value);
    });

    inputEl.addEventListener("focus", () => {
      renderOutletSuggestions(ctx, inputEl.value);
    });

    inputEl.addEventListener("keydown", (event) => {
      handleOutletKeydown(event, ctx);
    });

    inputEl.addEventListener("blur", () => {
      window.setTimeout(() => {
        hideList(list, inputEl);
      }, 120);
    });

    list.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });

    list.addEventListener("click", (event) => {
      const item = event.target.closest(".ac-item");
      if (!item) {
        return;
      }
      selectOutlet(ctx, item.dataset.id, item.dataset.name);
      if (onSelect) {
        onSelect(item.dataset.id);
      }
    });

    return ctx;
  };

  const clearRow = (row) => {
    const nameInput = row.querySelector(".product-input");
    const kodeInput = row.querySelector(".item-kode");
    const uomInput = row.querySelector(".item-uom");
    const qtyInput = row.querySelector(".item-qty");
    if (nameInput) {
      nameInput.value = "";
      nameInput.dataset.acIndex = "-1";
    }
    if (kodeInput) {
      kodeInput.value = "";
    }
    if (uomInput) {
      uomInput.value = "";
    }
    if (qtyInput) {
      qtyInput.value = "0";
    }
    row.dataset.productName = "";
    row.dataset.harga = "0";
    hideProductList(row);
  };

  const applyProductSelection = (row, data) => {
    const nameInput = row.querySelector(".product-input");
    const kodeInput = row.querySelector(".item-kode");
    const uomInput = row.querySelector(".item-uom");
    if (!nameInput || !kodeInput || !uomInput) {
      return;
    }
    nameInput.value = data.name || "";
    kodeInput.value = data.code || "";
    uomInput.value = data.uom || "";
    row.dataset.productName = data.name || "";
    row.dataset.harga = data.harga || "0";
    hideProductList(row);

    const qtyInput = row.querySelector(".item-qty");
    if (qtyInput) {
      qtyInput.focus();
      qtyInput.select();
    }
  };

  const hideProductList = (row) => {
    const list = row.querySelector(".ac-list");
    const input = row.querySelector(".product-input");
    hideList(list, input);
  };

  const renderProductSuggestions = (row, rawQuery) => {
    const input = row.querySelector(".product-input");
    const list = row.querySelector(".ac-list");
    if (!input || !list) {
      return;
    }
    const query = String(rawQuery || "").trim().toLowerCase();
    if (!query) {
      hideProductList(row);
      return;
    }
    const matches = currentProducts
      .filter((product) => {
        const name = String(product?.name || "").toLowerCase();
        const code = String(product?.default_code || "").toLowerCase();
        return name.includes(query) || code.includes(query);
      })
      .slice(0, 40);

    if (!matches.length) {
      hideProductList(row);
      return;
    }

    list.innerHTML = "";
    matches.forEach((product) => {
      const item = document.createElement("div");
      item.className = "ac-item";
      item.dataset.name = String(product.name || "");
      item.dataset.code = String(product.default_code || "");
      item.dataset.uom = String(product.uom_name || "");
      item.dataset.harga = String(product.harga || "0");
      const codeText = product.default_code ? product.default_code : "-";
      const uomText = product.uom_name ? product.uom_name : "-";
      item.innerHTML = `
        <div class="ac-item-main">${escapeHtml(product.name || "(tanpa nama)")}</div>
        <div class="ac-item-sub">${escapeHtml(codeText)} - ${escapeHtml(uomText)}</div>
      `;
      list.appendChild(item);
    });
    list.hidden = false;
    input.dataset.acIndex = "-1";
  };

  const handleProductKeydown = (event, row) => {
    const input = row.querySelector(".product-input");
    const list = row.querySelector(".ac-list");
    if (!input || !list || list.hidden) {
      return;
    }
    const items = Array.from(list.querySelectorAll(".ac-item"));
    if (!items.length) {
      return;
    }
    let idx = parseInt(input.dataset.acIndex || "-1", 10);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      idx = (idx + 1 + items.length) % items.length;
      input.dataset.acIndex = String(idx);
      setActiveSuggestion(items, idx);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      idx = (idx - 1 + items.length) % items.length;
      input.dataset.acIndex = String(idx);
      setActiveSuggestion(items, idx);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const selected = items[Math.max(idx, 0)];
      if (selected) {
        applyProductSelection(row, {
          name: selected.dataset.name || "",
          code: selected.dataset.code || "",
          uom: selected.dataset.uom || "",
          harga: selected.dataset.harga || "0",
        });
      }
    } else if (event.key === "Escape") {
      hideProductList(row);
    }
  };

  const attachProductAutocomplete = (row) => {
    const input = row.querySelector(".product-input");
    const list = row.querySelector(".ac-list");
    if (!input || !list) {
      return;
    }

    input.addEventListener("input", () => {
      row.dataset.productName = "";
      row.dataset.harga = "0";
      const kodeInput = row.querySelector(".item-kode");
      const uomInput = row.querySelector(".item-uom");
      if (kodeInput) {
        kodeInput.value = "";
      }
      if (uomInput) {
        uomInput.value = "";
      }
      renderProductSuggestions(row, input.value);
    });

    input.addEventListener("focus", () => {
      renderProductSuggestions(row, input.value);
    });

    input.addEventListener("keydown", (event) => {
      handleProductKeydown(event, row);
    });

    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        hideProductList(row);
      }, 120);
    });

    list.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });

    list.addEventListener("click", (event) => {
      const item = event.target.closest(".ac-item");
      if (!item) {
        return;
      }
      applyProductSelection(row, {
        name: item.dataset.name || "",
        code: item.dataset.code || "",
        uom: item.dataset.uom || "",
        harga: item.dataset.harga || "0",
      });
    });
  };

  const updateRowActions = () => {
    const rows = itemsBody.querySelectorAll(".item-row");
    rows.forEach((row, index) => {
      const addButton = row.querySelector(".add-row-inline");
      if (addButton) {
        addButton.style.display = index === rows.length - 1 ? "inline-flex" : "none";
      }
    });
  };

  const createRow = () => {
    if (!rowTemplate) {
      return null;
    }
    const fragment = rowTemplate.content.cloneNode(true);
    const row = fragment.querySelector(".item-row");
    if (!row) {
      return null;
    }
    itemsBody.appendChild(fragment);
    attachProductAutocomplete(row);
    clearRow(row);
    updateRowActions();
    return row;
  };

  const removeRow = (row) => {
    const rows = itemsBody.querySelectorAll(".item-row");
    if (rows.length <= 1) {
      clearRow(row);
      updateRowActions();
      return;
    }
    row.remove();
    updateRowActions();
  };

  const loadProducts = async (outletId) => {
    if (!outletId) {
      currentProducts = [];
      const rows = itemsBody.querySelectorAll(".item-row");
      rows.forEach(clearRow);
      if (itemsLoading) {
        itemsLoading.textContent = "Pilih outlet pengirim untuk memuat produk.";
      }
      return;
    }
    if (productCache.has(outletId)) {
      currentProducts = productCache.get(outletId);
      if (itemsLoading) {
        itemsLoading.textContent = `Produk tersedia: ${currentProducts.length} item.`;
      }
      const rows = itemsBody.querySelectorAll(".item-row");
      rows.forEach(clearRow);
      return;
    }
    if (itemsLoading) {
      itemsLoading.textContent = "Memuat daftar produk...";
    }
    try {
      const response = await fetch(
        `/api/products?outlet_id=${encodeURIComponent(outletId)}`
      );
      if (!response.ok) {
        throw new Error("Request gagal");
      }
      const data = await response.json();
      currentProducts = Array.isArray(data) ? data : [];
      productCache.set(outletId, currentProducts);
      if (itemsLoading) {
        itemsLoading.textContent = `Produk tersedia: ${currentProducts.length} item.`;
      }
      const rows = itemsBody.querySelectorAll(".item-row");
      rows.forEach(clearRow);
    } catch (error) {
      currentProducts = [];
      if (itemsLoading) {
        itemsLoading.textContent = "Gagal memuat produk.";
      }
    }
  };

  const collectItems = () => {
    const rows = itemsBody.querySelectorAll(".item-row");
    const items = [];
    rows.forEach((row) => {
      const qtyInput = row.querySelector(".item-qty");
      const kodeInput = row.querySelector(".item-kode");
      const uomInput = row.querySelector(".item-uom");
      const qty = qtyInput ? parseDecimal(qtyInput.value) : 0;
      items.push({
        product_name: row.dataset.productName || "",
        kode_item: kodeInput ? kodeInput.value || "" : "",
        uom: uomInput ? uomInput.value || "" : "",
        qty: Number.isNaN(qty) ? 0 : qty,
        harga: parseFloat(row.dataset.harga || "0") || 0,
      });
    });
    return items;
  };

  const validateForm = (items) => {
    const errors = [];
    const noForm = form.querySelector("#no_form");
    const dibuat = form.querySelector("#dibuat_oleh");
    const diterima = form.querySelector("#diterima_oleh");

    if (!noForm || !noForm.value.trim()) {
      errors.push("No Form");
    }
    if (!outletIdInput || !outletIdInput.value) {
      errors.push("Outlet Pengirim");
    }
    if (!outletReceiverIdInput || !outletReceiverIdInput.value) {
      errors.push("Outlet Penerima");
    }
    if (
      outletIdInput &&
      outletReceiverIdInput &&
      outletIdInput.value &&
      outletIdInput.value === outletReceiverIdInput.value
    ) {
      errors.push("Outlet pengirim dan penerima tidak boleh sama.");
    }
    if (!dibuat || splitNames(dibuat.value).length === 0) {
      errors.push("Dibuat Oleh");
    }
    if (!diterima || splitNames(diterima.value).length === 0) {
      errors.push("Diterima Oleh");
    }

    const nonEmptyItems = items.filter(
      (item) => item.product_name || (item.qty || 0) > 0
    );
    if (nonEmptyItems.length === 0) {
      errors.push("Minimal 1 item");
    } else {
      const validItems = nonEmptyItems.every(
        (item) => item.product_name && (item.qty || 0) > 0
      );
      if (!validItems) {
        errors.push("Lengkapi Nama Item dan Kuantiti di semua baris");
      }
    }

    if (fileInput && fileInput.files && fileInput.files[0]) {
      const fileSizeMb = fileInput.files[0].size / (1024 * 1024);
      if (fileSizeMb > maxUploadMb) {
        errors.push(`Ukuran file melebihi ${maxUploadMb}MB`);
      }
    }

    return errors;
  };

  const buildFormData = () => {
    flushTagInputs();
    const items = collectItems();
    itemsInput.value = JSON.stringify(items);
    const errors = validateForm(items);
    if (errors.length > 0) {
      if (errors.some((error) => error.includes("sama."))) {
        showAlert("Outlet pengirim dan penerima tidak boleh sama.");
      } else {
        showAlert(`Lengkapi dulu: ${errors.join(", ")}`);
      }
      return null;
    }
    const formData = new FormData(form);
    formData.set("items_json", JSON.stringify(items));
    return formData;
  };

  form.addEventListener("submit", (event) => {
    clearAlert();
    flushTagInputs();
    const items = collectItems();
    itemsInput.value = JSON.stringify(items);
    const errors = validateForm(items);
    if (errors.length > 0) {
      event.preventDefault();
      if (errors.some((error) => error.includes("sama."))) {
        showAlert("Outlet pengirim dan penerima tidak boleh sama.");
        return;
      }
      showAlert(`Lengkapi dulu: ${errors.join(", ")}`);
    }
  });

  if (printButton) {
    printButton.addEventListener("click", async () => {
      clearAlert();
      const formData = buildFormData();
      if (!formData) {
        return;
      }
      openPdfModal();
      setPdfStatus("Memuat pratinjau PDF...");
      clearPdfCanvas();
      try {
        const response = await fetch("/preview", {
          method: "POST",
          body: formData,
        });
        if (!response.ok) {
          if (response.status === 401) {
            window.location.href = "/login?next=/";
            return;
          }
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.error || "Gagal membuat preview PDF.");
        }
        const payload = await response.json();
        if (!payload || !payload.pdf_base64) {
          throw new Error("Data preview PDF tidak tersedia.");
        }
        const base64 = payload.pdf_base64;
        const fileName = payload.pdf_file_name || "Form-Mutasi.pdf";

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
    });
  }

  if (resetButton) {
    resetButton.addEventListener("click", () => {
      window.setTimeout(() => {
        clearAlert();
        if (outletNameInput && !isOutletLocked) {
          outletNameInput.value = "";
        }
        if (outletIdInput && !isOutletLocked) {
          outletIdInput.value = "";
        }
        if (outletReceiverNameInput) {
          outletReceiverNameInput.value = "";
        }
        if (outletReceiverIdInput) {
          outletReceiverIdInput.value = "";
        }
        hideAllOutletLists();
        resetTagInputs();
        if (fileInput) {
          fileInput.value = "";
        }
        if (uploadPreview) {
          uploadPreview.innerHTML = "";
        }
        itemsBody.innerHTML = "";
        createRow();
        if (isOutletLocked && outletIdInput && outletIdInput.value) {
          loadProducts(outletIdInput.value);
        } else if (itemsLoading) {
          itemsLoading.textContent = "Pilih outlet pengirim untuk memuat produk.";
        }
      }, 0);
    });
  }

  const hideAllOutletLists = () => {
    outletContexts.forEach((ctx) => {
      hideList(ctx.list, ctx.input);
    });
  };

  const hideAllProductLists = () => {
    const lists = itemsBody.querySelectorAll(".product-wrap .ac-list");
    lists.forEach((list) => {
      list.hidden = true;
      list.innerHTML = "";
    });
  };

  if (itemsBody) {
    itemsBody.addEventListener("click", (event) => {
      const addButton = event.target.closest(".add-row-inline");
      if (addButton) {
        createRow();
        return;
      }
      const removeButton = event.target.closest(".remove-row");
      if (removeButton) {
        const row = removeButton.closest(".item-row");
        if (row) {
          removeRow(row);
        }
      }
    });
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".outlet-wrap")) {
      hideAllOutletLists();
    }
    if (!event.target.closest(".product-wrap")) {
      hideAllProductLists();
    }
    if (event.target.closest("[data-close]")) {
      closePdfModal();
    }
  });

  let pengirimCtx = null;
  if (!isOutletLocked) {
    pengirimCtx = setupOutletAutocomplete(outletNameInput, outletIdInput, (id) => {
      loadProducts(id);
    });
  }
  setupOutletAutocomplete(outletReceiverNameInput, outletReceiverIdInput, null);

  tagInputs.forEach((container) => setupTagInput(container));
  setupDropZone();

  if (pengirimCtx && outletNameInput) {
    outletNameInput.addEventListener("change", () => {
      if (!outletIdInput.value) {
        currentProducts = [];
        if (itemsLoading) {
          itemsLoading.textContent = "Pilih outlet pengirim untuk memuat produk.";
        }
      }
    });
  }

  createRow();

  if (isOutletLocked && outletNameInput && outletIdInput) {
    if (!outletIdInput.value && outletNameInput.value) {
      const match = outlets.find(
        (outlet) =>
          String(outlet?.name || "").toLowerCase() ===
          outletNameInput.value.trim().toLowerCase()
      );
      if (match) {
        outletIdInput.value = String(match.id ?? "");
      }
    }
    if (outletIdInput.value) {
      loadProducts(outletIdInput.value);
    }
  }
})();
