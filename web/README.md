# SchemAccess Web

The browser version of SchemAccess. Upload a KiCad `.kicad_sch` file and
download the CircuiTikZ LaTeX, the rendered PDF, the netlists and the
screen-reader description. Everything runs in the browser, so schematics
are never uploaded to a server.

## Running locally

```
python web/build.py
python -m http.server 8765 --directory web
```

Then open <http://localhost:8765>. The page has to be served over HTTP;
opening `index.html` directly from the file system does not work.

## Deploying

The `web` folder is a static site with no build step. To deploy on
Netlify:

```
netlify deploy --dir=web --prod
```

or drag the `web` folder onto <https://app.netlify.com/drop>. Any static
host (GitHub Pages, a department web server) works the same way.

Run `python web/build.py` again after any change under `src/schemaccess`,
so the site uses the current version of the library.

## Options

| Option | Effect |
| --- | --- |
| Show junction dots | Draws a dot where three or more wires meet, as KiCad does |
| Show loop currents | Draws the mesh-analysis loop currents (I1, I2, ...) and adds them to the description |
| Switch style | Draws a switch as it is on the sheet, or forced to closing / opening |

Values are always drawn with their unit — a resistor marked `100` is
`100 Ω`, the same way `10m` on an inductor is `10 mH`. This used to be a
checkbox; it is now what the desktop program does too.

## Outputs

| File | Contents |
| --- | --- |
| `<name>.pdf` | The CircuiTikZ drawing, compiled in the browser |
| `<name>.tex` | Standalone CircuiTikZ document |
| `<name>.cir` | SPICE deck for ngspice or LTspice |
| `<name>.net` | KiCad netlist |
| `<name>_netlist.txt` | Readable net table |
| `<name>_netlist.csv` | One row per pin |
| `<name>_alt_text.txt` | Screen-reader description |

## Files

| File | Purpose |
| --- | --- |
| `index.html`, `app.js`, `styles.css` | The page |
| `driver.py` | Runs the conversion inside the page |
| `latex.js`, `latex/` | pdfTeX and the TeX files used to render the PDF |
| `build.py` | Builds `schemaccess.zip` from `src/schemaccess` |
| `netlify.toml` | Hosting settings |

## The TeX mirror

`latex/tex/` is the whole TeX installation the browser gets. pdfTeX in
WebAssembly has no file system behind it: it fetches each file it needs
over HTTP from a *texlive endpoint*, and `latex.js` points that endpoint
at this folder rather than at `texlive2.swiftlatex.com`, so the site
works offline and asks a third party for nothing.

That makes the folder a flat mirror, keyed by the exact name pdfTeX
requests:

- packages and classes keep their extension — `xstring.sty`,
  `standalone.cls`;
- **font metrics drop theirs** — the engine asks for `cmex7`, not
  `cmex7.tfm`, so the file is called `cmex7`;
- Type 1 outlines keep `.pfb`, and are what embeds the glyphs in the PDF.

A file that is not here is a 404, and the compile stops with nothing to
show but *"LaTeX did not produce a PDF"*. To find what a new symbol or
preamble change needs, compile the `.tex` locally with the recorder and
read the list of files it opened:

```bash
pdflatex -recorder -interaction=nonstopmode drawing.tex
```

Every `INPUT` line in `drawing.fls` outside the working directory has to
have a counterpart here. `tests/test_web_bundle.py` does exactly that
over a spread of fixtures and fails if one is absent, so the mirror
cannot quietly fall behind the generator again.

## Notes

- The first visit downloads the Python runtime (about 10 MB) and the LaTeX
  engine (about 22 MB). Both are cached after that.
- The first drawing takes about a minute to render; later ones are faster.
- For hierarchical projects, upload every sheet or a `.zip` of the project.
- KiCad 6 or newer is required. Open and re-save older projects first.
