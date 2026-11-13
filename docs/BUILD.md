# Build and validation

The firmware profile and input hashes are in `profile.json`. The two shipped
ARM32 AVB helpers were built with Android NDK r28c, API 28. Rebuilding with
NDK 28.2.13676358 reproduced both binary hashes. The DSU console is compiled
locally with a generated header containing the selected unit’s binding; no
personal header or generated image belongs in Git.

```sh
python3 tools/build_native.py --ndk /path/to/android-ndk-r28c
python3 onn2026.py verify-files
python3 -m unittest discover -s tests -v
cc -std=c11 -Wall -Wextra -Werror -O2 native/test_unlock_logic.c -o native/test_unlock_logic
./native/test_unlock_logic
```

Validation on November 10, 2026:

- 11 native AVB protocol scenarios passed.
- 17 Python test methods passed. The flash-template test runs 11 regular-file
  scenarios, including wrong unit/slot/boot, armed DSU, changed images, consumed
  attempts, check-only mode and partial-write failure.
- The packaged DSU builder completed a clean offline build using erofs-utils
  1.7.1 and the recorded NDK. All 2,461 original inode metadata records were
  preserved; every original unchanged file and symlink was checked. Three files
  are added and three policy files are changed. The rebuilt filesystem and AVB
  hash tree verified successfully.
- For the measured binding, every loaded ELF section of the packaged DSU helper
  matches the working hardware-tested helper. The filename/symbol metadata can
  differ because the public source has a generic filename and generated binding.
- The packaged status, on-target patch preparation, permanent-root verifier and
  update-preference inspection and disabling ran on the intended unit. The firmware-writing
  sequence was executed once during the measured install. The packaged full
  destructive sequence was not replayed against the already-rooted device.

The stock DSU gzip rebuilt byte-identically. Different serials or public keys
necessarily produce different root DSU images. Metadata and content verification
must pass on each build. The tests use regular files and fake device responses;
they do not simulate power loss, physical recovery or every firmware revision.

`NATIVE_SHA256.json` includes the packaged implementation inputs as well as the
reviewed binaries. Regenerate it only after reviewing and testing an intentional
source change. Keep `work/`, `cache/`, generated binding headers, images, APKs,
keys and raw device receipts out of commits.

Reconnect validation adds 21 tests for
saved identity, address changes, household device separation, unauthorized and
ambiguous devices, a changed identity immediately before saving, interrupted
host-file replacement, bounded LAN selection, scan limits and probe timeouts.
The tests block real subprocess and socket access for the reconnect fixtures.
Reconnect has not been run on a live onn during this update; these checks do not
validate real network timing.

The guided installer adds 33 tests, the terminal buttons add 7 tests,
automatic normal restarts add 13 tests, and process interruption adds 1 test,
bringing the total to **92 Python
test methods**. They exercise the complete
guide with a simulated backend, both risk
acknowledgments, already-unlocked units, a failed flash, readback failures,
interrupted operations, confirmed-result recovery without replay, Ctrl-C during
a critical step, separate household devices, the real menu/entry point, the guide
lock, and refusal to overwrite earlier manual attempt records. Real subprocess
and socket access are blocked in these guide fixtures. The button tests use local
pseudo-terminals to check Accept, Decline, Escape, arrow/Tab navigation and
restoration of terminal settings. They never run ADB.
The page tests also check that previous questions and answers are cleared,
that initial debugging setup comes before a new connection, and that the
post-reset debugging reminder waits before reconnecting without replaying unlock.
The restart tests check that a durable attempt is saved before each request,
timeouts and interruptions cannot cause replay, already-restarted units are
checked without another reboot, and failed flash proof, changed image hashes,
changed identity, changed boots and enabled temporary systems prevent restart.
Saved results from either new restart step resume without another request;
older guide checkpoints at a manual restart prompt use the same guarded check.

The process-interruption test sends a real Ctrl+C signal to an isolated test
process group during a command and a binary backup. Both use an offline transport
fixture. Protected device commands finish and save their result before the guide
stops; the interrupt does not terminate the child transport midway through the step.

The separate local preview used for validation uses byte-identical copies of `guide_core.py` and
`console_ui.py`, with an in-memory backend and checkpoint store. It exposes only
the normal successful walkthrough and is not included in this package. Its full flow and Decline pass under an audit
hook that rejects network access, subprocesses and filesystem writes. An actual
terminal run also completed through 16 fresh interaction screens using four
Accept selections and ten simulated TV confirmations. Both debugging setup
screens were present and no mock labels appeared in the rendered flow.
The additional naming page was checked with a custom room name and a blank
entry; the selected name or serial was retained through installation and completion.
The guided installer has not been used to repeat an unlock or
flash on the already-rooted onn.

## Read-only slot-report collector

`tools/slot_report.py` is independent of the installer and accepts an active
slot of A or B. Its offline tests use an allowlisted fake ADB transport; any
unexpected device command fails the test. Run just those tests with:

```sh
python3 -m unittest discover -s tests -p 'test_slot_report.py' -v
```

The 15 tests cover locked slot A, slot B, denied metadata access, readable
metadata and bounded init_boot hashing, fallback size information, unexpected
partition mappings/sizes, unavailable boot-control tools, HAL errors, a newer
firmware report, wrong hardware, changed identity/slot/boot, authorization,
timeouts without retry, report privacy and refusing to overwrite an existing
output directory. Raw command receipts stay separate from shareable reports.

A nonzero `bootctl is-slot-bootable` or `is-slot-marked-successful` result is
recorded as unknown/error, not interpreted as false: the
[Android 14 implementation](https://android.googlesource.com/platform/system/extras/+/android-14.0.0_r1/bootctl/bootctl.cpp)
uses failure exits for both a false result and a HAL error. Missing metadata
does not establish bootability, update completion, or installation support.

On November 12, 2026, all **107 Python tests** passed, including those 15 new
diagnostic tests. The collector also completed on a locked build-15640252
jarvis2 running slot B. It confirmed both init_boot links and normal Android,
reported denied size/hash access and unavailable boot-control information, and
left the device unchanged. The generated public files were checked for absence
of the target address, hardware serial and boot ID. Raw receipts remain in
ignored `work/slot-reports/`. Later that day, a
[community report](https://github.com/openensemble/onn-4k-pro-2026-unlock-root/issues/2#issuecomment-6833540065)
showed a complete collection on a locked slot-A unit with matching firmware,
bootloader and expected init_boot mappings. Sizes and hashes remained unavailable.
This is third-party diagnostic evidence, not a slot-A unlock or root result.

## Android installation-ID validation

The Android 14
[SettingsProvider implementation](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/packages/SettingsProvider/src/com/android/providers/settings/SettingsProvider.java)
generates the secure Android ID with `Long.toHexString`, which does not pad
leading zeroes. Both root preparation and DSU staging now use a shared reader
that accepts 1–16 hexadecimal characters, preserves the exact string, and
rejects missing or malformed responses. It does not generate or change IDs.

Five regression tests reproduce the previous short-ID rejection in both
paths, check exact receipt preservation, stop missing/invalid IDs before
staging, and retain the mismatch guard when a short installation ID changes.
These tests use fake device interfaces and forbid live subprocess calls.
All **112 Python tests** passed after the fix on November 12, 2026, and the
packaged-input hash verification passed. The reporter later confirmed a
15-character ID and that the update let them proceed past this check; the ID
itself was not supplied. No live unlock, DSU boot or root flash was performed
for this change.

## Magisk launch and verification retry

The walkthrough gives the Google TV Settings path for opening Magisk and waits
for the owner to be at the TV before requesting root. Magisk 30.7's
[request timer](https://github.com/topjohnwu/Magisk/blob/v30.7/app/apk/src/main/java/com/topjohnwu/magisk/ui/surequest/SuRequestViewModel.kt)
denies unanswered requests, and its
[request handler](https://github.com/topjohnwu/Magisk/blob/v30.7/app/core/src/main/java/com/topjohnwu/magisk/core/su/SuRequestHandler.kt)
can save that policy. The retry screen explains how to allow Shell or remove
its saved entry using Magisk. It does not edit Magisk's policy database.

Six regression tests cover readiness before the first request, denial followed
by an explicit retry, leaving and resuming after a timeout, preserved attempt
history, successful verification, and rejection of incomplete or incorrect
root proof. The denial fixture matches a saved command result: exit 13,
`Permission denied`, and no transport timeout. A retry repeats the read-only
verification step; uncertain writes and restarts remain blocked from replay.
The full Python suite and packaged-input hash verification passed on November
12, 2026.

A subsequent authorized read-only check on the recovered original unit passed
with Magisk 30.7, UID 0, SELinux Enforcing, complete runtime files and the exact
on-target patched init_boot hash. The original Android installation and the
Magisk setup restart were also confirmed. Its temporary system remained
installed and disabled; this check did not perform cleanup, flash or reboot.
The permission retry screen itself was tested offline with simulated responses.
The owner then resumed the guide to completion. A final independent read-only
check confirmed normal Android with DSU removed and automatic system updates
disabled. No assistant-run install or restart was needed for this troubleshooting.

## Active-slot A/B support — November 12, 2026

The installer accepts either active slot on the same pinned firmware. Slot A
targets `init_boot_a` / `mmcblk0p22`; slot B targets `init_boot_b` / `mmcblk0p23`.
The selected slot remains part of the physical-device binding through unlock,
DSU compilation and staging, backup, flashing, restart and permanent-root
verification. There is no slot switch or inactive-slot fallback. The B build ID
`jarvis2-15640252-b` is retained so existing build receipts and saved progress
remain usable; attempt journals are not reset or migrated.

Before privileged partition reads and before the sole firmware write, guards
check the selected slot, expected block mapping and 8 MiB size. The exact pinned
stock hash and verified backup remain prerequisites for writing. The compiled
DSU helper checks the same slot. The standalone builder now requires `--slot`;
the normal workflow supplies it from the verified device binding.

Validation completed without contacting any device:

- All **128 Python tests** passed. The flash-template test exercised **32
  regular-file scenarios** across both slots, including wrong mappings and sizes,
  changed slot/firmware/boot, bad images, consumed attempts, partial writes and
  failed readback. The inactive fixture stayed unchanged in every case.
- Simulated A and B installations exercised the real backup, install, restart
  and permanent-root verification functions. Cross-slot patch/build receipts,
  unexpected stock hashes and proof naming the inactive partition were refused.
  Existing B connection/build records and no-replay behavior remained valid.
- The real workflow built both DSU variants offline for a synthetic device
  identity. Both preserved all **2,461 original filesystem paths**, passed file
  and inode metadata verification, compiled SELinux policy and verified AVB.
  Generated headers and compiled helpers contained the selected slot. The stock
  archives matched across builds; the slot-bound root helpers and archives differed.
- All **11 native unlock-protocol scenarios** passed. Packaged-input hashes,
  guide preview, required builder-slot arguments and whitespace checks passed.

The diagnostic report now uses schema `onn26-slot-report-v2`, with
`installer_supported_slots` and separate per-slot hardware-verification flags.
Raw community reports, build receipts and logs remain in ignored `work/`.
Bulky synthetic-device test images were discarded after verification; their
hashes and build logs were retained. No test image was staged or installed.

At this initial November 12 validation, slot B had measured hardware results
and slot A had only diagnostic and offline coverage. On November 13, the project
maintainer confirmed successful community slot-A unlock/root installations;
the profile, guide and diagnostic status now reflect those reports. See
[the community results update](RESULTS.md#community-slot-a-results--november-13-2026).
Offline tests do not establish physical recovery or eliminate failed-boot risk.

## Offline DSU host compatibility and timeout handling — November 12, 2026

The reporter who confirmed a 15-character Android ID subsequently reported
`build-offline-dsu` timing out. Their later tool log identified an ODM extraction
failure: `fsck.erofs` rejected the argument to `--extract`. The same diagnostic
was reproduced with Ubuntu's erofs-utils 1.4 package. Version 1.7.1 accepts the
builder's extraction syntax. ODM is the first stock image extracted.

The workflow supervises `build-offline-dsu` with `host_build.py`. It enforces a
900-second overall deadline, streams stage and compression progress, and records
complete output in a session log with a bounded tail in the JSON receipt.
Timeout or Ctrl-C stops the build's process group, including descendant tools,
and records the reason. Nonzero tool exits are reported as failures with exit codes
and log paths. The previous device-command runner and its timeouts are unchanged.

The guide already treats host builds as repeatable. It preserves previous
sessions and retries a failed or cancelled build in a new directory, retaining
the completed unlock and patch receipts. It does not stage partial build output.

The initial progress/cancellation update passed **138 Python tests**, including
eight new host-process tests and two
guide-resume regressions. These check live progress before completion, retained
diagnostics, real command failures without retries, missing tools, bounded
receipt output, orphaned descendants, cancellation of TERM-ignoring tools,
unchanged device timeouts, and resuming after an old timeout or cancellation
without repeating unlock, restart or patch preparation.

A subsequent full regression reran all 138 Python tests and 11 native protocol
scenarios, reproduced both packaged ARM helper binaries, and checked Python
compilation, CLI entry points, guide preview, package hashes and whitespace.
Both A and B temporary systems were then built through the real host supervisor,
with metadata, file contents, policy and AVB validation passing.

The first default rebuild had a different root-image hash because `mkfs.erofs`
generated a new filesystem UUID. For the regression comparison only, wrappers
passed each previous build's UUID to `mkfs.erofs -U`. Both resulting root images,
root archives, stock archives and compiled helpers then matched the earlier
verified outputs byte for byte. Production UUID behavior was not changed.
These were synthetic-device offline builds; no onn was contacted, unlocked,
flashed or rebooted. Detailed receipts and logs are in ignored
`work/20261112T200726Z-full-regression/`.

The follow-up host compatibility change adds a tiny compressed tar build and
file/symlink extraction check before unlocking and at the start of every offline
build. It tests the selected binaries rather than trusting version strings or
help text. Incompatible tools produce the actual diagnostic, their selected path
and a link to `docs/HOST_TOOLS.md`. The requested 15-minute overall deadline is
restored; progress output cannot reset it or keep its check from running.

All **147 Python tests** and **11 native protocol scenarios** passed after this
change. The new tests cover missing tar input, legacy extraction semantics,
successful exits without extracted files, checks before other host/build work,
bounded probe waits, and timeout cleanup of a build and TERM-ignoring child even
while progress continues. A process exiting zero on termination still records a
timeout failure. Cancellation, immediate failures and guide resume coverage
also passed. Package hashes, syntax, CLI entry points and whitespace passed.

A real offline slot-B build passed the new check and completed in 38.54 seconds
under the 900-second supervisor. All 2,461 original paths, inode metadata, file
contents, SELinux policy and AVB checks passed. Using the previous filesystem
UUID only for comparison reproduced the earlier root image, both archives and
helper byte for byte. Logs and manifests are retained in ignored
`work/20261112T213421Z-erofs-compatibility/`; the new synthetic image payloads
were removed after verification. No device operation was performed.

## Temporary-system restart and reconnection — November 13, 2026

[Issue #3](https://github.com/openensemble/onn-4k-pro-2026-unlock-root/issues/3)
reported interrupted uploads, installed DSU leftovers, consumed staging journals,
wireless-debugging port changes, and confusion between the two temporary boots.
The guide now offers a confirmed removal/restart, also available through
`guide --restart-dsu`. It retains old journals and snapshots the guide state,
then selects fresh DSU attempt records only after verified cleanup. Firmware
attempt records remain global to the device. Old stage/rehearsal receipts cannot
authorize operations after their DSU cycle is retired.

All **179 Python tests** and **11 native protocol scenarios** passed. The new
coverage exercises partial uploads, absent host receipts, decline, both slots,
wrong device/slot/Android identity, changed boots/files, symlinks, firmware-write
attempts, running/enabled guests, unresolved reboots, wipe timeout/incomplete
removal, connection loss, host-update interruption, preserved backups/history,
retired receipts, and fresh upload/rehearsal/root-guest boot after removal.
Guide entry tests cover the explicit flag, the R menu, automatic leftover
prompts, and pending recovery. Reconnection tests cover mDNS connection ports,
excluded pairing ports, identity ambiguity, fallback when multicast fails, and
the root DSU's prepared port 5555 at the saved IP. These fixtures forbid live
ADB/subprocess/network access; the existing flash tests use regular files.

The installation image already includes `adb_keys` and an init action starting
ADB on port 5555. Debugging settings/pairing guidance remains a reconnection
fallback. The stock rehearsal requires observing setup on the TV and verifying
original Android after its return, without ADB inside that first guest.

Removal behavior was checked against Android 14's
[gsi_tool](https://android.googlesource.com/platform/system/gsid/+/refs/tags/android-14.0.0_r1/gsi_tool.cpp)
and [GSI service](https://android.googlesource.com/platform/system/gsid/+/refs/tags/android-14.0.0_r1/gsi_service.cpp):
wipe from original Android clears the installer and removes its backing files;
the running-guest deferred-removal path is refused by this workflow. The DSU
installer process is stopped first so its asynchronous work cannot continue
using the archives being removed. ADB discovery follows the documented
[connection and pairing service types](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/dev/adb_wifi.md).

Receipts are in ignored `work/issue-3-recovery-research/`. No physical device was
contacted and no firmware/image build inputs changed. Recovery and wireless
rediscovery still need confirmation on hardware, including WSL2 networking.

## Full restart-feature review — November 13, 2026

The review reproduced and fixed six problems in the pending recovery changes:

- Wireless ADB could list an authorized paired device by its TLS connection
  service name, which discovery previously ignored. Existing service transports
  and explicit IP/port connections now resolve to the actual ADB target, with
  the physical-device binding checked again before saving it. A saved IP remains
  available for the root image's port-5555 fallback.
- An interrupted write to the cleanup completion receipt could leave partial
  JSON that prevented recovery. Completion now publishes a fully written receipt
  atomically without replacing any existing receipt.
- A changed fresh-guide template could be adopted during pending recovery. Its
  hash and fresh-state fields are now checked before cleanup and host activation.
- Missing active-cycle metadata could allow legacy receipts to appear current.
  Completed restart evidence now prevents that fallback.
- Separate checkouts sharing a work folder, and manual modifying commands, did
  not share the guide's lock. Both receipt storage and the checkout's cache now
  remain locked throughout modifying operations.
- Moving the preserved work folder during pending recovery could leave the
  resumed guide pointing at its old connection file. Recovery now records the
  selected file's current path while preserving the pinned guide template and
  device binding. Repeated interruptions after that update are also covered.

All **197 Python tests** and **11 native protocol scenarios** passed after the
fixes. The Python suite includes the **32 regular-file flash cases** across both
slots, a complete restarted walkthrough without another unlock, and the existing
15-minute build-timeout, cancellation, Android-ID and root-verification checks.
All 39 packaged hashes, syntax for 30 Python files, CLI help/preview entry points,
and whitespace checks passed. A real tiny compressed EROFS tar build and file/
symlink extraction passed with the local erofs-utils 1.7.1 tools.

Reproduction logs, final results and source snapshots are in ignored
`work/20261113T084839Z-restart-full-review/`. No physical device was contacted,
unlocked, flashed or rebooted. The full-size DSU builder and its image inputs
were unchanged; this review did not repeat the earlier full-image builds or
establish hardware/WSL2 recovery validation.

## Slot-A status and unlock recovery instructions — November 13, 2026

The project maintainer confirmed successful community unlock/root installations
on slot A. Its profile status, diagnostic output, guide notices and current
documentation now reflect those reports. Firmware pins, partition mappings,
stock-image checks and the required verification of each installation are unchanged.

The initial warning and unlock confirmation now explicitly explain the factory
reset in Android Recovery and loss of all user apps, accounts, settings and other
local data. After the unlock reboot, the guide waits while the owner completes
the recovery reset and Android setup, then explains how to re-enable debugging.
The manual command help and instructions carry the same explanation. Devices
already unlocked on their current boot still skip the unlock and reset steps.

All **197 Python tests** passed, including the existing **32 regular-file flash
cases**. The guide checks verify that the recovery/data-loss warning is shown
before an unlock can run, declining still prevents writes, and resuming setup
does not repeat the unlock. Package hashes, preview/CLI help, Python syntax,
static-site HTML/JSON-LD and whitespace checks passed. Logs are in ignored
`work/slot-a-and-unlock-recovery-update/`. No physical device was contacted and
no website deployment was performed.
