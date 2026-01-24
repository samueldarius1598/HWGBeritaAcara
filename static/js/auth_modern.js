document.addEventListener("DOMContentLoaded", function () {
  const passwordToggles = document.querySelectorAll(".password-toggle");

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
