**If you found this useful, consider donating.** I had to purchase two units at
eBay prices to get this working. I bricked the first one.

If you'd like to send a tip:

- **Ethereum:** `0x994f4605d07049490687c1B64856995723f2d111` (**only on the Ethereum network**)
- **Solana:** `CUDbdRSngRAoky9EyVVoUFH1nTWCgAs23iR9YRWuDZJC` (**only on the Solana network**)
- **Bitcoin:** `3QjXzKrHKqV9QZ98ZU5JHkP1bZfx2q4Y6N` (**only on the Bitcoin network**)

---

# 2026 onn 4K Pro — unlock and permanent root

[**Project website — compatibility, setup and measured results**](https://openensemble.github.io/onn-4k-pro-2026-unlock-root/)

<p align="center">
  <img src="assets/images/onn-4k-pro-2026.png" width="460" alt="2026 onn 4K Pro with elongated fabric-covered body, remote, and blue retail box">
</p>

**2026 onn 4K Pro (`jarvis2`), pictured above.** [Photo: Walmart](https://www.walmart.com/ip/18382213962).

A guided, network-only unlock and root installer for the **2026 onn 4K Pro
(`jarvis2`)**. Run `python3 onn2026.py` and follow the instructions in the terminal.
The guide runs the commands and restarts, keeps track of the files, looks for the
same onn after IP changes, verifies permanent root, and turns off automatic system updates.
A USB data cable or USB drive is not required.

**Measured result, November 10, 2026:** persistent unlock and permanent
**Magisk 30.7** root worked on one replacement unit. Root survived normal Android
boot and Magisk’s additional-setup reboot. The original Android installation
returned, SELinux remained **Enforcing**, the flashed image matched its readback,
and the temporary DSU installation was removed. No temporary-root exploit or
custom startup script was used.

| Supported item | Exact value |
| --- | --- |
| Device | 2026 onn 4K Pro, `jarvis2`, AMLS905X5M / S7D |
| Android | **14**, API **34** |
| Build ID / incremental | **URO3.260203.035.A1 / 15640252** |
| Security patch | **2026-06-01** |
| Bootloader | **01.01.260525.135322** |
| Active slot | **`_b`** |
| Userspace | **ARM32 / `armeabi-v7a`** |
| Root target | **`init_boot_b`**, 8 MiB |

This is support for that **exact build**, not every Android 14 firmware.
Other builds, Android versions, active slots, and the older 2024 `jarvis` are
outside the supported profile. The script checks these values and stops on a
mismatch. Do not edit the profile to bypass a mismatch.

**Unlocking will factory-reset your onn and erase its apps, accounts, settings
and other data. Unlocking or rooting could brick it: it may stop working and
may not be recoverable.** Back up anything you want to keep before starting.

The guide shows **Accept / Decline** buttons for both factory reset/data loss and
possible permanent bricking. Use the arrow keys or Tab to choose, then press
Enter; you do not need to type a confirmation phrase. Decline is selected
initially. The same buttons confirm unlocking and installing root. An
already-unlocked onn skips the unlock step. Acceptance is saved for the selected device.

An independent way to restore a bad root installation has **not been proven**.
A stock backup does not guarantee recovery. Read the [recovery details](docs/RECOVERY.md).

## What you need

- An x86-64 Linux computer with Python 3.10+ and at least **12 GiB free**.
- The supported onn firmware listed above, with at least **8 GiB free**.
- The onn connected to a TV you can watch, its remote, and a steady power supply.
- The computer and onn on the same trusted network.
- Android platform-tools, Android NDK **28.2.13676358**, `erofs-utils`
  (**1.7.1** tested), and `secilc`.

On Debian/Ubuntu, install the host packages with:

```sh
sudo apt-get install git python3 adb iproute2 erofs-utils secilc
```

Install NDK **28.2.13676358** through Android SDK Manager. The guide looks in the
usual SDK locations and asks for its folder if it cannot find it. It checks the
tools before unlocking. The first matching firmware download is about 911 MB.

## Start the guided walkthrough

Open a terminal on your Linux computer. Get the files and start the walkthrough:

```sh
git clone https://github.com/openensemble/onn-4k-pro-2026-unlock-root.git
cd onn-4k-pro-2026-unlock-root
python3 onn2026.py
```

**`python3 onn2026.py` starts the whole walkthrough, from unlocking through root.**

Follow the script's on-screen instructions to complete each step.

When it asks you to open Magisk, use **Settings > Apps > See all apps > Magisk >
Open** on the TV. Magisk may not appear in Google TV's regular app list.
Finish its additional setup and restart, then stay at the TV with the remote
before starting root verification. Select **Grant** for **Shell** when prompted.

If you miss the prompt, Magisk can remember a denial. Open Magisk's **Superuser**
screen and allow **Shell**, or remove only Shell's saved entry to request a fresh
prompt. The guide waits for Enter before the first check and each permission
retry. You can also type `q`, rerun `python3 onn2026.py`, and select the same onn
to resume verification. An existing grant can pass without another TV prompt.

## Leaving and returning

Type **q** at a prompt to leave. To return, run `python3 onn2026.py` again and
select the same onn. Its completed steps and file paths are remembered. During a
critical device operation, Ctrl-C asks the guide to finish and save that step,
then stop before the next one.

If a write or restart has an uncertain result, the guide stops. Restarting the
guide **does not repeat that action**. Keep the onn in its current state and
preserve the saved receipts for inspection. If a completed result was saved
before the terminal closed, the guide can continue after that completed step.
Ordinary connection failures and read-only checks can be checked again.

Keep the repository's `work/` folder. It contains device identities, acceptance,
progress, attempt records and backups. A completed guide reports its last saved
successful result when reopened; that is not a fresh live device check.

## More than one onn, or a changed IP

Use the same guide for multiple supported **2026** units, **one at a time**.
Choose the intended unit from the saved-device list, or choose **Add another onn**.
Each has its own serial, progress, prepared images, backups and attempt records.
Stock downloads can be reused. The older 2024 onn needs its separate installer.

**Enter the IP address once when adding each new onn.** The first connection
does not automatically discover new units. Once saved, the guide can look for
that same onn automatically after an address change. Room names are yours to
choose; leaving the name blank uses the hardware serial.

Before a device action, the guide checks the saved serial, firmware, bootloader
and slot. After an IP change it looks for that same unit. Another onn taking the
old address is rejected, even if its firmware is identical. If discovery cannot
confirm one device, the guide asks you to check Wi-Fi/debugging or enter its new
address; it does not select a different onn automatically.

## Preview and advanced use

For a text overview without contacting any device:

```sh
python3 onn2026.py guide --preview
```

The [advanced command reference](docs/MANUAL.md) retains the individual commands
for inspection and reviewed manual work. Use `python3 onn2026.py --help` for
options such as a custom ADB path. Do not mix manual mutations into an active guide.

## Collect a slot-A diagnostic report

For a compatibility question such as [issue #2](https://github.com/openensemble/onn-4k-pro-2026-unlock-root/issues/2),
run the separate read-only collector. It accepts **slot A or B**, including a
locked 2026 `jarvis2`, without changing the installer's slot-B requirement.
Only Python 3.10+ and Android platform-tools (`adb`) are needed; no root, NDK
or firmware download is required.

Enable debugging on the onn, then replace `ONN_IP` with its IP address:

```sh
python3 tools/slot_report.py --target ONN_IP
```

The default ADB port is 5555; use `--target ONN_IP:PORT` if needed. Accept the
debugging prompt on that onn and rerun if the collector reports unauthorized.
Use `--adb /path/to/adb` for a custom executable. It connects only to the address
you specify and checks the same physical unit, firmware, slot and boot throughout
collection. Optional `--expect-serial SERIAL` also checks the initial physical
identity. Neither the address nor serial appears in the shareable report.

The collector reads firmware and AVB properties, both slots' partition mappings,
accessible partition sizes, available boot-control/update status, and init_boot
hashes when ordinary shell access and the expected partition length permit it.
Permission denials and missing tools are recorded as unavailable information.
An unreadable partition is not evidence that the slot is damaged or unsupported.

Results are saved in a new ignored `work/slot-reports/` directory:

- **`slot-report.txt` or `slot-report.json`:** review and attach one to the issue.
  These contain selected diagnostic facts, with address, serial and boot ID omitted.
- **`private.json`:** raw command receipts containing device identifiers; keep
  this file local. Do not attach the entire output directory.

This script does not request root, install an app, write settings or firmware,
reboot, switch slots, or run the unlock installer. A completed report means the
collection finished; it does **not** establish that slot A is safe to unlock/root
or that the inactive slot contains a usable installation. Leave the firmware
profile and active slot unchanged when collecting a report.

## What was tested

The underlying unlock, DSU return, DSU root console, single init_boot flash,
readback, Magisk setup and subsequent root were measured on one supported unit.
The packaged builder completed a clean build and metadata/content round trip;
its helper’s loaded ELF sections match the tested helper for the same binding.
The packaged permanent-root verifier was also run on that unit.

The host tests cover identity/slot/boot mismatches, prior attempts, enable/reboot
failures, interrupted writes, changed IP addresses, other onns at an old address,
unauthorized devices, ambiguous identities and separate household profiles.
The guide tests also cover required risk acceptance, saving progress, resuming
confirmed results, and refusing to repeat uncertain operations. The new guide,
automatic restarts and reconnect behavior have been tested with simulated device
responses. See the
[validation details and rebuild commands](docs/BUILD.md).

## What was not tested

- Recovery after a power loss or an unbootable modified `init_boot` image.

Passing the tests does not guarantee bug-free behavior or recovery from a failed
installation. See the [recovery limitations](docs/RECOVERY.md).

## Licenses

See [third-party license information](THIRD_PARTY.md).
