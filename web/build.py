"""Builds the browser version.

Two archives come out of this:

* ``web/schemaccess.zip`` - the library and ``driver.py``, which the page
  unpacks into Pyodide;
* ``kicad-to-netlist-deploy.zip`` at the repository root - the whole
  ``web`` folder with its contents at the top level, ready to drag onto
  a static host.

The deploy archive is built here rather than by hand because it had
drifted: the copy in the repository was months behind the folder it is
supposed to mirror, so deploying it would have shipped a LaTeX bundle
with packages missing and a stale copy of the library.

Both archives are written with a fixed timestamp, so a build with no
source change produces a byte-identical file and leaves git quiet.
"""

from __future__ import annotations

import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "src", "schemaccess")
DRIVER = os.path.join(HERE, "driver.py")
ARCHIVE = os.path.join(HERE, "schemaccess.zip")
DEPLOY = os.path.join(ROOT, "kicad-to-netlist-deploy.zip")
EXAMPLES = os.path.join(HERE, "examples")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

#: Left out of the deploy archive: caches, and anything a test or a
#: probe dropped in the folder.
SKIP_DIRS = {"__pycache__", ".history"}

EXAMPLE_FILES = ()

_STAMP = (1980, 1, 1, 0, 0, 0)


def _add(archive: zipfile.ZipFile, source: str, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname, date_time=_STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(source, "rb") as handle:
        archive.writestr(info, handle.read())


def build() -> None:
    names = []
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED) as archive:
        for dirpath, dirnames, filenames in os.walk(PACKAGE):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in ("__pycache__", "gui"))
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, os.path.dirname(PACKAGE))
                arcname = relative.replace(os.sep, "/")
                _add(archive, full, arcname)
                names.append(arcname)
        _add(archive, DRIVER, "driver.py")
        names.append("driver.py")

    os.makedirs(EXAMPLES, exist_ok=True)
    copied = []
    for name in EXAMPLE_FILES:
        source = os.path.join(FIXTURES, name)
        if os.path.exists(source):
            shutil.copyfile(source, os.path.join(EXAMPLES, name))
            copied.append(name)

    size = os.path.getsize(ARCHIVE)
    print(f"wrote {os.path.relpath(ARCHIVE, ROOT)} "
          f"({size / 1024:.1f} KB, {len(names)} modules)")
    for name in names:
        print(f"  {name}")
    print(f"examples: {', '.join(copied) if copied else 'none found'}")


def deploy() -> None:
    """Zip the whole web folder, contents at the top level.

    That shape is what a static host wants: unzipped, ``index.html`` has
    to sit at the site root, not inside a ``web/`` directory.
    """
    count = 0
    with zipfile.ZipFile(DEPLOY, "w", zipfile.ZIP_DEFLATED) as archive:
        for dirpath, dirnames, filenames in os.walk(HERE):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                arcname = os.path.relpath(full, HERE).replace(os.sep, "/")
                _add(archive, full, arcname)
                count += 1
    size = os.path.getsize(DEPLOY)
    print(f"wrote {os.path.relpath(DEPLOY, ROOT)} "
          f"({size / (1024 * 1024):.1f} MB, {count} files)")


if __name__ == "__main__":
    build()
    deploy()
