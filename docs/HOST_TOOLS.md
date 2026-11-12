# Compatible EROFS host tools

The DSU build uses `mkfs.erofs --tar=f` with LZ4HC compression and
`fsck.erofs --extract=DIR --no-preserve`. **erofs-utils 1.7.1 is tested.** The guide
checks these capabilities by building and extracting a tiny image before
unlocking. The standalone builder repeats the check, including on resumed runs.

Older `fsck.erofs` versions use `--extract` only to check encoded file data.
They reject a destination argument before reading the image. Directory extraction
was added in [erofs-utils 1.5](https://lkml.iu.edu/2206.1/05030.html), and tar input
in [1.7](https://lkml.iu.edu/2309.2/05170.html). Removing the destination argument
would leave the files needed by the builder unextracted.

Check which tools your terminal selects:

```sh
command -v fsck.erofs mkfs.erofs
fsck.erofs -V
```

Upgrade both tools to a compatible package from your distribution. If its package
is too old, build the tested release in your home directory using the
[upstream build procedure](https://erofs.docs.kernel.org/en/latest/install.html).
For Debian/Ubuntu:

```sh
sudo apt-get install build-essential git autoconf automake libtool pkg-config liblz4-dev uuid-dev
mkdir -p "$HOME/.cache/onn2026-host-tools"
git clone --depth 1 --branch v1.7.1 https://git.kernel.org/pub/scm/linux/kernel/git/xiang/erofs-utils.git "$HOME/.cache/onn2026-host-tools/erofs-utils-1.7.1"
cd "$HOME/.cache/onn2026-host-tools/erofs-utils-1.7.1"
./autogen.sh
./configure --prefix="$HOME/.local/onn2026-erofs-1.7.1" --enable-lz4
make -j2
make install
export PATH="$HOME/.local/onn2026-erofs-1.7.1/bin:$PATH"
fsck.erofs -V
```

If you already cloned that release, use the existing source folder. Check that
each build command succeeds before continuing. `make install` uses the home
directory prefix above and needs no `sudo`.

In that same terminal, return to the installer checkout and rerun
`python3 onn2026.py`. Keep its existing `work/` folder and select the same onn;
the guide resumes the build using its saved unlock and patch records. Export
the same `PATH` again if you open a new terminal. If you previously selected
explicit tool paths, those saved paths must point to compatible binaries too.
