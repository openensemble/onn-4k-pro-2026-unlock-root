#!/usr/bin/env python3
"""Rebuild helpers into ignored work/, leaving reviewed binaries untouched."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ndk', required=True, type=Path, help='Android NDK r28c directory')
    args = parser.parse_args()
    compiler = args.ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang'
    (ROOT / 'work').mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='native-rebuild-', dir=ROOT / 'work'))
    manifest = json.loads((ROOT / 'NATIVE_SHA256.json').read_text())
    results = {}
    for name in ('read_state', 'unlock_once'):
        command = [str(compiler), '-std=c11', '-D_GNU_SOURCE', '-O2', '-Wall', '-Wextra',
                   '-Werror', '-fPIE', '-pie', str(ROOT / 'native' / (name + '.c')),
                   '-o', str(output / name)]
        subprocess.run(command, check=True)
        sha = hashlib.sha256((output / name).read_bytes()).hexdigest()
        results[name] = {'sha256': sha, 'matches_reviewed_binary': sha == manifest['bin/armeabi-v7a/' + name]}
    (output / 'build.json').write_text(json.dumps(results, indent=2) + '\n')
    print(output)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
