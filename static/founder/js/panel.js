(() => {
  const section = document.querySelector("[data-panel-verdict]");
  if (!section) return;

  const votes = [...section.querySelectorAll("[data-vote]")];
  const tally = section.querySelector("[data-tally]");
  const status = section.querySelector("[data-reveal-status]");
  const skip = section.querySelector("[data-reveal-skip]");
  const storyButton = section.querySelector("[data-story-card]");
  const finalTally = tally.textContent;
  const invested = Number(section.dataset.invested) || 0;
  let timers = [];

  function showAll() {
    timers.forEach(clearTimeout);
    timers = [];
    votes.forEach(vote => vote.classList.remove("is-pending"));
    tally.textContent = finalTally;
    status.textContent = "";
    skip.hidden = true;
  }

  // Голоса открываются по одному только сразу после голосования, а не при каждом визите.
  const params = new URLSearchParams(window.location.search);
  if (params.get("reveal") === "1") {
    params.delete("reveal");
    const query = params.toString();
    history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : "") + window.location.hash);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduceMotion) {
      votes.forEach(vote => vote.classList.add("is-pending"));
      tally.textContent = "Акулы совещаются…";
      skip.hidden = false;
      skip.addEventListener("click", showAll);
      votes.forEach((vote, index) => {
        const start = index * 1700;
        timers.push(setTimeout(() => { status.textContent = `${vote.dataset.name} думает…`; }, start + 300));
        timers.push(setTimeout(() => {
          vote.classList.remove("is-pending");
          const word = vote.dataset.decision === "invest" ? "вкладываю" : "пас";
          status.textContent = `${vote.dataset.name}: ${word}.`;
        }, start + 1500));
      });
      timers.push(setTimeout(showAll, votes.length * 1700 + 800));
    }
  }

  const SHARK_COLORS = { timur: "#16877d", oleg: "#534ab7", margarita: "#d85a30" };

  function wrap(context, text, maxWidth) {
    const lines = [];
    let line = "";
    for (const word of text.split(/\s+/)) {
      const candidate = line ? `${line} ${word}` : word;
      if (context.measureText(candidate).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    }
    if (line) lines.push(line);
    return lines;
  }

  function drawCard() {
    const canvas = document.createElement("canvas");
    canvas.width = 1080;
    canvas.height = 1920;
    const context = canvas.getContext("2d");
    const font = getComputedStyle(document.body).fontFamily || "sans-serif";
    context.fillStyle = "#172b3d";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.textAlign = "center";

    context.fillStyle = "#9fe1cb";
    context.font = `700 34px ${font}`;
    context.fillText("CO-FOUNDER.AI · ПАНЕЛЬ АКУЛ", 540, 220);

    context.fillStyle = "#ffffff";
    context.font = `800 72px ${font}`;
    const title = wrap(context, section.dataset.project || "Мой проект", 900).slice(0, 3);
    title.forEach((line, index) => context.fillText(line, 540, 400 + index * 88));

    const top = 820;
    votes.forEach((vote, index) => {
      const x = 240 + index * 300;
      const shark = [...vote.classList].find(name => name.startsWith("shark-"))?.slice(6);
      context.fillStyle = SHARK_COLORS[shark] || "#16877d";
      context.beginPath();
      context.arc(x, top, 105, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "#ffffff";
      context.font = `800 92px ${font}`;
      context.fillText(vote.dataset.initial, x, top + 32);
      const yes = vote.dataset.decision === "invest";
      context.fillStyle = yes ? "#9fe1cb" : "#f0997b";
      context.font = `800 40px ${font}`;
      context.fillText(yes ? "✓ Вкладываю" : "✗ Пас", x, top + 190);
      context.fillStyle = "#c9d4dc";
      context.font = `500 34px ${font}`;
      context.fillText(vote.dataset.name, x, top + 245);
    });

    context.fillStyle = "#ffffff";
    context.font = `800 108px ${font}`;
    const headline = invested ? [`${invested} из 3 акул`, "вложились"] : ["Акулы пока", "не вложились"];
    headline.forEach((line, index) => context.fillText(line, 540, 1300 + index * 124));
    context.fillStyle = "#c9d4dc";
    context.font = `500 42px ${font}`;
    const subline = invested === 3 ? "Все три сказали «вкладываю»" : `${3 - invested} ${3 - invested === 1 ? "условие" : "условия"}, чтобы это изменить`;
    context.fillText(subline, 540, 1580);

    context.fillStyle = "#7f93a3";
    context.font = `500 32px ${font}`;
    context.fillText("Тренировка, не инвестиционное решение", 540, 1800);
    return canvas;
  }

  if (storyButton) {
    storyButton.hidden = false;
    storyButton.addEventListener("click", () => {
      drawCard().toBlob(blob => {
        if (!blob) return;
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "panel-akul.png";
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      }, "image/png");
    });
  }
})();
