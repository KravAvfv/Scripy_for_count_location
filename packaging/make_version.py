"""Write packaging/version_info.txt (Windows VERSIONINFO resource) from sitesizer.__version__."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sitesizer import __version__  # noqa: E402

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({t}), prodvers=({t}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('042204B0', [
      StringStruct('CompanyName', 'LocalCount'),
      StringStruct('FileDescription', 'LocalCount — Fortinet location sizing'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'LocalCount'),
      StringStruct('OriginalFilename', 'LocalCount.exe'),
      StringStruct('ProductName', 'LocalCount'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x0422, 1200])])
  ]
)
"""


def main() -> None:
    parts = [int(x) for x in __version__.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    out = ROOT / "packaging" / "version_info.txt"
    out.write_text(TEMPLATE.format(t=", ".join(map(str, parts)), v=__version__), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
