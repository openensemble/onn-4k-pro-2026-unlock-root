# Advanced command reference

For the normal guided walkthrough, run `python3 onn2026.py` from the repository
folder. You do not need to enter the commands below when using the guide.

These lower-level commands are for inspecting receipts or carrying out a
reviewed manual workflow. Do not mix them into an active guide. Read the
[factory-reset and bricking warnings](../README.md) and
[recovery limitations](RECOVERY.md) first. Preserve `work/` and all attempt records.

Enable Developer mode, USB debugging and Wi-Fi/network debugging on the onn. Authorize this
computer’s ADB key on the TV. Replace the example address and NDK path below:

```sh
ONN26_INITIAL_TARGET="YOUR_ONN_IP:5555"
ONN26_NDK="/path/to/android-sdk/ndk/28.2.13676358"
adb connect "$ONN26_INITIAL_TARGET"
python3 onn2026.py --target "$ONN26_INITIAL_TARGET" status --name "Living room"
```

Copy the printed `connection_file` path into the variable below. It contains the
verified hardware serial, firmware, slot, friendly name and last address:

```sh
ONN26_CONNECTION="work/devices/REPLACE_WITH_PRINTED_DEVICE_DIRECTORY/connection.json"
onn26() { python3 onn2026.py --connection "$ONN26_CONNECTION" "$@"; }
```

If ADB is not on PATH, add `--adb /absolute/path/to/adb` before the command name.
Keep using `--connection` after initial registration: every device command then
checks the saved physical serial and firmware before proceeding. Keep the same
repository and `work/` directory throughout the process.

## IP changes and multiple onns

After a restart or reconnecting Wi-Fi, run:

```sh
onn26 reconnect --scan-lan
```

This first checks the saved address and existing network ADB connections. If
needed, it checks mDNS connection advertisements and port 5555 at the saved IP.
With `--scan-lan`, it then searches port 5555 on up to four active private LAN `/24` networks
(or smaller subnets). Common VPN and virtual interfaces are excluded. Only
authorized devices have their properties read. A matching serial, firmware,
bootloader and slot must be found and checked again before the address is saved.
Another onn taking the old address is rejected, even if it has identical firmware.
An ambiguous match stops and asks you to specify one address:

```sh
onn26 reconnect --candidate NEW_ONN_IP:5555
```

The reconnect command only reads device properties and updates the host's
connection file. It never replays an unlock, flash or reboot. Normal commands
stop when disconnected; reconnect is an explicit step, not a background retry.
After reconnecting, continue with the documented verification step. Preserve all
attempt records, especially after an uncertain write.

For abandoned temporary-system setup before a firmware write, use
`python3 onn2026.py --connection "$ONN26_CONNECTION" guide --restart-dsu`.
This opens the guided removal/restart prompt and preserves the manual receipts.
See [the recovery requirements](RECOVERY.md#abandoning-temporary-system-setup-before-a-firmware-write).

A household can process multiple supported **2026** units **one at a time**.
Register each at its own initial address with a distinct name, for example:

```sh
adb connect BEDROOM_ONN_IP:5555
python3 onn2026.py --target BEDROOM_ONN_IP:5555 status --name "Bedroom"
python3 onn2026.py devices
```

`devices` lists saved names, serials, last addresses and connection-file paths
without contacting any box. Select the desired file in `ONN26_CONNECTION`, run
`onn26 reconnect --scan-lan`, and follow the full walkthrough for that unit.
Names are labels; the serial is the identity. The same name does not combine
devices. Stock downloads can be shared, but each unit needs its own prepared
Magisk image, DSU build, backup and receipts. Attempt journals are stored under
its serial-derived directory. Receipts and images from another unit are rejected.
The older 2024 onn uses its separate installer.

## Individual unlock and root commands

**1. Unlock once.** If `status` reports trusted state 0 and
`unlocked_on_current_boot: true`, skip the unlock write. Otherwise:

**Unlocking requires a factory reset in Android Recovery on the TV. This erases
all user apps, accounts, settings and other data stored on the onn.** Back up
anything you want to keep before accepting data loss.

```sh
onn26 unlock --accept-data-loss
```

After a reported successful write, perform one normal restart from the TV.
In Android Recovery, select the factory reset option and confirm the data wipe
using its on-screen controls. After the reset, reboot into Android if prompted.
If the reset has already completed, continue with Android setup. Connect Ethernet
or Wi-Fi, re-enable Developer mode, USB debugging and Wi-Fi/network debugging,
and reconnect ADB.
Run `onn26 reconnect --scan-lan`, then `onn26 status` again. Require trusted state **0**, flash lock **0**, vbmeta
**unlocked**, verified boot **orange**, and completed Android boot. The unlock
command never retries its write and never reboots automatically.

**2. Prepare Magisk on this onn.**

```sh
python3 onn2026.py fetch-inputs
onn26 prepare-root
```

This downloads hash-pinned official inputs, patches init_boot **on the selected
device**, and installs the Magisk app. It does not flash firmware. Save the
printed `patch_result` path as `ONN26_PATCH`:

```sh
ONN26_PATCH="work/REPLACE_WITH_PREPARE_SESSION/patch-result.json"
```

Throughout this walkthrough, replace `REPLACE_WITH_...` with the exact path
printed by the preceding command. Receipt paths are deliberately explicit so
one device’s files cannot silently be used for another device.

If root preparation reports **Android installation identity unavailable**, the
read of `settings get secure android_id` did not pass validation. This check
comes after unlock verification and before root-image patching or flashing.
An earlier release incorrectly required exactly 16 hexadecimal characters;
Android's ID can legitimately have fewer because its hexadecimal representation
is not padded. Root preparation and DSU staging now accept 1–16 hexadecimal
characters and preserve the exact value for later installation comparisons.

An empty or `null` ID still stops the workflow. Complete Android setup and
inspect the session's `android-installation-id` command receipt, preserving
the existing `work/` folder. Report whether the value is missing, hexadecimal,
and its character count; the ID itself does not need to be posted publicly.
Do not set a replacement ID, factory-reset the onn, or repeat the unlock to
work around this check. Without the receipt, this error alone does not establish
that a short ID caused a particular user's failure.

**3. Build the stock rehearsal and root DSU images.**

```sh
onn26 build-dsu --ndk "$ONN26_NDK" --adb-public-key "$HOME/.android/adbkey.pub"
ONN26_BUILD="work/REPLACE_WITH_BUILD_SESSION/dsu-build/build.json"
```

Use the **public** key belonging to the ADB server you authorized. Never supply
`adbkey` (the private key). The builder creates a helper bound to this serial,
firmware, bootloader and slot. It preserves stock file ownership, permissions,
SELinux labels and capabilities, verifies the rebuilt filesystem, and compiles
the combined SELinux policy. Only the helper’s DSU domain is permissive; global
SELinux enforcement is not disabled. The helper never starts automatically.

**4. First DSU boot: test that temporary Android starts and original Android returns.**

This is a test boot. Root is not installed here. After the power cycle and return
check below, you will start a second DSU boot for root installation in step 5.

```sh
onn26 stage-dsu --build "$ONN26_BUILD" --kind stock
ONN26_STOCK_STAGE="work/REPLACE_WITH_STOCK_STAGE_SESSION/stage.json"
onn26 dsu-status --stage "$ONN26_STOCK_STAGE"
```

Installation is asynchronous and stays disabled. Wait for it to finish, then
run the read-only `dsu-status` command again if necessary. Continue only when it
reports `installed_disabled_verified: true`.

```sh
onn26 boot-dsu --stage "$ONN26_STOCK_STAGE"
```

The command enables **one** DSU boot and requests **one** reboot. ADB may time
out because the device is restarting; that is not a reason to repeat the
command. Once the TV reaches the separate setup screen, skip setup, sign-in and
debugging settings. Disconnect power and reconnect it once. The original Android
installation should return. Reconnect ADB, then record those observations:

```sh
onn26 reconnect --scan-lan
onn26 confirm-stock-return --stage "$ONN26_STOCK_STAGE" --observed-setup --power-cycled-back
ONN26_REHEARSAL="work/REPLACE_WITH_RETURN_SESSION/stock-return.json"
```

The command checks the original Android ID, a changed boot ID and disabled DSU
state. This demonstrates the DSU fallback, not recovery from a bad init_boot.

**5. Second DSU boot: start the root installation system.**

```sh
onn26 stage-dsu --build "$ONN26_BUILD" --kind root --rehearsal "$ONN26_REHEARSAL"
ONN26_ROOT_STAGE="work/REPLACE_WITH_ROOT_STAGE_SESSION/stage.json"
onn26 dsu-status --stage "$ONN26_ROOT_STAGE"
```

After `installed_disabled_verified: true`:

```sh
onn26 boot-dsu --stage "$ONN26_ROOT_STAGE"
```

This is the **second** temporary boot, used for root installation; stay in it and
leave the power on. Pair the remote if needed and connect Ethernet or Wi-Fi. The
root DSU already starts ADB on port 5555 and includes this computer's public key.
Try this prepared connection before opening debugging settings or signing in to
Google; Google account setup is not a step in this procedure. Its IP address may
change. Run `onn26 reconnect --scan-lan` to find the saved unit again. Discovery
also checks mDNS connection ports before scanning port 5555. If a Wireless
debugging connection needs pairing, use `adb pair IP:PAIRING_PORT`, then
`onn26 reconnect --candidate IP:CONNECTION_PORT`. The two ports are different.
Keep the same connection file; do not register a new device because its IP changed.

**6. Install permanent Magisk.** This is the firmware-writing step. The stock
DSU return test does not remove the remaining init_boot recovery risk.

```sh
onn26 install-root --stage "$ONN26_ROOT_STAGE" --patch-result "$ONN26_PATCH" --accept-unproven-recovery
ONN26_INSTALL="work/REPLACE_WITH_INSTALL_SESSION/install-result.json"
```

The script requires a running but already-disabled single-use root DSU. It
checks the helper, original image, prepared patch and device identity; saves a
binary stock backup; runs all flash guards in check-only mode on Android; and
records host and device attempt markers before the only partition write.
It writes exactly 8 MiB to the bound active `init_boot_a` or `init_boot_b`,
flushes it, reads it back independently,
and removes the flasher. It does not write another slot, vbmeta or bootloader,
and does not install a script at boot.

Both slots have successful unlock and root results: community reports for slot A
and measured hardware verification for slot B. The selected slot comes from the device's
saved identity, including when building the DSU helper. The standalone offline
builder requires an explicit `--slot _a` or `--slot _b`; the workflow supplies
the verified slot automatically. The active partition's exact mapping, size and
stock hash must pass before any firmware write. Never change the active slot or
edit saved bindings to get past a refusal.

Continue only after `verified_flash: true`. If any write fails or times out,
keep the current session running and inspect the receipts. **Do not rerun the
write, delete attempt records, or reboot on an uncertain result.**

**7. Return to normal Android and finish Magisk setup.**

Only after `verified_flash: true`, restart once from the TV's power menu, or
disconnect and reconnect power once. After normal Android returns, reconnect:

```sh
onn26 reconnect --scan-lan
```

On the TV, use **Settings > Apps > See all apps > Magisk > Open**. Magisk may not
appear in Google TV's regular app list. Accept Magisk’s **Requires additional
setup** dialog and allow its setup reboot to finish. Run `onn26 reconnect
--scan-lan` again. Open Magisk through the same Settings path after the reboot.

**8. Verify permanent root and remove the test guest.**

Stay at the TV with the remote before running verification. Select **Grant** for
**Shell** when Magisk asks; its prompt has a short countdown. If a previous
request expired or no prompt appears, open Magisk's **Superuser** screen and
allow **Shell**. Alternatively, remove only Shell's saved entry there to get a
fresh request. An existing grant can pass without another prompt.

```sh
onn26 verify-root --patch-result "$ONN26_PATCH"
onn26 cleanup-root --install-result "$ONN26_INSTALL"
```

Run `cleanup-root` only after `verify-root` succeeds. A denied or timed-out
verification can be rerun after checking Shell's permission; keep the existing
patch receipt and work folder. In the guided walkthrough, Enter retries this
read-only check, and `q` leaves it ready to resume. Installation and restart
steps are not repeated to recover a missed permission prompt.

Verification requires original Android, Magisk **30.7 / 30700**, UID **0**,
SELinux **Enforcing**, complete Magisk runtime files and the exact patched
partition hash. Guest-only root does not pass. Cleanup saves a verified stock
backup in Magisk’s backup directory and removes this workflow’s DSU and downloads.
The local backups and attempt records remain in ignored `work/`.

**9. Disable automatic system updates.**

```sh
onn26 disable-updates
onn26 updates-status
```

Require `automatic_system_updates_disabled: true`. This persists Android’s
`ota_disable_automatic_update=1` preference, which disables automatic scheduling
of system updates ([AOSP setting definition](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/core/java/android/provider/Settings.java)). It needs no boot script. Manual system updates remain
available; app updates are a separate setting. To intentionally restore automatic
system updates later, run `onn26 enable-updates`.
