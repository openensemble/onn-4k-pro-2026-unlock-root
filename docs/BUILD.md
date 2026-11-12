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
ignored `work/slot-reports/`. Slot-A behavior has been tested with fixtures;
collection on real hardware actively running slot A is still untested.

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
packaged-input hash verification passed. The reporting user's actual ID/output
has not been supplied, so the regression reproduces a possible cause rather
than confirming the cause on their unit. No live unlock, DSU boot or root flash
was performed for this change.

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
