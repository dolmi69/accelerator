(() => {
  const form = document.getElementById("chat-form");
  if (!form) return;

  const list = document.getElementById("message-list");
  const input = document.getElementById("message-input");
  const fileInput = document.getElementById("attachment-input");
  const fileName = document.getElementById("file-name");
  const button = document.getElementById("send-button");

  const scrollToBottom = () => { list.scrollTop = list.scrollHeight; };
  const citedMessage = document.getElementById(window.location.hash.slice(1));
  if (citedMessage && list.contains(citedMessage)) {
    citedMessage.scrollIntoView({block: "center"});
  } else {
    scrollToBottom();
  }

  fileInput.addEventListener("change", () => {
    fileName.textContent = fileInput.files[0]?.name || "Прикрепить файл";
  });

  // Подсказка под полем ввода отправляется как обычное сообщение.
  document.querySelectorAll("[data-quick-reply]").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (button.disabled) return;
      input.value = chip.dataset.quickReply;
      form.requestSubmit(button);
    });
  });

  // Бруно ещё доделывает прошлый запрос: ждём и повторяем, а не пугаем ошибкой.
  const BUSY_RETRIES = 4;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!button.disabled) form.requestSubmit(button);
  });

  function addMessage(role, text) {
    const node = document.createElement("div");
    node.className = `message message-${role}`;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "В" : "Б";
    const body = document.createElement("div");
    body.className = "message-body";
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = role === "user" ? "Вы" : "Бруно";
    const content = document.createElement("div");
    content.className = "message-text";
    content.textContent = text;
    body.append(meta, content);
    node.append(avatar, body);
    list.append(node);
    scrollToBottom();
    content.bubble = { node, avatar, meta };
    return content;
  }

  // Панель акул: сервер называет говорящего до текста его реплики.
  function setSpeaker(content, data) {
    const { node, avatar, meta } = content.bubble;
    node.className = `message message-assistant shark-${data.speaker}`;
    avatar.textContent = data.initial || data.name.charAt(0);
    meta.textContent = data.title ? `${data.name} · ${data.title}` : data.name;
    content.textContent = `${data.name} думает…`;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    const text = input.value.trim();
    const attachment = fileInput.files[0];
    if (!text && !attachment) return;

    const payload = new FormData(form);
    button.disabled = true;
    input.disabled = true;
    fileInput.disabled = true;
    const finishButton = document.querySelector('[data-pitch-finish] button');
    if (finishButton) finishButton.disabled = true;
    const sent = addMessage("user", text || `Файл: ${attachment.name}`);
    let answer = addMessage("assistant", "Бруно думает…");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    let accepted = false;
    let rejected = false;
    let completed = false;

    try {
      let response;
      for (let attempt = 0; ; attempt += 1) {
        response = await fetch(form.action, {
          method: "POST",
          body: payload,
          headers: { Accept: "text/event-stream" },
          credentials: "same-origin",
          signal: controller.signal,
        });
        const wait = Number(response.headers && response.headers.get ? response.headers.get("Retry-After") : 0);
        // 429 приходит до сохранения сообщения, поэтому повтор не создаёт дубль.
        if (response.status !== 429 || attempt >= BUSY_RETRIES || !(wait > 0 && wait <= 30)) break;
        answer.textContent = `Бруно заканчивает предыдущий запрос, отвечу через ${wait} с…`;
        await sleep(wait * 1000);
      }
      if (response.redirected) {
        rejected = true;
        throw new Error("Сессия завершилась. Войдите в аккаунт снова. Текст остался в поле ввода.");
      }
      if (!response.ok) {
        rejected = true;
        const result = await response.json().catch(() => ({}));
        throw new Error(typeof result.error === "string"
          ? result.error
          : response.status === 403 ? "Сессия устарела. Скопируйте текст и обновите страницу."
          : response.status >= 500 ? "Сервис временно недоступен. Текст остался в поле ввода."
          : "Проверьте текст сообщения и формат файла.");
      }
      const type = response.headers.get("Content-Type") || "";
      if (!type.includes("text/event-stream") && !type.includes("text/plain")) {
        throw new Error("Сервер вернул неожиданный ответ.");
      }
      // The backend saves the founder's message before starting the AI stream.
      accepted = true;
      input.value = "";
      fileInput.value = "";
      fileName.textContent = "Прикрепить файл";
      if (!response.body) throw new Error("Браузер не поддерживает потоковый ответ.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let started = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const eventText = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const line = eventText.split("\n").find(item => item.startsWith("data: "));
          if (!line) continue;
          const data = JSON.parse(line.slice(6));
          if (data.type === "speaker") {
            if (typeof data.name !== "string" || typeof data.speaker !== "string") throw new Error("Неверный формат ответа.");
            // Пока реплика пуста, переименовываем её; иначе следующий говорящий получает свою.
            if (started) { answer = addMessage("assistant", ""); started = false; }
            setSpeaker(answer, data);
          } else if (data.type === "status") {
            if (typeof data.text === "string" && !started) answer.textContent = data.text;
          } else if (data.type === "delta") {
            if (typeof data.text !== "string") throw new Error("Неверный формат ответа.");
            if (!started) { answer.textContent = ""; started = true; }
            answer.textContent += data.text;
            scrollToBottom();
          } else if (data.type === "error") {
            throw new Error(data.message);
          } else if (data.type === "done") {
            completed = true;
          }
        }
      }
      if (!completed) throw new Error("Соединение прервалось до завершения ответа.");
      window.location.assign(window.location.pathname);
    } catch (error) {
      const reason = error.name === "AbortError" ? "Истекло время ожидания." : error.message;
      answer.textContent = `Не удалось получить ответ: ${reason}`;
      if (rejected) sent.closest(".message").remove();
      if (!rejected) {
        answer.append(document.createTextNode(accepted
          ? " Ваше сообщение сохранено. Обновите чат, чтобы проверить ответ. "
          : " Сообщение могло сохраниться. Текст остался в поле ввода — скопируйте его и проверьте чат перед повторной отправкой. "));
        const refresh = document.createElement("a");
        refresh.href = window.location.pathname;
        refresh.textContent = "Обновить чат";
        answer.append(refresh);
      }
      answer.classList.add("stream-error");
      scrollToBottom();
    } finally {
      clearTimeout(timeout);
      controller.abort();
      // Do not resend an ambiguous network request and create a duplicate.
      button.disabled = !rejected && !completed;
      input.disabled = false;
      fileInput.disabled = false;
      if (finishButton) finishButton.disabled = false;
    }
  });
})();
