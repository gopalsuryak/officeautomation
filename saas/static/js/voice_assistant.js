(function () {
  const micBtn = document.getElementById("micBtn");
  const parseBtn = document.getElementById("parseBtn");
  const confirmBtn = document.getElementById("confirmBtn");
  const cancelBtn = document.getElementById("cancelBtn");
  const commandInput = document.getElementById("commandInput");
  const transcriptArea = document.getElementById("transcriptArea");
  const voiceSupportMessage = document.getElementById("voiceSupportMessage");
  const executionMessage = document.getElementById("executionMessage");

  const previewIntent = document.getElementById("previewIntent");
  const previewConfidence = document.getElementById("previewConfidence");
  const previewMessage = document.getElementById("previewMessage");
  const previewParameters = document.getElementById("previewParameters");
  const previewClientInfo = document.getElementById("previewClientInfo");

  let latestPreview = null;
  let recognition = null;

  function updatePreview(preview) {
    latestPreview = preview || null;
    previewIntent.textContent = (preview && preview.intent) || "-";
    previewConfidence.textContent = (preview && preview.confidence) || "-";
    previewMessage.textContent = (preview && (preview.warning || preview.message)) || "Parse a command to view preview.";
    previewParameters.textContent = JSON.stringify((preview && preview.parameters) || {}, null, 2);

    if (preview && preview.client_match) {
      previewClientInfo.textContent = "Matched client: " + preview.client_match.client_name + " (#" + preview.client_match.client_entity_id + ")";
    } else if (preview && preview.client_candidates && preview.client_candidates.length) {
      previewClientInfo.textContent = "Candidates:\n" + preview.client_candidates
        .map((c) => "- " + c.client_name + " (#" + c.client_entity_id + ")")
        .join("\n");
    } else {
      previewClientInfo.textContent = "None";
    }

    const canExecute = !!preview && preview.intent && preview.intent !== "unknown";
    confirmBtn.disabled = !canExecute;
  }

  async function parseCommand() {
    const commandText = (commandInput.value || "").trim();
    if (!commandText) {
      executionMessage.textContent = "Enter a command first.";
      return;
    }

    executionMessage.textContent = "Parsing command...";
    try {
      const response = await fetch("/voice-assistant/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command_text: commandText }),
      });
      const payload = await response.json();
      updatePreview(payload);
      executionMessage.textContent = payload.message || "Command parsed.";
    } catch (error) {
      updatePreview(null);
      executionMessage.textContent = "Could not parse command right now.";
    }
  }

  async function executeCommand() {
    if (!latestPreview) {
      executionMessage.textContent = "Parse a command first.";
      return;
    }

    executionMessage.textContent = "Executing confirmed action...";
    try {
      const response = await fetch("/voice-assistant/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(latestPreview),
      });
      const payload = await response.json();
      executionMessage.textContent = payload.message || "Execution completed.";
      if (payload.success && payload.redirect_url) {
        window.location.href = payload.redirect_url;
      }
    } catch (error) {
      executionMessage.textContent = "Could not execute command right now.";
    }
  }

  function resetState() {
    latestPreview = null;
    updatePreview(null);
    executionMessage.textContent = "Cancelled.";
  }

  function setupSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      voiceSupportMessage.textContent = "Voice recognition is not supported in this browser. Use text input.";
      if (micBtn) {
        micBtn.disabled = true;
      }
      return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "en-IN";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = function (event) {
      const transcript = event.results[0][0].transcript || "";
      transcriptArea.textContent = transcript;
      commandInput.value = transcript;
      voiceSupportMessage.textContent = "Voice transcript captured.";
    };

    recognition.onerror = function () {
      voiceSupportMessage.textContent = "Voice recognition failed. You can continue with manual text input.";
    };

    recognition.onend = function () {
      micBtn.innerHTML = '<i class="bi bi-mic"></i> Start Mic';
      micBtn.disabled = false;
    };

    voiceSupportMessage.textContent = "Voice recognition is available.";
  }

  if (parseBtn) {
    parseBtn.addEventListener("click", parseCommand);
  }
  if (confirmBtn) {
    confirmBtn.addEventListener("click", executeCommand);
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", resetState);
  }
  if (micBtn) {
    micBtn.addEventListener("click", function () {
      if (!recognition) {
        return;
      }
      micBtn.disabled = true;
      micBtn.innerHTML = '<i class="bi bi-mic-fill"></i> Listening...';
      voiceSupportMessage.textContent = "Listening...";
      recognition.start();
    });
  }

  updatePreview(null);
  setupSpeechRecognition();
})();
