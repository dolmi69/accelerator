const assessmentForm = document.getElementById("ai-assessment-form");

if (assessmentForm) {
  assessmentForm.addEventListener("submit", () => {
    const button = assessmentForm.querySelector("button[type='submit']");
    const status = document.getElementById("ai-assessment-status");
    button.disabled = true;
    status.textContent = "Бруно изучает данные. Это может занять около минуты…";
  });
}
