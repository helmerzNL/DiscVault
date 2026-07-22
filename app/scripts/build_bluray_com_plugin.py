from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app" / "backend" / "next_plugins" / "bluray_com"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def build(output_dir: Path) -> tuple[Path, str]:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"bluray_com_{version}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ("manifest.json", "plugin.py"):
            info = zipfile.ZipInfo(f"bluray_com/{name}", ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (SOURCE / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(
        f"{digest}  {output.name}\n",
        encoding="ascii",
    )
    return output, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "plugins")
    args = parser.parse_args()
    output, digest = build(args.output_dir)
    print(json.dumps({"archive": str(output), "sha256": digest}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
