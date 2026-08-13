const input = document.querySelector("#document");
const form = document.querySelector("#intake-form");
const dropZone = document.querySelector("#drop-zone");
const dropTitle = document.querySelector("#drop-title");
const dropNote = document.querySelector("#drop-note");
const processButton = document.querySelector("#process-button");
const processing = document.querySelector("#processing");
const result = document.querySelector("#result");
const token = document.querySelector('meta[name="local-session"]').content;
const allowed = new Set(["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "heic", "heif", "txt"]);
const maxBytes = 50 * 1024 * 1024;
let selectedFile = null;

const reasons = {
  FILE_TOO_LARGE: "O arquivo ultrapassa o limite de 50 MB.",
  UNSUPPORTED_FORMAT: "O formato deste arquivo não é compatível.",
  EMPTY_FILE: "O arquivo está vazio.",
  DOCUMENT_MISSING: "Nenhum documento foi recebido pela interface local.",
  OCR_FAILED: "Não foi possível ler o texto da imagem.",
  DOCUMENT_LOW_CONFIDENCE: "A leitura automática ficou abaixo da confiança mínima.",
  INSUFFICIENT_CLINICAL_TEXT: "Não foi encontrado texto clínico suficiente.",
  TOO_MANY_PAGES: "O documento tem mais de 10 páginas.",
  PDF_CORRUPT_OR_PROTECTED: "O PDF está protegido ou não pôde ser aberto.",
  IMAGE_CORRUPT_OR_UNSUPPORTED: "A imagem está corrompida ou não pôde ser aberta.",
  NER_NOT_READY: "O modelo local de proteção ainda não está pronto.",
  NER_RUNTIME_FAILURE: "O modelo local encontrou uma falha durante a verificação.",
  LOCAL_PROCESSING_FAILED: "O processamento local não pôde ser concluído."
};

function humanSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function choose(file) {
  const extension = (file?.name.split(".").pop() || "").toLowerCase();
  if (!file || !allowed.has(extension)) {
    selectedFile = null;
    dropTitle.textContent = "Formato não compatível";
    dropNote.textContent = "Escolha PDF, imagem ou TXT.";
    processButton.disabled = true;
    return;
  }
  if (file.size > maxBytes) {
    selectedFile = null;
    dropTitle.textContent = "Arquivo acima de 50 MB";
    dropNote.textContent = "Reduza o tamanho antes de continuar.";
    processButton.disabled = true;
    return;
  }
  selectedFile = file;
  dropTitle.textContent = file.name;
  dropNote.textContent = `${humanSize(file.size)} · pronto para processamento local`;
  processButton.disabled = false;
}

dropZone.addEventListener("click", () => input.click());
input.addEventListener("change", () => choose(input.files[0]));
["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
}));
["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
}));
dropZone.addEventListener("drop", (event) => choose(event.dataTransfer.files[0]));

function render(data) {
  const state = data.estado || "ERROR";
  result.dataset.state = state;
  document.querySelector("#result-kicker").textContent = `Resultado local · ${state}`;
  const title = document.querySelector("#result-title");
  const message = document.querySelector("#result-message");
  const metrics = document.querySelector("#result-metrics");
  metrics.replaceChildren();

  if (state === "PASS") {
    title.textContent = "Texto protegido liberado ao agente";
    message.textContent = "Volte ao Codex ou Claude Code. O agente continuará usando somente o texto desidentificado e descartará o job ao terminar.";
  } else if (state === "HOLD") {
    title.textContent = "Documento retido por segurança";
    const codes = data.motivos || [];
    message.textContent = codes.map((code) => reasons[code] || "A verificação local encontrou um risco residual.").join(" ") + " Feche esta aba e peça ao agente para tentar novamente com uma digitalização mais nítida.";
  } else {
    title.textContent = "Processamento local interrompido";
    message.textContent = reasons[data.codigo] || "Não foi possível concluir com segurança. Feche esta aba e peça ao agente para verificar a instalação.";
  }
  if (data.paginas) metrics.append(metric(`${data.paginas} página${data.paginas > 1 ? "s" : ""}`));
  if (data.duracao_ms) metrics.append(metric(`${(data.duracao_ms / 1000).toFixed(1)} s local`));
  const total = Object.values(data.contagens || {}).reduce((sum, value) => sum + Number(value), 0);
  if (total) metrics.append(metric(`${total} identificador${total > 1 ? "es" : ""} substituído${total > 1 ? "s" : ""}`));
  processing.hidden = true;
  result.hidden = false;
}

function metric(text) {
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile) return;
  form.hidden = true;
  processing.hidden = false;
  const payload = new FormData();
  payload.append("document", selectedFile, selectedFile.name);
  try {
    const response = await fetch("processar", {
      method: "POST",
      headers: { "X-Local-Session": token },
      body: payload,
      credentials: "same-origin",
      cache: "no-store"
    });
    render(await response.json());
  } catch (_error) {
    render({ estado: "ERROR", codigo: "LOCAL_PROCESSING_FAILED" });
  } finally {
    selectedFile = null;
    input.value = "";
  }
});
