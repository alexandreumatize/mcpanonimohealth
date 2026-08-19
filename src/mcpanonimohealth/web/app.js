const input = document.querySelector("#document");
const folderInput = document.querySelector("#folder");
const form = document.querySelector("#intake-form");
const dropZone = document.querySelector("#drop-zone");
const dropTitle = document.querySelector("#drop-title");
const dropNote = document.querySelector("#drop-note");
const pickFiles = document.querySelector("#pick-files");
const pickFolder = document.querySelector("#pick-folder");
const fileList = document.querySelector("#file-list");
const processButton = document.querySelector("#process-button");
const processing = document.querySelector("#processing");
const processingTitle = document.querySelector("#processing-title");
const processingNote = document.querySelector("#processing-note");
const result = document.querySelector("#result");
const batchActions = document.querySelector("#batch-actions");
const batchList = document.querySelector("#batch-list");
const downloadZip = document.querySelector("#download-zip");
const token = document.querySelector('meta[name="local-session"]').content;
const allowed = new Set(["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "heic", "heif", "txt"]);
const maxBytes = 50 * 1024 * 1024;
const maxFiles = 40;
let selectedFiles = [];

const reasons = {
  FILE_TOO_LARGE: "Um arquivo ultrapassa o limite de 50 MB (ou o lote excede o limite total).",
  UNSUPPORTED_FORMAT: "Há arquivo com formato incompatível.",
  EMPTY_FILE: "Há arquivo vazio.",
  DOCUMENT_MISSING: "Nenhum documento foi recebido pela interface local.",
  TOO_MANY_FILES: "O lote ultrapassa o limite de 40 documentos.",
  OCR_FAILED: "Não foi possível ler o texto da imagem.",
  DOCUMENT_LOW_CONFIDENCE: "A leitura automática ficou abaixo da confiança mínima (aviso; o texto ainda pode ser liberado).",
  INSUFFICIENT_CLINICAL_TEXT: "Não foi encontrado texto clínico suficiente.",
  TOO_MANY_PAGES: "O documento tem mais de 10 páginas.",
  PDF_CORRUPT_OR_PROTECTED: "O PDF está protegido ou não pôde ser aberto.",
  IMAGE_CORRUPT_OR_UNSUPPORTED: "A imagem está corrompida ou não pôde ser aberta.",
  NER_NOT_READY: "O modelo local de proteção ainda não está pronto.",
  NER_NOT_READY_CONTINUED: "O modelo NER não estava pronto; a máscara seguiu com regras locais.",
  NER_RUNTIME_FAILURE: "O modelo local encontrou uma falha durante a verificação.",
  LOCAL_PROCESSING_FAILED: "O processamento local não pôde ser concluído.",
  EMPTY_INPUT: "O documento não continha texto utilizável.",
  DOWNLOAD_UNAVAILABLE: "O ZIP do lote não está mais disponível. Reabra a interface."
};

function reasonMessage(code) {
  if (reasons[code]) return reasons[code];
  if (/_QR_OR_BARCODE$/.test(code)) {
    return "Foi detectado QR ou código de barras em uma página (aviso; não bloqueia a liberação).";
  }
  if (/_LOW_CONFIDENCE$/.test(code)) {
    return "Uma página teve confiança de OCR baixa (aviso; o texto extraído ainda pode ser usado).";
  }
  if (/_NO_TEXT$/.test(code)) {
    return "Uma página não rendeu texto no OCR (aviso).";
  }
  if (/^RESIDUAL_BEST_EFFORT_/.test(code)) {
    return "Houve identificador residual tratado em melhor esforço.";
  }
  return "A verificação local registrou um aviso operacional.";
}

function humanSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(file) {
  return (file?.name.split(".").pop() || "").toLowerCase();
}

function acceptFiles(fileListLike) {
  const incoming = Array.from(fileListLike || []);
  const accepted = [];
  for (const file of incoming) {
    if (!allowed.has(extensionOf(file))) continue;
    if (file.size > maxBytes) continue;
    accepted.push(file);
  }
  if (!accepted.length) {
    selectedFiles = [];
    dropTitle.textContent = "Nenhum arquivo compatível";
    dropNote.textContent = "Use PDF, imagem ou TXT (máx. 50 MB cada).";
    processButton.disabled = true;
    renderFileList();
    return;
  }
  if (accepted.length > maxFiles) {
    selectedFiles = accepted.slice(0, maxFiles);
    dropTitle.textContent = `${selectedFiles.length} documentos (limite ${maxFiles})`;
    dropNote.textContent = "Os primeiros 40 arquivos compatíveis foram mantidos.";
  } else {
    selectedFiles = accepted;
    dropTitle.textContent = selectedFiles.length === 1
      ? selectedFiles[0].name
      : `${selectedFiles.length} documentos prontos`;
    const total = selectedFiles.reduce((sum, file) => sum + file.size, 0);
    dropNote.textContent = selectedFiles.length === 1
      ? `${humanSize(selectedFiles[0].size)} · pronto para processamento local`
      : `${humanSize(total)} no total · lote local`;
  }
  processButton.disabled = false;
  processButton.textContent = selectedFiles.length > 1
    ? `Desidentificar lote (${selectedFiles.length})`
    : "Desidentificar neste computador";
  renderFileList();
}

function renderFileList() {
  fileList.replaceChildren();
  if (!selectedFiles.length) {
    fileList.hidden = true;
    return;
  }
  fileList.hidden = false;
  selectedFiles.forEach((file, index) => {
    const item = document.createElement("li");
    item.innerHTML = `<strong>${index + 1}.</strong> <span></span> <em></em>`;
    item.querySelector("span").textContent = file.name;
    item.querySelector("em").textContent = humanSize(file.size);
    fileList.append(item);
  });
}

dropZone.addEventListener("click", () => input.click());
pickFiles.addEventListener("click", () => input.click());
pickFolder.addEventListener("click", () => folderInput.click());
input.addEventListener("change", () => acceptFiles(input.files));
folderInput.addEventListener("change", () => acceptFiles(folderInput.files));
["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragging");
}));
["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
}));
dropZone.addEventListener("drop", (event) => acceptFiles(event.dataTransfer.files));

function metric(text) {
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}

function renderSingle(data) {
  const state = data.estado || "ERROR";
  result.dataset.state = state;
  document.querySelector("#result-kicker").textContent = `Resultado local · ${state}`;
  const title = document.querySelector("#result-title");
  const message = document.querySelector("#result-message");
  const metrics = document.querySelector("#result-metrics");
  const textWrap = document.querySelector("#result-text-wrap");
  const textBox = document.querySelector("#result-text");
  const copyButton = document.querySelector("#copy-text");
  metrics.replaceChildren();
  batchActions.hidden = true;
  batchList.hidden = true;
  batchList.replaceChildren();
  textWrap.hidden = true;
  textBox.textContent = "";

  if (state === "PASS") {
    title.textContent = "Texto desidentificado pronto";
    message.textContent = "Revise abaixo o texto liberado. Idade e sexo são preservados para análise; identificadores diretos devem aparecer mascarados.";
    const released = data.texto_desidentificado || "";
    if (released) {
      textBox.textContent = released;
      textWrap.hidden = false;
      copyButton.onclick = async () => {
        try {
          await navigator.clipboard.writeText(released);
          copyButton.textContent = "Copiado";
        } catch (_error) {
          copyButton.textContent = "Selecione o texto";
        }
      };
    }
  } else if (state === "HOLD") {
    title.textContent = "Documento retido por segurança";
    const codes = data.motivos || [];
    message.textContent = codes.map((code) => reasonMessage(code)).join(" ")
      + " Feche esta aba e peça ao agente para tentar novamente com uma digitalização mais nítida.";
  } else {
    title.textContent = "Processamento local interrompido";
    message.textContent = reasons[data.codigo] || reasonMessage(data.codigo || "") || "Não foi possível concluir com segurança.";
  }
  if (data.paginas) metrics.append(metric(`${data.paginas} página${data.paginas > 1 ? "s" : ""}`));
  if (data.duracao_ms) metrics.append(metric(`${(data.duracao_ms / 1000).toFixed(1)} s local`));
  const total = Object.values(data.contagens || {}).reduce((sum, value) => sum + Number(value), 0);
  if (total) metrics.append(metric(`${total} marcador${total > 1 ? "es" : ""}`));
}

function renderBatch(data) {
  const state = data.estado || "ERROR";
  result.dataset.state = state;
  document.querySelector("#result-kicker").textContent = `Lote local · ${state}`;
  document.querySelector("#result-title").textContent = data.liberados
    ? "Lote desidentificado"
    : "Lote sem textos liberados";
  document.querySelector("#result-message").textContent = data.aviso
    || `${data.liberados || 0} liberado(s), ${data.retidos || 0} retido(s), ${data.erros || 0} erro(s).`;
  const metrics = document.querySelector("#result-metrics");
  metrics.replaceChildren();
  metrics.append(metric(`${data.processados || 0} processados`));
  metrics.append(metric(`${data.liberados || 0} PASS`));
  metrics.append(metric(`${data.retidos || 0} HOLD`));
  metrics.append(metric(`${data.erros || 0} ERROR`));
  if (data.duracao_ms) metrics.append(metric(`${(data.duracao_ms / 1000).toFixed(1)} s`));

  document.querySelector("#result-text-wrap").hidden = true;
  batchActions.hidden = !data.baixar_disponivel;
  if (data.baixar_disponivel) {
    downloadZip.href = "baixar.zip";
    downloadZip.onclick = () => {
      // Após o download, a sessão encerra no servidor.
      setTimeout(() => {
        downloadZip.textContent = "ZIP solicitado";
      }, 300);
    };
  }

  batchList.replaceChildren();
  batchList.hidden = false;
  (data.itens || []).forEach((item) => {
    const card = document.createElement("article");
    card.className = "batch-item";
    card.dataset.state = item.estado || "ERROR";
    const head = document.createElement("header");
    const label = [
      `#${item.indice}`,
      item.estado,
      item.iniciais || "—",
      item.tipo || "—",
      item.data_documento || "—"
    ].join(" · ");
    head.innerHTML = `<strong></strong><span></span>`;
    head.querySelector("strong").textContent = label;
    head.querySelector("span").textContent = item.relativo || "";
    card.append(head);
    if (item.motivos?.length) {
      const notes = document.createElement("p");
      notes.textContent = item.motivos.map((code) => reasonMessage(code)).join(" ");
      card.append(notes);
    }
    if (item.texto_desidentificado) {
      const pre = document.createElement("pre");
      pre.className = "result-text";
      pre.textContent = item.texto_desidentificado;
      card.append(pre);
    }
    batchList.append(card);
  });
}

function render(data) {
  if (data.modo === "lote") renderBatch(data);
  else renderSingle(data);
  processing.hidden = true;
  result.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFiles.length) return;
  form.hidden = true;
  processing.hidden = false;
  processingTitle.textContent = selectedFiles.length > 1
    ? `Processando lote de ${selectedFiles.length} documentos…`
    : "Não feche esta página.";
  processingNote.textContent = selectedFiles.length > 1
    ? "PDFs multipágina podem levar alguns minutos no total."
    : "Imagens e PDFs podem levar de 30 a 60 segundos.";
  const payload = new FormData();
  selectedFiles.forEach((file) => payload.append("document", file, file.name));
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
    selectedFiles = [];
    input.value = "";
    folderInput.value = "";
  }
});
