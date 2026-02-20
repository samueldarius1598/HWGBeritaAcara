document.addEventListener("DOMContentLoaded", function () {
  const passwordToggles = document.querySelectorAll(".password-toggle");
  const rememberStorageKey = "remember_login_email";
  const loginForm = document.querySelector('form.modern-form[action="/login"]');
  const emailInput = document.getElementById("email");
  const rememberCheckbox = document.getElementById("remember_me");

  // Fallback client-side remember email if browser cookies are not consistently sent.
  if (emailInput && rememberCheckbox && !emailInput.value) {
    const rememberedEmail = window.localStorage.getItem(rememberStorageKey) || "";
    if (rememberedEmail) {
      emailInput.value = rememberedEmail;
      rememberCheckbox.checked = true;
    }
  }

  if (loginForm && emailInput && rememberCheckbox) {
    loginForm.addEventListener("submit", function () {
      const emailValue = emailInput.value.trim();
      if (rememberCheckbox.checked && emailValue) {
        window.localStorage.setItem(rememberStorageKey, emailValue);
        return;
      }
      window.localStorage.removeItem(rememberStorageKey);
    });
  }

  passwordToggles.forEach((button) => {
    button.addEventListener("click", function () {
      const targetId = this.getAttribute("data-target");
      const input = document.getElementById(targetId);

      if (!input) {
        return;
      }

      const iconEye = this.querySelector(".icon-eye");
      const iconEyeOff = this.querySelector(".icon-eye-off");
      const showPassword = input.type === "password";

      input.type = showPassword ? "text" : "password";

      if (iconEye && iconEyeOff) {
        iconEye.classList.toggle("hidden", showPassword);
        iconEyeOff.classList.toggle("hidden", !showPassword);
      }

      this.setAttribute(
        "aria-label",
        showPassword ? "Sembunyikan password" : "Lihat password"
      );
    });
  });
});
