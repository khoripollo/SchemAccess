/* Compiles the CircuiTikZ .tex to PDF with pdfTeX in WebAssembly. */

const LATEX_DIR = "latex/";

let enginePromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = src;
    tag.onload = () => resolve();
    tag.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(tag);
  });
}

async function engine() {
  if (!enginePromise) {
    enginePromise = (async () => {
      await loadScript(`${LATEX_DIR}PdfTeXEngine.js`);
      // eslint-disable-next-line no-undef
      const instance = new exports.PdfTeXEngine();
      await instance.loadEngine();
      instance.setTexliveEndpoint(
        new URL(LATEX_DIR, location.href).href);
      return instance;
    })().catch((error) => {
      enginePromise = null;
      throw error;
    });
  }
  return enginePromise;
}

let queue = Promise.resolve();

async function compile(tex) {
  const instance = await engine();
  instance.writeMemFSFile("main.tex", tex);
  instance.setEngineMainFile("main.tex");
  const result = await instance.compileLaTeX();
  if (!result.pdf) {
    const error = new Error("LaTeX did not produce a PDF");
    error.texLog = result.log || "";
    throw error;
  }
  return new Uint8Array(result.pdf);
}

export function renderPdf(tex, stillWanted) {
  const run = queue.then(
    () => (stillWanted && !stillWanted() ? null : compile(tex)));
  queue = run.then(() => {}, () => {});
  return run;
}

export function isWarm() {
  return enginePromise !== null;
}
