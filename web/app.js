/* SchemAccess web: runs the converter in the browser and shows the results. */

const PYODIDE_VERSION = "0.28.2";
const PYODIDE_URL =
  `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.mjs`;

const WORK_DIR = "/work";

import { renderPdf } from "./latex.js";

const $ = (id) => document.getElementById(id);

const els = {
  tagline: $("tagline"),
  filechip: $("filechip"),
  filename: $("filename"),
  reset: $("reset"),
  uploadView: $("upload-view"),
  workspaceView: $("workspace-view"),
  dropzone: $("dropzone"),
  choose: $("choose"),
  fileInput: $("file-input"),
  boot: $("boot"),
  bootText: $("boot-text"),
  bootSpinner: $("boot-spinner"),
  statusbar: $("statusbar"),
  statusIcon: $("status-icon"),
  statusText: $("status-text"),
  notesCount: $("notes-count"),
  notesCountText: $("notes-count-text"),
  openReport: $("open-report"),
  tabs: document.querySelectorAll("[role=tab]"),
  panel: $("panel"),
  fileList: $("file-list"),
  fileCount: $("file-count"),
  downloadAll: $("download-all"),
  overleaf: $("overleaf"),
  overleafForm: $("overleaf-form"),
  overleafSnip: $("overleaf-snip"),
  junctionDots: $("junction-dots"),
  showLoops: $("show-loops"),
  loopsNote: $("loops-note"),
  showUnits: $("show-units"),
};

const ICONS = {
  tick: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"></path></svg>`,
  warn: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path></svg>`,
  error: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M15 9l-6 6"></path><path d="M9 9l6 6"></path></svg>`,
  info: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 11v5"></path><path d="M12 7.5h.01"></path></svg>`,
  noteWarn: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path></svg>`,
  download: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v13"></path><path d="M7 12l5 5 5-5"></path><path d="M4 21h16"></path></svg>`,
  copy: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="1.5"></rect><path d="M5 15V5a2 2 0 0 1 2-2h10"></path></svg>`,
  minus: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"></path></svg>`,
  plus: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>`,
};

const NETLIST_TABS = [
  ["spice", "SPICE", ".cir"],
  ["kicad", "KiCad", ".net"],
  ["text", "Readable table", "_netlist.txt"],
  ["csv", "CSV", "_netlist.csv"],
];

const DETAIL_LEVELS = [
  ["short", "Short"],
  ["standard", "Standard"],
  ["detailed", "Detailed"],
];

const state = {
  pyodide: null,
  driver: null,
  ready: false,
  result: null,
  source: null,
  junctionDots: true,
  showLoops: false,
  showUnits: false,
  contents: {},
  tab: "preview",
  netlistFormat: "spice",
  detail: "detailed",
  latex: { status: "idle", pdf: null, url: null, error: "" },
};

function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

function duration(ms) {
  if (!ms || ms < 1) return "under a millisecond";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  return `${(size / 1024).toFixed(1)} KB`;
}

let toastTimer = null;
function toast(message) {
  let node = document.querySelector(".toast");
  if (!node) {
    node = document.createElement("div");
    node.className = "toast";
    node.setAttribute("role", "status");
    document.body.appendChild(node);
  }
  node.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), 2400);
}

function saveFile(name, text, mime) {
  const blob = text instanceof Blob
    ? text
    : new Blob([text], { type: `${mime || "text/plain"};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to the clipboard");
  } catch {
    toast("The browser would not allow copying — select the text instead");
  }
}

function setBoot(message, kind) {
  els.boot.hidden = kind === "ready";
  els.bootText.textContent = message;
  els.boot.className = `boot${kind ? ` is-${kind}` : ""}`;
  els.bootSpinner.hidden = kind === "ready" || kind === "failed";
}

async function boot() {
  setBoot("Loading…");
  try {
    const { loadPyodide } = await import(PYODIDE_URL);
    const pyodide = await loadPyodide({
      indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`,
    });

    const response = await fetch("schemaccess.zip", { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`schemaccess.zip could not be loaded (${response.status})`);
    }
    await pyodide.unpackArchive(await response.arrayBuffer(), "zip", {
      extractDir: "/schemaccess_pkg",
    });
    pyodide.runPython(
      `import sys, os\n` +
      `sys.path.insert(0, "/schemaccess_pkg")\n` +
      `os.makedirs("${WORK_DIR}", exist_ok=True)\n`);

    state.pyodide = pyodide;
    state.driver = pyodide.pyimport("driver");
    state.ready = true;

    setBoot("", "ready");

  } catch (error) {
    console.error(error);
    setBoot(
      "Could not start. The Python runtime is fetched once from a CDN; "
      + "check the connection and reload. "
      + `(${error && error.message ? error.message : error})`,
      "failed");
  }
}

function clearWorkDir() {
  state.pyodide.runPython(
    `import os, shutil\n` +
    `shutil.rmtree("${WORK_DIR}", ignore_errors=True)\n` +
    `os.makedirs("${WORK_DIR}", exist_ok=True)\n`);
}

async function writeUpload(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const safe = file.name.replace(/[\\/]/g, "_");
  state.pyodide.FS.writeFile(`${WORK_DIR}/${safe}`, bytes);
  return safe;
}

function expandArchives() {
  state.pyodide.runPython(
`import os, zipfile
for name in sorted(os.listdir("${WORK_DIR}")):
    if not name.lower().endswith(".zip"):
        continue
    path = os.path.join("${WORK_DIR}", name)
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                # Flatten: KiCad resolves sub-sheets by file name, and a
                # flat directory keeps a zip built on any OS working.
                target = os.path.join("${WORK_DIR}",
                                      os.path.basename(member))
                if not os.path.basename(member):
                    continue
                with archive.open(member) as source, open(target, "wb") as out:
                    out.write(source.read())
    except zipfile.BadZipFile:
        pass
    os.remove(path)
`);
}

function listSchematics() {
  const raw = state.pyodide.runPython(
    `import json, os\n` +
    `json.dumps(sorted(os.path.join("${WORK_DIR}", n) ` +
    `for n in os.listdir("${WORK_DIR}") ` +
    `if n.lower().endswith(".kicad_sch")))`);
  return JSON.parse(raw);
}

async function handleFiles(files) {
  if (!state.ready) {
    toast("The converter is still starting — try again in a moment");
    return;
  }
  const accepted = Array.from(files).filter((file) =>
    /\.(kicad_sch|zip)$/i.test(file.name));
  if (!accepted.length) {
    showFailure("That is not a KiCad schematic.",
                "Drop a .kicad_sch file (KiCad 6 or newer), or a .zip of the "
                + "project folder. Legacy .sch files from KiCad 5 and earlier "
                + "are a different format — open and re-save the project in a "
                + "current KiCad first.");
    return;
  }

  clearWorkDir();
  for (const file of accepted) {
    await writeUpload(file);
  }
  expandArchives();

  const schematics = listSchematics();
  if (!schematics.length) {
    showFailure("No schematic inside that archive.",
                "The .zip contained no .kicad_sch file.");
    return;
  }

  const root = state.driver.root_sheet(schematics);
  state.source = { path: root, fileCount: schematics.length };
  runConversion();
}

function runConversion(keepView = false) {
  const { path, fileCount } = state.source;
  let payload;
  try {
    payload = JSON.parse(
      state.driver.convert(path, state.junctionDots, state.showLoops,
                           state.showUnits));
  } catch (error) {
    console.error(error);
    showFailure("The converter stopped unexpectedly.", String(error));
    return;
  }

  if (!payload.ok) {
    showFailure("That file could not be read.", payload.error);
    return;
  }

  state.result = payload;
  state.result.extraFiles = Math.max(0, fileCount - 1);
  state.contents = {
    tex: payload.tex,
    spice: payload.netlists.spice,
    kicad: payload.netlists.kicad,
    text: payload.netlists.text,
    csv: payload.netlists.csv,
    alt: `${payload.descriptions.detailed}\n`,
  };
  if (!keepView) state.tab = "preview";
  if (state.latex.url) URL.revokeObjectURL(state.latex.url);
  state.latex = { status: "idle", pdf: null, url: null, error: "" };
  showWorkspace();
  startLatexRender();
}

async function startLatexRender() {
  const result = state.result;
  if (!result) return;
  state.latex = { status: "running", pdf: null, url: null, error: "" };
  renderOptions();
  if (state.tab === "preview") renderPanel();
  try {
    const pdf = await renderPdf(result.tex, () => state.result === result);
    if (pdf === null || state.result !== result) return;
    const url = URL.createObjectURL(
      new Blob([pdf], { type: "application/pdf" }));
    state.latex = { status: "done", pdf, url, error: "" };
  } catch (error) {
    if (state.result !== result) return;
    console.error(error);
    state.latex = {
      status: "failed", pdf: null, url: null,
      error: error && error.message ? error.message : String(error),
    };
  }
  renderFileList();
  renderOptions();
  if (state.tab === "preview") renderPanel();
}

function showFailure(headline, detail) {
  state.result = null;
  els.uploadView.hidden = false;
  els.workspaceView.hidden = true;
  els.filechip.hidden = true;
  els.reset.hidden = true;
  els.tagline.hidden = false;
  setBoot(`${headline} ${detail || ""}`.trim(), "failed");
  els.boot.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showWorkspace() {
  const result = state.result;
  els.uploadView.hidden = true;
  els.workspaceView.hidden = false;
  els.filechip.hidden = false;
  els.reset.hidden = false;
  els.tagline.hidden = true;
  els.filename.textContent = result.source;
  document.title = `${result.source} — KiCad to Netlist`;

  renderStatus();
  renderFileList();
  renderOptions();
  renderPanel();
}

function renderOptions() {
  els.junctionDots.checked = state.junctionDots;
  els.showLoops.checked = state.showLoops;
  els.showUnits.checked = state.showUnits;
  const loops = state.result && state.showLoops ? state.result.loops : null;
  if (loops && loops.drawn.length) {
    els.loopsNote.textContent =
      `Drawn: ${loops.drawn.join(", ")}.`;
  } else if (loops) {
    els.loopsNote.textContent = `Not drawn for this circuit: ${loops.reason}.`;
  }
  els.loopsNote.hidden = !loops;
}

function allNotes() {
  const result = state.result;
  const notes = [];
  for (const text of result.warnings) {
    notes.push({ kind: "warn", text });
  }
  const seen = new Set();
  for (const format of Object.keys(result.netlist_notes)) {
    for (const text of result.netlist_notes[format]) {
      if (seen.has(text)) continue;
      seen.add(text);
      notes.push({ kind: "info", text, format });
    }
  }
  return notes;
}

function renderStatus() {
  const { stats } = state.result;
  const notes = allNotes();
  const clean = stats.fallbacks.length === 0 && stats.undescribed.length === 0;

  els.statusIcon.innerHTML = clean ? ICONS.tick : ICONS.warn;
  els.statusbar.className = `statusbar${clean ? "" : " is-warn"}`;

  const bits = [];
  if (clean) {
    bits.push(`<strong>All ${plural(stats.components, "component", "components")} converted.</strong>`);
  } else if (stats.fallbacks.length) {
    bits.push(`<strong>${stats.drawn} of ${stats.components} components `
              + `drawn as CircuiTikZ symbols.</strong>`);
  } else {
    bits.push(`<strong>${stats.described} of ${stats.components} components `
              + `described.</strong>`);
  }
  bits.push(`${plural(stats.nodes, "node", "nodes")}, `
            + `${plural(stats.nets, "net", "nets")} — read, drawn and `
            + `described in ${duration(stats.convert_ms)}.`);
  if (state.result.extraFiles) {
    bits.push(`${plural(state.result.extraFiles, "other schematic file",
                        "other schematic files")} came with it; `
              + `sub-sheets are merged automatically.`);
  }
  els.statusText.innerHTML = bits.join(" ");

  els.notesCount.hidden = notes.length === 0;
  els.notesCountText.textContent = plural(notes.length, "note", "notes");
}

function latexFileEntry() {
  const done = state.latex.status === "done" && state.latex.pdf;
  return {
    key: "pdf",
    name: `${state.result.stem}.pdf`,
    note: done ? "CircuiTikZ rendering"
      : state.latex.status === "failed" ? "LaTeX render failed"
        : "rendering…",
    bytes: done ? state.latex.pdf.length : 0,
    binary: true,
    mime: "application/pdf",
    pending: !done,
  };
}

function renderFileList() {
  const files = [latexFileEntry()].concat(state.result.files);
  els.fileCount.textContent = plural(files.length, "file", "files");
  els.fileList.innerHTML = files.map((file) => {
    const ext = file.name.split(".").pop();
    return `<div class="filerow">
      <span class="ext">${esc(ext)}</span>
      <span class="meta">
        <span class="n" title="${esc(file.name)}">${esc(file.name)}</span>
        <span class="s">${esc(file.note)}${file.bytes
          ? " · " + formatBytes(file.bytes) : ""}</span>
      </span>
      <button class="icon-btn" data-download="${esc(file.key)}"
              ${file.pending ? "disabled" : ""}
              aria-label="Download ${esc(file.name)}">${ICONS.download}</button>
    </div>`;
  }).join("");
}

function toolbar(left, right) {
  return `<div class="panel-toolbar">
    <div class="toolbar-group">${left}</div>
    <div class="toolbar-group">${right}</div>
  </div>`;
}

function segmented(name, options, active) {
  return `<div class="segmented" role="group">` + options.map(([key, label]) =>
    `<button data-${name}="${esc(key)}" aria-pressed="${key === active}">`
    + `${esc(label)}</button>`).join("") + `</div>`;
}

function codeBlock(text) {
  const lines = text.replace(/\n+$/, "").split("\n");
  const gutter = lines.map((_line, index) => index + 1).join("\n");
  return `<div class="code"><div class="gutter" aria-hidden="true">${gutter}</div>`
    + `<pre>${esc(lines.join("\n"))}</pre></div>`;
}

function noteStrip(notes) {
  if (!notes.length) return "";
  return `<div class="strip">${ICONS.noteWarn}<ul>`
    + notes.map((text) => `<li>${esc(text)}</li>`).join("")
    + `</ul></div>`;
}

function copyButton(key) {
  return `<button class="btn btn-small" data-copy="${esc(key)}">`
    + `${ICONS.copy}Copy</button>`;
}

function downloadButton(key, label) {
  return `<button class="btn btn-small" data-download="${esc(key)}">`
    + `${esc(label)}</button>`;
}

function renderPanel() {
  const result = state.result;
  els.tabs.forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === state.tab));
  });
  els.panel.setAttribute("aria-labelledby", `tab-${state.tab}`);

  if (state.tab === "preview") {
    const latex = state.latex;
    if (latex.status === "done") {
      els.panel.innerHTML =
        `<div class="panel-body"><iframe class="pdf-view" title="CircuiTikZ rendering"
            src="${latex.url}#toolbar=0&navpanes=0"></iframe></div>`
        + `<div class="panel-foot"><span>Rendered by pdflatex and
           circuitikz in this tab &mdash; the same PDF the desktop program
           produces.</span></div>`;
      return;
    }
    if (latex.status === "failed") {
      els.panel.innerHTML =
        `<div class="panel-body"><div class="rendering">
           <p><strong>LaTeX could not render this schematic.</strong></p>
           <p class="rendering-note">${esc(latex.error)}</p>
           <p class="rendering-note">The .tex, the netlists and the
           description are unaffected &mdash; they are on the other tabs
           and in the downloads.</p>
         </div></div>`;
      return;
    }
    els.panel.innerHTML =
      `<div class="panel-body"><div class="rendering">
         <span class="spinner" aria-hidden="true"></span>
         <p><strong>Rendering with LaTeX…</strong></p>
         <p class="rendering-note">pdfTeX and circuitikz are running in
         this tab. The first schematic of a session takes about a minute
         while the TeX packages load; later ones are quicker.</p>
       </div></div>`;
    return;
  }

  if (state.tab === "latex") {
    els.panel.innerHTML =
      toolbar(
        `<span class="toolbar-label">Standalone document ·
         <span class="mono" style="font-size:12.5px">circuitikz</span> ·
         compiles with pdflatex</span>`,
        copyButton("tex") + downloadButton("tex", "Download .tex"))
      + `<div class="panel-body">${codeBlock(result.tex)}</div>`;
    return;
  }

  if (state.tab === "netlist") {
    const format = state.netlistFormat;
    const meta = NETLIST_TABS.find(([key]) => key === format);
    const notes = result.netlist_notes[format] || [];
    els.panel.innerHTML =
      toolbar(
        `<span class="toolbar-label">Format</span>`
        + segmented("netlist", NETLIST_TABS.map(([k, l]) => [k, l]), format),
        copyButton(format)
        + downloadButton(format, `Download ${meta[2].startsWith("_") ? meta[2].slice(1) : meta[2]}`))
      + `<div class="panel-body">${codeBlock(result.netlists[format])}</div>`
      + noteStrip(notes);
    return;
  }

  if (state.tab === "description") {
    const text = result.descriptions[state.detail];
    const prose = text.split("\n").filter((line) => line.trim()).map((line) =>
      line.trim().endsWith(":")
        ? `<h3>${esc(line)}</h3>`
        : `<p>${esc(line)}</p>`).join("");
    els.panel.innerHTML =
      toolbar(
        `<span class="toolbar-label">Detail</span>`
        + segmented("detail", DETAIL_LEVELS, state.detail),
        copyButton(`description:${state.detail}`)
        + downloadButton(`description:${state.detail}`, "Download .txt"))
      + `<div class="panel-body"><div class="prose">${prose}</div></div>`
      + `<div class="panel-foot"><span>Written as ordinary sentences, one per
         line, so a screen reader announces it without punctuation noise.
         Every component in the schematic appears here.</span></div>`;
    return;
  }

  renderReport();
}

function renderReport() {
  const { stats } = state.result;
  const notes = allNotes();
  const schematicNotes = notes.filter((note) => note.kind === "warn");
  const netlistNotes = notes.filter((note) => note.kind === "info");

  const stat = (n, l) =>
    `<div class="stat"><span class="n">${esc(n)}</span>`
    + `<span class="l">${esc(l)}</span></div>`;

  const noteItem = (note) =>
    `<div class="note${note.kind === "warn" ? " warn" : ""}">`
    + (note.kind === "warn" ? ICONS.noteWarn : ICONS.info)
    + `<span class="body"><span class="d">${esc(note.text)}</span></span></div>`;

  const fallbackLine = stats.fallbacks.length
    ? `<p style="margin:10px 2px 0;font-size:12.5px;color:var(--ink-faint)">`
      + `Drawn as labelled boxes because no CircuiTikZ symbol matched: `
      + `<span class="mono">${esc(stats.fallbacks.join(", "))}</span>. `
      + `Their pins still land in the right places, so the wiring is correct.</p>`
    : `<p style="margin:10px 2px 0;font-size:12.5px;color:var(--ink-faint)">`
      + `Nothing fell back to a plain labelled box — every symbol had a `
      + `dedicated CircuiTikZ element. The same counts are written into the `
      + `header of the .tex and .cir files.</p>`;

  els.panel.innerHTML = `<div class="panel-body"><div class="report">
    <div>
      <h3>What came through</h3>
      <div class="stats">
        ${stat(`${stats.drawn} / ${stats.components}`, "components drawn")}
        ${stat(`${stats.described} / ${stats.components}`, "described in prose")}
        ${stat(stats.nodes, "nodes")}
        ${stat(stats.nets, "nets in total")}
        ${stat(stats.symbols, "symbols placed")}
        ${stat(duration(stats.convert_ms), "read, draw, describe")}
      </div>
      ${fallbackLine}
    </div>

    <div>
      <h3>Notes from the schematic <span class="count">— ${schematicNotes.length}</span></h3>
      ${schematicNotes.length
        ? `<div class="note-list">${schematicNotes.map(noteItem).join("")}</div>`
        : `<div class="empty-note">${ICONS.info}<span>KiCad's connectivity
             rules were applied without complaint: every pin landed on a net,
             and no symbol was missing a library definition.</span></div>`}
    </div>

    <div>
      <h3>Notes from the netlist <span class="count">— ${netlistNotes.length}</span></h3>
      ${netlistNotes.length
        ? `<div class="note-list">${netlistNotes.map(noteItem).join("")}</div>`
        : `<div class="empty-note">${ICONS.info}<span>Every component had a
             direct SPICE equivalent and a readable value. Nothing was left as
             a comment in the deck.</span></div>`}
    </div>

    <div>
      <h3>The conversion, line by line</h3>
      <div class="empty-note" style="display:block">
        ${state.result.summary.map((line) =>
          `<div style="font-size:13.2px;line-height:1.7">${esc(line)}</div>`).join("")}
      </div>
    </div>
  </div></div>`;
}

function contentFor(key) {
  if (key.startsWith("description:")) {
    return `${state.result.descriptions[key.split(":")[1]]}\n`;
  }
  return state.contents[key];
}

function fileEntry(key) {
  if (key === "pdf") return latexFileEntry();
  return state.result.files.find((file) => file.key === key);
}

function toBytes(value) {
  const data = value && value.toJs ? value.toJs() : value;
  if (value && value.destroy) value.destroy();
  return data;
}

function download(key) {
  if (key.startsWith("description:")) {
    const level = key.split(":")[1];
    saveFile(`${state.result.stem}_alt_text_${level}.txt`,
             contentFor(key), "text/plain");
    return;
  }
  const entry = fileEntry(key);
  if (!entry) return;
  if (entry.binary) {
    if (!state.latex.pdf) return;
    saveFile(entry.name,
             new Blob([state.latex.pdf], { type: entry.mime }));
    return;
  }
  saveFile(entry.name, contentFor(key), entry.mime);
}

async function downloadEverything() {
  const data = toBytes(state.driver.build_zip(state.latex.pdf || undefined));
  saveFile(`${state.result.stem}_schemaccess.zip`,
           new Blob([data], { type: "application/zip" }));
}

els.choose.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", (event) => {
  handleFiles(event.target.files);
  event.target.value = "";
});

let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  els.dropzone.classList.add("is-over");
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) els.dropzone.classList.remove("is-over");
});
window.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  els.dropzone.classList.remove("is-over");
  if (event.dataTransfer && event.dataTransfer.files.length) {
    handleFiles(event.dataTransfer.files);
  }
});

els.reset.addEventListener("click", () => {
  state.result = null;
  els.uploadView.hidden = false;
  els.workspaceView.hidden = true;
  els.filechip.hidden = true;
  els.reset.hidden = true;
  els.tagline.hidden = false;
  document.title = "KiCad to Netlist";
  setBoot("", "ready");
});

els.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    state.tab = tab.dataset.tab;
    renderPanel();
  });
  tab.addEventListener("keydown", (event) => {
    const tabs = Array.from(els.tabs);
    const index = tabs.indexOf(tab);
    let next = null;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    next.focus();
    state.tab = next.dataset.tab;
    renderPanel();
  });
});

els.openReport.addEventListener("click", (event) => {
  event.preventDefault();
  state.tab = "report";
  renderPanel();
  $("tab-report").focus();
});

els.panel.addEventListener("click", (event) => {
  const target = event.target.closest("[data-copy], [data-download], "
                                      + "[data-netlist], [data-detail]");
  if (!target) return;
  if (target.dataset.copy !== undefined) {
    copyText(contentFor(target.dataset.copy));
  } else if (target.dataset.download !== undefined) {
    download(target.dataset.download);
  } else if (target.dataset.netlist !== undefined) {
    state.netlistFormat = target.dataset.netlist;
    renderPanel();
  } else if (target.dataset.detail !== undefined) {
    state.detail = target.dataset.detail;
    renderPanel();
  }
});

els.fileList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-download]");
  if (button) download(button.dataset.download);
});

els.junctionDots.addEventListener("change", () => {
  state.junctionDots = els.junctionDots.checked;
  if (!state.result) return;
  runConversion(true);
});

els.showLoops.addEventListener("change", () => {
  state.showLoops = els.showLoops.checked;
  if (!state.result) return;
  runConversion(true);
});

els.showUnits.addEventListener("change", () => {
  state.showUnits = els.showUnits.checked;
  if (state.result) runConversion(true);
});

els.downloadAll.addEventListener("click", downloadEverything);

els.overleaf.addEventListener("click", () => {
  if (!state.result) return;
  els.overleafSnip.value = state.result.tex;
  els.overleafForm.submit();
});

boot();
