"""Writes a PyInstaller version resource so the exe's file properties show the app version."""

import sys

from version import APP_NAME, __version__

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple}, prodvers={tuple}),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('ProductName', '{name}'),
        StringStruct('FileDescription', '{name}'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('OriginalFilename', 'AD-Multitool.exe'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main(output_path):
    parts = [int(part) for part in __version__.split(".")]
    version_tuple = tuple((parts + [0, 0, 0, 0])[:4])
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(tuple=version_tuple, name=APP_NAME, version=__version__))


if __name__ == "__main__":
    main(sys.argv[1])
