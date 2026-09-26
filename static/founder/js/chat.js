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
    return content;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    const attachment = fileInput.files[0];
    if (!text && !attachment) return;

    const payload = new FormData(form);
    button.disabled = true;
    const finishButton = document.querySelector('[data-pitch-finish] button');
    if (finishButton) finishButton.disabled = true;
    addMessage("user", text || `Файл: ${attachment.name}`);
    const answer = addMessage("assistant", "Бруно думает…");
    input.value = "";
    fileInput.value = "";
    fileName.textContent = "Прикрепить файл";

    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: payload,
        headers: { Accept: "text/event-stream" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(typeof result.error === "string"
          ? result.error
          : "Проверьте текст сообщения и формат файла.");
      }
      if (!response.body) throw new Error("Браузер не поддерживает потоковый ответ.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let started = false;
      let completed = false;

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
          if (data.type === "delta") {
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
      window.location.reload();
    } catch (error) {
      answer.textContent = `Не удалось получить ответ: ${error.message}`;
      answer.classList.add("stream-error");
      scrollToBottom();
    } finally {
      button.disabled = false;
      if (finishButton) finishButton.disabled = false;
    }
  });
})();
