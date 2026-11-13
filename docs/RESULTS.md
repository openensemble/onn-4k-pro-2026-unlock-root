# Measured results — November 10, 2026

One 2026 jarvis2 on the exact Android 14 profile in `profile.json` was tested.

| Check | Result |
| --- | --- |
| Direct AVB unlock from normal ADB shell | One trusted-service write, state 1 to 0 |
| Unlock after completed normal boots | State 0; unlocked/orange; saved startup unchanged |
| On-target image preparation | Official Magisk 30.7, matching stock init_boot |
| Stock DSU rehearsal | Separate setup reached; power cycle returned original Android |
| Modified DSU and root console | Booted; manual UID 0 console worked with DSU already disabled |
| Permanent firmware write | One 8 MiB write to init_boot_b; independent binary readback matched |
| Normal Android after flash | Original Android ID and slot returned |
| Magisk additional setup | Completed through the official app and its reboot |
| Permanent root | UID 0, Magisk 30.7 / 30700, SELinux Enforcing |
| Stock backup | Exact pinned image verified on host and in Magisk backup location |
| Test cleanup | DSU guest and generated downloads removed |
| Automatic system-update scheduling | Disabled through the packaged command; fresh process read back 1 |
| Custom boot scripts / modules | None |
| Temporary-root exploits | None |
| Recovery after an unbootable modified init_boot | Not demonstrated |

An initial flash-script preflight stopped because Android mksh’s `hash` alias
shadowed a same-named function. The partition was independently confirmed stock
and the device write marker absent. The corrected script uses a unique function
name and an explicit on-device check-only mode; exactly one firmware write then
completed. The stopped attempt was not silently replayed.

The earlier unlock was followed by missing apps/temporary files and renewed ADB
authorization, consistent with a data reset. Do not promise a data-preserving
unlock. The DSU/root installation later preserved the original Android identity.

Personal identifiers and raw receipts stay outside this repository. The exact
supported build is deliberately narrow; one unit does not establish support for
other Android versions or hardware sold under a similar name.

## Community slot-A results — November 13, 2026

The project maintainer confirmed successful community unlock and root
installations on active slot A for the supported firmware. The profile, guide
and diagnostic status now reflect these results. The measured slot-B results
above remain the project's earlier direct hardware verification.
