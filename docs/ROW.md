# Canada/ROW support

The Canada/ROW addition comes from [kushmint's PR #4](https://github.com/openensemble/onn-4k-pro-2026-unlock-root/pull/4)
and [network DSU report in issue #5](https://github.com/openensemble/onn-4k-pro-2026-unlock-root/issues/5).
The contributor reports successful unlock, USB fastboot root and network DSU
root on slot A. The guide supports both root methods for this exact identity:

| Field | Canada/ROW value |
| --- | --- |
| Hardware serial prefix | `JCA` |
| Device property | `jarvis2` |
| Fingerprint | `onn/jarvis2_row/jarvis2:14/URO3.260203.009.A2/15016191:user/release-keys` |
| Bootloader | `01.01.260304.134947` |
| Userspace | ARM32, `armeabi-v7a` |
| Active slot A | `init_boot_a`, `/dev/block/mmcblk0p22`, 8 MiB |
| Active slot B | `init_boot_b`, `/dev/block/mmcblk0p23`, 8 MiB |

Both active slots are accepted and remain bound throughout an operation. The
reported ROW hardware result is on **slot A**; ROW slot B has not been separately
confirmed on hardware. The successful US A/B results do not serve as ROW results.
No command switches the active slot. These are contributor hardware reports;
**we have not independently tested either Canadian method on hardware**.

## Use the Canadian menu

Run the normal guide and select your Canadian onn:

```sh
python3 onn2026.py guide
```

Only the exact Canadian identity above gets the **Canada/ROW options** menu:

1. **Network DSU rooting** — recommended, no USB cable. Read the contributor's
   report and testing/recovery limitations, then accept or decline the risks.
2. **USB rooting** — the earlier method, with separate manual flash instructions.
3. **Show status only** — inspect the selected device without starting root setup.

The network route uses the same walkthrough as the US release: unlock only if
needed, patch on this device, build the temporary images, test the first DSU boot
and power-cycle back, then start the second DSU boot for root installation.
The second temporary system starts network ADB on port 5555 with this computer's
public key. Ethernet or Wi-Fi works. No Google account is required for debugging.
The guide verifies original Android and permanent Magisk root after the flash,
preserves the backup, removes its temporary system and disables automatic updates.
It needs the Linux NDK and EROFS tools listed in the [main setup guide](../README.md).
Network progress stays in `guide.json`; choose network DSU again to resume.

The USB walkthrough checks platform-tools, skips unlock if already unlocked,
or performs the existing one-time unlock and recovery-reset sequence if needed.
It downloads the pinned sources, patches the image on this onn and asks again
before showing USB flash instructions bound to its physical serial and active
slot. Run those commands in another terminal, checking each result as instructed.
The guide does not run a USB flash or USB reboot itself. It then checks permanent
root and disables automatic system-update scheduling.

The USB route needs `adb`, `fastboot` and a USB data connection. No NDK or EROFS
build tools are needed. Progress is saved separately in `usb-guide.json`.
After leaving with `q`, run the guide again and choose USB rooting to resume.
Once flash instructions have been shown, resuming proceeds to verification;
the guide does not issue those commands again. Keep the saved instructions and
all receipts, especially after an uncertain result.

US units retain the DSU walkthrough and never receive this menu. The direct
commands below are also available for owners who want to perform each step
manually instead of using the menu.

## Network DSU firmware identity and backups

The approved image source is the pinned **US 15640252 OTA**, downloaded from
Google. It supplies the temporary system and the `init_boot` patched on this onn;
it is not presented as a Canadian stock backup. There is no pinned Canadian OTA
or reference Canadian factory `init_boot` hash in this release.

Canadian Android reports a combined fingerprint while running that temporary
system: `onn/jarvis2_row/jarvis2:14/URO3.260203.035.A1/15640252:user/release-keys`.
The installer accepts that exact value only when Android reports a running DSU.
It keeps the original Canadian fingerprint in the saved binding and still
requires the same physical serial, Canadian bootloader and active slot. Returning
to original Android requires the native 15016191 fingerprint again.

Before flashing, the temporary root console hashes the active `init_boot`, copies
all 8 MiB to the computer and checks the copy against that hash. The flasher
rechecks the live partition against the copy immediately before its single write.
The receipt labels this `pre_flash_backup`, with `factory_stock_verified: false`.
It may already contain earlier modifications; it is not proof of factory stock.
After permanent root is verified, cleanup saves a compressed copy under
`/data/adb/onn26-backups/<snapshot-sha256>/pre-flash-init_boot.img.gz` and retains
the host copy. It does not put the snapshot in Magisk's factory-stock restore
directory. A backup does not establish a working recovery transport.

To abandon an unfinished DSU setup **before a permanent-root attempt**, use:

```sh
python3 onn2026.py guide --restart-dsu
```

This requires original Android and confirms removal before starting a fresh
temporary-system cycle. It keeps receipts and backups and skips unlocking an
already-unlocked unit. It does not erase a root-install attempt or bypass an
already-rooted check. The `--reinstall` flag mentioned in issue #5 is a testing
addition in the contributor's fork, not an option in this script. Use the saved
verification/cleanup flow after a permanent installation.

## Check and save the selected onn

Enable Developer mode, USB debugging and Wi-Fi/network debugging on the TV.
Connect the computer and onn to the same network and authorize this computer.
Use the connection port shown by Android if it differs from 5555.

```sh
ONN26_INITIAL_TARGET="YOUR_ONN_IP:5555"
adb connect "$ONN26_INITIAL_TARGET"
python3 onn2026.py --target "$ONN26_INITIAL_TARGET" status --name "Canada onn"
```

Copy the printed `connection_file` path. Keep the same checkout and `work/`
folder; they hold the physical-device binding and consumed attempt records.

```sh
ONN26_CONNECTION="work/devices/REPLACE_WITH_PRINTED_DEVICE_DIRECTORY/connection.json"
python3 onn2026.py --connection "$ONN26_CONNECTION" status
```

After an address change, use the saved identity:

```sh
python3 onn2026.py --connection "$ONN26_CONNECTION" reconnect --candidate NEW_ONN_IP:5555
```

`reconnect --scan-lan` is also available. Another device at the old address is
rejected. The serial, exact firmware, bootloader and active slot must all match.
For a shareable diagnostic report, use `python3 tools/slot_report.py --help`;
its public report redacts both `JCA` and `JUSA` serials. ROW stock-image matches
are reported as unknown because no reference hash is available.

## Unlock once

Read the [factory-reset warning](../README.md) and [recovery limits](RECOVERY.md).
Unlocking requires a factory reset in Android Recovery and erases all user apps,
accounts, settings and other local data. Manual unlock does not install root.

If status reports `secure_lock_state: 0`, do not repeat the unlock write. If
`unlocked_on_current_boot` is also true, the current Android boot already proves
the unlock. Otherwise inspect the saved result before requesting another restart.
For a locked device, explicitly accept the data loss:

```sh
python3 onn2026.py --connection "$ONN26_CONNECTION" unlock --accept-data-loss
```

Only after the command reports a verified successful write, perform one normal
restart using the TV. Complete the factory reset in Android Recovery, finish
Android setup and enable debugging again. Reconnect using the saved connection
and run `status` to verify the persisted unlock. An uncertain result must be
inspected; rerunning the command cannot repeat its consumed write attempt.

The native unlock protocol is unchanged: it verifies the selected identity and
boot, permits one unlock write and checks the result. The packaged helper is
rebuilt with the project's Android NDK r28c, API 28 and `-O2` settings.

## Prepare the USB root image

The contributor rooted Canadian slot A by patching the **US build 15640252
init_boot** and flashing it through USB fastbootd. This procedure follows that
reported compatibility result. It flashes only the active `init_boot` partition,
not the full US firmware. Canadian slot B is accepted by the identity and
verification code but has no separate hardware result yet.

The US image is a patch source, **not a backup of the original Canadian
partition**. This procedure cannot make an original ROW backup on an unrooted
unit. Preserve one if you already have it; restoring the US source does not
restore a known original ROW image. Recovery remains unproven as described in
[RECOVERY.md](RECOVERY.md).

Start from the original Android after setup and a verified unlock. The downloads
come directly from Google's pinned US OTA and the official Magisk release.
Neither stock nor patched firmware is hosted by this repository. The full OTA
is downloaded only if the extracted input is not already cached.

```sh
python3 onn2026.py --connection "$ONN26_CONNECTION" fetch-inputs --method usb-fastboot
python3 onn2026.py --connection "$ONN26_CONNECTION" prepare-root --method usb-fastboot
```

The script verifies the source hashes, patches `cache/firmware/init_boot.img` on
this onn using Magisk 30.7 and verifies its ramdisk. It installs the Magisk app if
needed. It does not flash or reboot the device. Keep the printed `patch_result`
path and that session's `patched-init_boot.img` and `patch-source-init_boot.img`.
The receipt records the physical serial, exact ROW firmware, active slot,
original Android installation and US patch source. Never share a patched image
for another unit.

## Flash once over USB fastbootd

Use Android platform tools with USB permissions configured on the host. Connect
the onn's data-capable USB port to the computer. Replace the placeholders below
with this onn's physical `ro.serialno`, current authorized ADB target and the
patch receipt printed above. The fastboot serial must match the physical serial
in that receipt; an IP address is not a fastboot serial.

```sh
ONN26_PATCH_RESULT="work/REPLACE_WITH_PREPARE_ROOT_SESSION/patch-result.json"
ONN26_ADB_TARGET="YOUR_CURRENT_ADB_TARGET"
ONN26_USB_SERIAL="YOUR_JCA_PHYSICAL_SERIAL"
python3 onn2026.py --connection "$ONN26_CONNECTION" status
adb -s "$ONN26_ADB_TARGET" shell getprop ro.serialno
adb -s "$ONN26_ADB_TARGET" shell getprop ro.boot.slot_suffix
```

Check those values against the saved binding and patch receipt. If they differ,
stop. Request fastbootd once:

```sh
adb -s "$ONN26_ADB_TARGET" reboot fastboot
fastboot devices
fastboot -s "$ONN26_USB_SERIAL" getvar serialno
fastboot -s "$ONN26_USB_SERIAL" getvar current-slot
```

Confirm the same physical serial and slot. Fastboot usually prints the slot as
`a` or `b`, while Android records `_a` or `_b`. Use `a` only for confirmed `_a`,
or `b` only for confirmed `_b`; never default to A or change slots to make a
command apply. Set `ONN26_SLOT` to that confirmed letter:

```sh
ONN26_SLOT="REPLACE_WITH_CONFIRMED_a_OR_b"
ONN26_PATCHED_IMAGE="$(dirname "$ONN26_PATCH_RESULT")/patched-init_boot.img"
python3 - "$ONN26_PATCH_RESULT" "$ONN26_USB_SERIAL" "$ONN26_SLOT" <<'PY'
import json
from pathlib import Path
import sys
import onn2026 as o
p = Path(sys.argv[1])
r = json.loads(p.read_text())
o.verify_files()
o.require(r.get("root_method") == "usb-fastboot", "Not a USB patch receipt")
o.require(r["binding"]["ro.serialno"] == sys.argv[2], "Physical serial mismatch")
o.require(sys.argv[3] in ("a", "b") and r["binding"]["ro.boot.slot_suffix"] == "_" + sys.argv[3], "Slot mismatch")
o.require(r["fastboot_partition"] == "init_boot_" + sys.argv[3], "Partition mismatch")
o.require(o.validate_image(p.parent / "patched-init_boot.img") == r["patched_image"], "Image changed")
print("Receipt and image match. Flash only after the fastboot identity checks above.")
PY
```

Proceed only if that check succeeds and the live fastboot identity matched.
Run the flash **once**:

```sh
fastboot -s "$ONN26_USB_SERIAL" flash "init_boot_$ONN26_SLOT" "$ONN26_PATCHED_IMAGE"
```

If it fails or the result is uncertain, preserve the output and inspect before
another action; do not automatically retry or reboot. After fastboot reports a
successful flash, request one normal restart:

```sh
fastboot -s "$ONN26_USB_SERIAL" reboot
```

## Verify permanent root

Open **Settings > Apps > See all apps > Magisk > Open**, finish any additional
setup Magisk requests and allow its restart. Reconnect the saved onn if its ADB
address changed, then run:

```sh
python3 onn2026.py --connection "$ONN26_CONNECTION" verify-root --method usb-fastboot --patch-result "$ONN26_PATCH_RESULT"
```

Grant Shell access in Magisk when prompted. A denied prompt can be corrected in
Magisk's Superuser settings and this read-only verification repeated. Success
requires a new normal boot of the original Android installation, the same
physical serial/firmware/slot, Magisk 30.7 root, SELinux Enforcing and an active
partition hash matching this device's patched image. A successful flash command
alone does not mark root as verified.

Keep the work folder and receipts. The network DSU workflow, `install-root`, `cleanup-root`
and `guide --restart-dsu` do not apply to this manual USB route. No DSU image is
created or uploaded. Android's automatic-update setting can be managed separately
with `disable-updates`, `updates-status` and `enable-updates` using the saved
connection.
