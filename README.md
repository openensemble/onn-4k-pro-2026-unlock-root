# 2026 onn 4K Pro — unlock and permanent root

> **This fork adds Canada/ROW (`jarvis2_row`) support.** See the [Canada/ROW section](#canadarow-variant-jarvis2_row) below.
> Original work by [openensemble](https://github.com/openensemble/onn-4k-pro-2026-unlock-root) — full credit to them for discovering the AVB TA unlock method and building the guided installer. This fork extends it.

---

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
**Magisk 30.7** root worked on one replacement unit on **slot B**. Root survived normal Android
boot and Magisk's additional-setup reboot. The original Android installation
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
| Active slot | **`_a` or `_b`**, detected and bound for the entire workflow |
| Userspace | **ARM32 / `armeabi-v7a`** |
| Root target | Active **`init_boot_a`** (`mmcblk0p22`) or **`init_boot_b`** (`mmcblk0p23`), 8 MiB |

This is support for that **exact build**, not every Android 14 firmware.
Other builds, Android versions, and the older 2024 `jarvis` are
outside the supported profile. The script checks these values and stops on a
mismatch. Do not edit the profile to bypass a mismatch.

**Both active slots have successful unlock and root results on the supported
firmware.** The project maintainer confirmed successful community slot-A
installations on November 13, 2026. Slot B has the measured hardware results above.
See [the results and community update](docs/RESULTS.md).

The installer uses the current active slot and never switches slots. Its DSU
helper, backup, flash and verification are bound to that slot and physical unit.
Before flashing, it checks the active partition's mapping, 8 MiB size and exact
pinned stock-image hash, and saves a verified backup. If any check fails it
stops. A slot change also stops the saved workflow; the inactive slot is not
used as a fallback. Existing slot-B connection and installation records remain
valid, including completed or uncertain attempt journals.

**Unlocking will factory-reset your onn and erase its apps, accounts, settings
and other data. Unlocking or rooting could brick it: it may stop working and
may not be recoverable.** Back up anything you want to keep before starting.
After the unlock reboot, **complete the factory reset in Android Recovery on
the TV**, following its on-screen controls to confirm the data wipe. Then finish
Android setup, connect Ethernet or Wi-Fi, and re-enable debugging when prompted.

The guide shows **Accept / Decline** buttons for both factory reset/data loss and
possible permanent bricking. Use the arrow keys or Tab to choose, then press
Enter; you do not need to type a confirmation phrase. Decline is selected
initially. The same buttons confirm unlocking and installing root. An
already-unlocked onn skips the unlock step. Acceptance is saved for the selected device.

An independent way to restore a bad root installation has **not been proven**.
A stock backup does not guarantee recovery. Read the [recovery details](docs/RECOVERY.md).

---

## Canada/ROW variant (`jarvis2_row`)

**Hardware verified on:** JCA2627000951, URO3.260203.009.A2 / 15016191, bootloader 01.01.260304.134947, November 14, 2026.

The Canadian/international onn 4K Pro ships with a `jarvis2_row` firmware variant. It uses
identical hardware to the US model — same Amlogic S905X5M SoC, same partition layout, same
AVB Trusted Application misconfiguration. The guide auto-detects the ROW variant from the
device fingerprint and applies the correct identity values.

| Item | Canada/ROW value |
| --- | --- |
| Device codename | `jarvis2` (same hardware as US) |
| Firmware variant | `jarvis2_row` |
| Build ID / incremental | **URO3.260203.009.A2 / 15016191** |
| Bootloader | **01.01.260304.134947** |
| Serial prefix | **JCA** (US is JUSA) |
| Partition layout | Identical to US (`init_boot_a` = `mmcblk0p22`, `init_boot_b` = `mmcblk0p23`) |
| ABI | `armeabi-v7a` (identical to US) |

### What was verified on hardware

- AVB TA unlock: `secure_lock_state` 1→0, 1 write invocation ✅
- Magisk 30.7 root installed and verified ✅
- Widevine L1 preserved post-unlock ✅
- SELinux: Enforcing ✅
- Automatic OTA updates disabled ✅

### How to run it

The guide works exactly the same as for the US model — no flags or changes needed:

```sh
git clone https://github.com/kushmint/onn-4k-pro-2026-unlock-root.git
cd onn-4k-pro-2026-unlock-root
python3 onn2026.py
```

The guide detects `jarvis2_row` automatically and displays a note explaining
that the US OTA is used as the firmware source (the init_boot image is fully
compatible — both models share the same S905X5M GKI ramdisk).

### Known limitation — root installation path

The ROW variant ships on its latest available firmware with no OTA update available.
The full **network-only DSU path** (the default for US units) uses the US OTA partitions
to build a temporary Android system. The init_boot from that OTA is confirmed compatible
with ROW hardware, but **DSU boot of the US system image on a ROW device has not yet been
hardware-verified**. The guide will attempt this path and display a note if it reaches
the DSU boot step.

If the DSU boot step fails, use the **fastboot alternative** (requires a USB-A cable):

```sh
# After unlock, the guide will have produced a Magisk-patched init_boot.
# Connect the onn via USB-A cable, then:
adb reboot fastboot
# Wait for fastbootd prompt
fastboot flash init_boot_a <path-to-magisk-patched-init_boot.img>
fastboot reboot
# Open Magisk on the TV and complete additional setup
```

The patched image is saved in `work/<session>/patched-init_boot.img` after the
`prepare-root` step completes.

### Help wanted

If you have a Canadian onn on **older ROW firmware** (any build before 15016191), check
for a system update and post the OTA URL here. That URL is the missing piece to
make the network-only DSU path hardware-verified for ROW devices.

---

## What you need

- An x86-64 Linux computer with Python 3.10+ and at least **12 GiB free**.
- The supported onn firmware listed above, with at least **8 GiB free**.
- The onn connected to a TV you can watch, its remote, and a steady power supply.
- The computer and onn on the same trusted network.
- Android platform-tools, Android NDK **28.2.13676358**, `erofs-utils`
  with directory extraction and compressed tar input (**1.7.1** tested), and `secilc`.

On Debian/Ubuntu, install the host packages with:

```sh
sudo apt-get install git python3 adb iproute2 erofs-utils secilc
```

Some distributions supply older `erofs-utils` packages. The guide tests a small
build and extraction before unlocking. If that check fails, follow the
[host-tool upgrade instructions](docs/HOST_TOOLS.md).

Install NDK **28.2.13676358** through Android SDK Manager. The guide looks in the
usual SDK locations and asks for its folder if it cannot find it. It checks the
tools before unlocking. The first matching firmware download is about 911 MB.

## Start the guided walkthrough

Open a terminal on your Linux computer. Get the files and start the walkthrough:

```sh
git clone https://github.com/kushmint/onn-4k-pro-2026-unlock-root.git
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

### Remove temporary leftovers and start again

If an upload or temporary-system installation was interrupted, the guide offers
**Remove temporary system and start again**. You can also choose **R** when
reopening unfinished setup, or request that option directly:

```sh
python3 onn2026.py guide --restart-dsu
```

Select the same onn and review the removal prompt. This requires the supported,
unlocked **original Android** to be running, with any temporary system disabled.
The guide removes the temporary DSU image/data and its `onn26-stock-…raw.gz` /
`onn26-root-…raw.gz` downloads, including incomplete uploads. It then prepares
and uploads fresh images and repeats the two temporary boots. An already-unlocked
onn skips unlocking.

Previous progress, logs, attempt records and backups stay in `work/`. Each restart
uses new temporary-system attempt records; old receipts cannot authorize the new
attempt. A fresh clone can remove device-side leftovers with this option, but
cannot recover records that were deleted. Keep your original work folder.

This option refuses an attempted permanent-root write, an existing permanent-root
installation, a running/enabled guest, or an unresolved reboot on the same boot.
It does not bypass firmware or stock-image checks. If cleanup loses its connection,
rerun the guide to inspect the current state and explicitly confirm recovery again.
See [recovery and failure handling](docs/RECOVERY.md) before changing a device
whose firmware write or reboot is uncertain.

### The two temporary boots

The **first DSU boot is a test** to check that temporary Android starts and that
the onn can return to original Android. Observe the setup screen, skip setup,
sign-in and debugging settings, then follow the prompt to power-cycle once back
into original Android. **Root is not installed during this test.** The guide
checks the original system after it returns, then prepares and starts a second
DSU boot for root installation. ADB inside the test system is not required.

The **second DSU boot is for root installation**: pair the remote if needed,
connect Ethernet or Wi-Fi, and **stay in this system with the power on**. This image
already starts network ADB on **port 5555** with the computer's public key included.
Continue in the guide once the network is connected so it can try that prepared
connection before you open debugging settings or sign in to Google. Google
account setup is not a step in this guide. If it cannot connect, use the
reconnection screen's debugging/pairing instructions as troubleshooting.

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

When the saved address is unavailable, discovery also checks ADB's mDNS
advertisements for a changed wireless connection port and tries the prepared
image's port 5555 at the saved IP. Paired devices are recognized when ADB lists
their connection service name instead of an IP address. Pairing ports are excluded,
and a discovered address must pass the same device checks. If multicast discovery
is unavailable (including some WSL2 networks), enter the **IP:connection-port**
shown on the TV. If pairing is needed, run `adb pair IP:PAIRING_PORT` in another
terminal and enter the code shown on the TV, then use the separate connection port
in the guide. See [Android's wireless ADB instructions](https://developer.android.com/tools/adb#connect-to-a-device-over-wi-fi).

## Preview and advanced use

For a text overview without contacting any device:

```sh
python3 onn2026.py guide --preview
```

The [advanced command reference](docs/MANUAL.md) retains the individual commands
for inspection and reviewed manual work. Use `python3 onn2026.py --help` for
options such as a custom ADB path. Do not mix manual mutations into an active guide.

## If the temporary-system build stops

`build-offline-dsu` prepares the temporary Android images on your computer.
Build time depends on CPU and storage speed. This step now shows its current
stage and compression progress and has a **15-minute overall limit**. A tool
failure stops it immediately. Reaching the limit or pressing Ctrl-C stops the
build and its tools and preserves the logs.

After a failed or cancelled build, run the same guide command from the same
checkout and keep the existing `work/` folder. The guide retries the local build
in a fresh session and reuses the completed unlock and on-device patch records.
It will only stage a temporary system after the new build passes validation.

For a tool failure, the error gives its exit code and the combined
`NNN-build-offline-dsu.log` path. The named tool log under that session's
`dsu-build/` directory contains the tool's detailed output. Existing timeout
receipts from older versions remain available for diagnosis.

If `fsck.erofs` reports that `--extract` does not allow an argument, its version
cannot extract files into a directory. Upgrade the computer's EROFS tools using
[these instructions](docs/HOST_TOOLS.md), then resume the guide.

## Collect a slot-A diagnostic report

Run the separate read-only collector. It accepts **slot A or B**, including a
locked 2026 `jarvis2` or `jarvis2_row`. The report distinguishes slots accepted
by the installer from slots with hardware-verified unlock/root results.
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

**US jarvis2 (openensemble):** The underlying unlock, DSU return, DSU root console,
single init_boot flash, readback, Magisk setup and subsequent root were measured on
one supported unit. The packaged builder completed a clean build and metadata/content
round trip; its helper's loaded ELF sections match the tested helper for the same binding.
The packaged permanent-root verifier was also run on that unit.

**Canada/ROW jarvis2_row (this fork):** AVB TA unlock, Magisk 30.7 root via fastboot
flash of Magisk-patched US init_boot, Widevine L1 preservation, and OTA update
blocking were verified on hardware (JCA2627000951, November 14, 2026). DSU boot of
the US system image on ROW hardware has not yet been hardware-verified.

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
- DSU boot of the US system image on `jarvis2_row` (ROW/Canada) hardware.

Passing the tests does not guarantee bug-free behavior or recovery from a failed
installation. See the [recovery limitations](docs/RECOVERY.md).

## Credits

Original unlock method, guided installer, safety architecture, and all initial
hardware verification by [openensemble](https://github.com/openensemble/onn-4k-pro-2026-unlock-root).
Canada/ROW (`jarvis2_row`) support, slot diagnostic extension, and ROW hardware
verification by [kushmint](https://github.com/kushmint).

## Licenses

See [third-party license information](THIRD_PARTY.md).
