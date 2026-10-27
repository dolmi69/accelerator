// Existing deep links still reveal their section without a duplicate navigation bar.
function revealWorkspaceSection() {
  if (['#project-history', '#project-tasks'].includes(location.hash)) {
    const section = document.getElementById(location.hash.slice(1));
    if (section) section.open = true;
  }
}
revealWorkspaceSection();
window.addEventListener('hashchange', revealWorkspaceSection);

document.querySelectorAll('[data-task-checkbox]').forEach(checkbox => {
  const dialog = document.getElementById(checkbox.dataset.taskDialog);
  if (!dialog) return;
  function openResult() {
    // Completion is persisted only when an observation has been saved.
    checkbox.checked = false;
    dialog.showModal();
  }
  checkbox.addEventListener('change', openResult);
  const link = document.querySelector(`[data-task-open="${dialog.id}"]`);
  link?.addEventListener('click', event => { event.preventDefault(); openResult(); });
  dialog.querySelector('[data-task-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    checkbox.checked = false;
    checkbox.focus({preventScroll:true});
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
});

const assessmentForm = document.getElementById("ai-assessment-form");

if (assessmentForm) {
  assessmentForm.addEventListener("submit", () => {
    const button = assessmentForm.querySelector("button[type='submit']");
    const status = document.getElementById("ai-assessment-status");
    button.disabled = true;
    status.textContent = "Бруно изучает данные. Это может занять около минуты…";
  });
}

document.querySelectorAll("[data-interactive-radar]").forEach((radar) => {
  const chart = radar.querySelector("[data-radar-chart]");
  const tooltip = radar.querySelector("[data-radar-tooltip]");
  const tooltipLabel = tooltip.querySelector("[data-radar-tooltip-label]");
  const points = Array.from(radar.querySelectorAll("[data-radar-axis]"));
  const panels = Array.from(radar.querySelectorAll("[data-radar-detail]"));
  let tooltipPoint = null;
  let activePoint = null;

  function isOpen(panel) { return panel.matches(":popover-open"); }

  function syncSelection() {
    points.forEach((point) => {
      const panel = panels.find((item) => item.dataset.radarDetail === point.dataset.radarAxis);
      point.setAttribute("aria-expanded", String(Boolean(panel && isOpen(panel))));
    });
  }

  function hideTooltip() {
    tooltip.hidden = true;
    tooltipPoint = null;
  }

  // Строка списка и точка одного направления подсвечивают друг друга.
  function link(point, on) {
    points.forEach((item) => {
      if (item !== point && item.dataset.radarAxis === point.dataset.radarAxis) item.classList.toggle("is-linked", on);
    });
  }

  function showTooltip(point) {
    // У строки списка название и балл уже видны, подсказка нужна только на радаре.
    if (panels.some(isOpen) || !chart.contains(point)) return;
    tooltipPoint = point;
    tooltipLabel.textContent = `${point.dataset.label} · ${point.dataset.score === "—" ? "нет оценки" : point.dataset.score + "/100"}`;
    tooltip.hidden = false;
    const bounds = point.getBoundingClientRect();
    const chartBounds = chart.getBoundingClientRect();
    const halfWidth = tooltip.offsetWidth / 2;
    const center = bounds.left + bounds.width / 2 - chartBounds.left;
    tooltip.style.left = `${Math.max(halfWidth, Math.min(chartBounds.width - halfWidth, center))}px`;
    tooltip.style.top = `${bounds.top + bounds.height / 2 - chartBounds.top}px`;
  }

  function positionPanel(panel) {
    const edge = 16;
    const bounds = activePoint.getBoundingClientRect();
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    if (window.matchMedia("(max-width:680px)").matches) {
      panel.style.left = `${(window.innerWidth - width) / 2}px`;
      panel.style.top = `${Math.max(edge, window.innerHeight - height - edge)}px`;
      return;
    }
    let left = bounds.right + 14;
    if (left + width > window.innerWidth - edge) left = bounds.left - width - 14;
    panel.style.left = `${Math.max(edge, Math.min(left, window.innerWidth - width - edge))}px`;
    panel.style.top = `${Math.max(edge, Math.min(bounds.top - 20, window.innerHeight - height - edge))}px`;
  }

  function activate(point) {
    const panel = panels.find((item) => item.dataset.radarDetail === point.dataset.radarAxis);
    if (!panel) return;
    hideTooltip();
    if (isOpen(panel)) { panel.hidePopover(); return; }
    panels.forEach((other) => { if (isOpen(other)) other.hidePopover(); });
    activePoint = point;
    panel.showPopover();
    positionPanel(panel);
    panel.querySelector("[data-radar-close]").focus({preventScroll:true});
    syncSelection();
  }

  points.forEach((point) => {
    point.addEventListener("pointerenter", () => { link(point, true); showTooltip(point); });
    point.addEventListener("pointerleave", () => { link(point, false); hideTooltip(); });
    point.addEventListener("focus", () => { link(point, true); showTooltip(point); });
    point.addEventListener("blur", () => { link(point, false); hideTooltip(); });
    point.addEventListener("click", () => activate(point));
    point.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate(point);
      }
      if (event.key === "Escape") hideTooltip();
    });
  });

  panels.forEach((panel) => {
    panel.querySelector("[data-radar-close]").addEventListener("click", () => {
      panel.hidePopover();
      activePoint?.focus({preventScroll:true});
      hideTooltip();
      syncSelection();
    });
    panel.addEventListener("toggle", syncSelection);
    // Native popovers handle outside clicks and Escape. Return keyboard focus
    // to the chart for Escape without intercepting clicks on the rest of the page.
    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      panel.hidePopover();
      activePoint?.focus({preventScroll:true});
      hideTooltip();
      syncSelection();
    });
    new ResizeObserver(() => { if (isOpen(panel) && activePoint) positionPanel(panel); }).observe(panel);
  });
  window.addEventListener("resize", () => {
    if (tooltipPoint) showTooltip(tooltipPoint);
    panels.forEach((panel) => { if (isOpen(panel)) positionPanel(panel); });
  });
  // Follow the chart during page scroll, including browser focus scrolling.
  window.addEventListener("scroll", () => {
    hideTooltip();
    panels.forEach((panel) => { if (isOpen(panel)) positionPanel(panel); });
  }, {passive:true});
  syncSelection();
});
