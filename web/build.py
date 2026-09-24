"""Packs src/schemaccess and driver.py into web/schemaccess.zip."""

from __future__ import annotations

import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "src", "schemaccess")
DRIVER = os.path.join(HERE, "driver.py")
ARCHIVE = os.path.join(HERE, "schemaccess.zip")
EXAMPLES = os.path.join(HERE, "examples")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")

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


if __name__ == "__main__":
    build()
