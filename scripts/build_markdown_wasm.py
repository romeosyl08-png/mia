"""Build the vendored Wasm asset, or check that it matches its source manifest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "rust" / "markdown"
ASSET = ROOT / "static" / "app" / "wasm" / "markdown_core.wasm"
MANIFEST = ASSET.with_suffix(".json")
NOTICES = ASSET.with_name("THIRD_PARTY_LICENSES.txt")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest():
    sources = [Path(__file__).resolve()]
    sources.extend(CRATE / name for name in ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml"])
    sources.extend(sorted((CRATE / "src").rglob("*.rs")))
    return {
        "sources": {str(path.relative_to(ROOT)): digest(path) for path in sources},
        "wasm_sha256": digest(ASSET),
        "notices_sha256": digest(NOTICES),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check source and asset hashes without requiring Rust")
    args = parser.parse_args()
    if args.check:
        if not all(path.is_file() for path in [ASSET, MANIFEST, NOTICES]) or json.loads(MANIFEST.read_text()) != manifest():
            raise SystemExit("Markdown Wasm is missing or stale; run python scripts/build_markdown_wasm.py")
        print("Markdown Wasm matches its Rust sources.")
        return
    cargo = shutil.which("cargo")
    if not cargo:
        raise SystemExit("Install Rust/rustup first; rust/markdown/rust-toolchain.toml pins the toolchain.")
    subprocess.run([cargo, "build", "--locked", "--release", "--target", "wasm32-unknown-unknown"], cwd=CRATE, check=True)
    target = Path(os.environ.get("CARGO_TARGET_DIR", CRATE / "target"))
    if not target.is_absolute():
        target = CRATE / target
    built = target / "wasm32-unknown-unknown" / "release" / "ai2ai_markdown.wasm"
    ASSET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, ASSET)
    metadata = json.loads(subprocess.check_output(
        [cargo, "metadata", "--locked", "--format-version", "1"], cwd=CRATE, text=True,
    ))
    notices = ["Third-party Rust dependencies of ai2ai-markdown.\n"]
    for package in sorted(metadata['packages'], key=lambda item: item['name']):
        if not package['source']:
            continue
        directory = Path(package['manifest_path']).parent
        notices.append(f"\n{'=' * 72}\n{package['name']} {package['version']} ({package['license']})\n")
        licenses = sorted(path for path in directory.iterdir() if path.name.upper().startswith(('LICENSE', 'COPYING')) and path.is_file())
        if not licenses:
            raise SystemExit(f"Missing license text for {package['name']}")
        for license_path in licenses:
            notices.append(f"\n{license_path.name}\n{license_path.read_text()}\n")
    NOTICES.write_text(''.join(notices))
    MANIFEST.write_text(json.dumps(manifest(), indent=2) + "\n")
    print(f"Built {ASSET.relative_to(ROOT)} ({ASSET.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
