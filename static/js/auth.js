(() => {
  const outlets = Array.isArray(window.OUTLETS) ? window.OUTLETS : [];
  const outletInput = document.getElementById("outlet_name");
  const outletHidden = document.getElementById("outlet_id");
  const registerForm = document.getElementById("register-form");
  const errorBox = document.getElementById("register-error");

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

  const getOutletMatch = (name) => {
    const needle = String(name || "").trim().toLowerCase();
    if (!needle) {
      return null;
    }
    return (
      outlets.find(
        (outlet) =>
          String(outlet?.name || "").toLowerCase() === needle
      ) || null
    );
  };

  const setupOutletAutocomplete = () => {
    if (!outletInput || !outletHidden) {
      return;
    }
    const wrap = outletInput.closest(".ac-wrap");
    const list = wrap ? wrap.querySelector(".ac-list") : null;
    if (!list) {
      return;
    }

    const hideList = () => {
      list.hidden = true;
      list.innerHTML = "";
      outletInput.dataset.acIndex = "-1";
    };

    const setActiveSuggestion = (items, activeIndex) => {
      items.forEach((item, index) => {
        item.classList.toggle("active", index === activeIndex);
      });
    };

    const renderSuggestions = (rawQuery) => {
      const query = String(rawQuery || "").trim().toLowerCase();
      const matches = outlets
        .filter((outlet) => {
          const name = String(outlet?.name || "").toLowerCase();
          return !query || name.includes(query);
        })
        .slice(0, 50);

      if (!matches.length) {
        hideList();
        return;
      }

      list.innerHTML = "";
      matches.forEach((outlet) => {
        const item = document.createElement("div");
        item.className = "ac-item";
        item.dataset.id = String(outlet.id ?? "");
        item.dataset.name = String(outlet.name ?? "");
        item.innerHTML = `<div class="ac-item-main">${escapeHtml(
          outlet.name ?? ""
        )}</div>`;
        list.appendChild(item);
      });
      list.hidden = false;
      outletInput.dataset.acIndex = "-1";
    };

    const selectOutlet = (id, name) => {
      outletHidden.value = id || "";
      outletInput.value = name || "";
      hideList();
    };

    outletInput.addEventListener("input", () => {
      outletHidden.value = "";
      renderSuggestions(outletInput.value);
    });

    outletInput.addEventListener("focus", () => {
      renderSuggestions(outletInput.value);
    });

    outletInput.addEventListener("keydown", (event) => {
      if (list.hidden) {
        return;
      }
      const items = Array.from(list.querySelectorAll(".ac-item"));
      if (!items.length) {
        return;
      }
      let idx = parseInt(outletInput.dataset.acIndex || "-1", 10);
      if (event.key === "ArrowDown") {
        event.preventDefault();
        idx = (idx + 1 + items.length) % items.length;
        outletInput.dataset.acIndex = String(idx);
        setActiveSuggestion(items, idx);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        idx = (idx - 1 + items.length) % items.length;
        outletInput.dataset.acIndex = String(idx);
        setActiveSuggestion(items, idx);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const selected = items[Math.max(idx, 0)];
        if (selected) {
          selectOutlet(selected.dataset.id, selected.dataset.name);
        }
      } else if (event.key === "Escape") {
        hideList();
      }
    });

    outletInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        const match = getOutletMatch(outletInput.value);
        if (match) {
          outletHidden.value = String(match.id ?? "");
        }
        hideList();
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
      selectOutlet(item.dataset.id, item.dataset.name);
    });

    if (outletInput.value && !outletHidden.value) {
      const match = getOutletMatch(outletInput.value);
      if (match) {
        outletHidden.value = String(match.id ?? "");
      }
    }
  };

  const showError = (message) => {
    if (!errorBox) {
      return;
    }
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  };

  const clearError = () => {
    if (!errorBox) {
      return;
    }
    errorBox.textContent = "";
    errorBox.classList.add("hidden");
  };

  const setupRegisterValidation = () => {
    if (!registerForm) {
      return;
    }
    registerForm.addEventListener("submit", (event) => {
      const password = document.getElementById("password");
      const confirm = document.getElementById("confirm_password");
      const passwordValue = password ? password.value : "";
      const confirmValue = confirm ? confirm.value : "";
      if (passwordValue !== confirmValue) {
        event.preventDefault();
        showError("Password dan konfirmasi harus sama.");
        return;
      }
      if (outletHidden && !outletHidden.value) {
        event.preventDefault();
        showError("Pilih outlet dari daftar yang tersedia.");
        return;
      }
      clearError();
    });
  };

  const setupPasswordToggles = () => {
    const toggles = document.querySelectorAll(".password-toggle");
    toggles.forEach((toggle) => {
      toggle.addEventListener("click", () => {
        const targetId = toggle.dataset.target;
        const input = targetId ? document.getElementById(targetId) : null;
        if (!input) {
          return;
        }
        const isVisible = input.type === "text";
        input.type = isVisible ? "password" : "text";
        toggle.dataset.visible = isVisible ? "false" : "true";
        toggle.setAttribute(
          "aria-label",
          isVisible ? "Tampilkan password" : "Sembunyikan password"
        );
        toggle.setAttribute("aria-pressed", isVisible ? "false" : "true");
      });
    });
  };

  setupOutletAutocomplete();
  setupRegisterValidation();
  setupPasswordToggles();
})();
