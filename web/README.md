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
| Show loop currents | Draws the mesh-analysis loop currents (i1, i2, ...) and adds them to the description |
| Show units on values | Writes units after values on the drawing (`1` → `1 H`, `22n` → `22 nF`) |

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

## Notes

- The first visit downloads the Python runtime (about 10 MB) and the LaTeX
  engine (about 22 MB). Both are cached after that.
- The first drawing takes about a minute to render; later ones are faster.
- For hierarchical projects, upload every sheet or a `.zip` of the project.
- KiCad 6 or newer is required. Open and re-save older projects first.
