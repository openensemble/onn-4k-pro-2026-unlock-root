# Recovery and failure handling

Unlocking the bootloader requires a factory reset in Android Recovery on the TV.
It erases all user apps, accounts, settings and other local data. After the unlock
reboot, follow recovery's on-screen controls to select the factory reset and
confirm the data wipe, then reboot into Android if prompted. If the reset has
already finished, continue with Android setup. The guide waits for this step and
then helps you re-enable debugging. An already-unlocked onn skips unlocking.

The measured network installation used a single-use DSU root console to write
only the active `init_boot_b`. The stock backup matched the pinned Google OTA.
The modified partition passed a fresh read and a binary copy back to the host;
normal Android and permanent Magisk root then survived two boots, including
Magisk’s official additional setup.

Stock recovery entry through ADB, its visible menu and return to Android were
observed before the flash. Independent physical entry and a working recovery
flash/restore transport were **not demonstrated**. The owner explicitly accepted
that limitation for the measured flash. Do not treat the inactive A/B slot as a
proven fallback. The guided installer asks you to choose **Accept** or **Decline**
for this risk before it starts and again before the root installation. The manual
`install-root` command requires `--accept-unproven-recovery`. These acknowledgments
do not mean that recovery is available.

For the supported stock first-stage init, the single-use DSU flag is changed to
`disabled` and fsynced before mapping the DSU images. A stock rehearsal reached
setup; a physical power cycle then returned to original Android. The root helper
also refuses to run unless the DSU is already disabled and its single-use marker
is present. This fallback depends on the original first-stage boot path and does
not establish recovery from a damaged init_boot.

If a stage, enable, flash or reboot has an uncertain result, keep its receipts.
Do not delete attempt records to rerun it. An ADB reboot timeout may simply mean
the reboot was accepted; inspect the TV and reconnect rather than repeating it.
A failed firmware write must not be followed by an automatic reboot or blind
restore. The script stops for inspection and retains the current console.

## Abandoning temporary-system setup before a firmware write

Use `python3 onn2026.py guide --restart-dsu`, or choose **R** when reopening
unfinished guide progress. The guide can also offer this recovery when it finds
an existing temporary installation, an incomplete upload, or an unconfirmed
temporary staging/boot action. Removal always needs explicit confirmation after
the current device and files have been inspected.

Requirements are the same supported serial, firmware, bootloader and active slot,
a completed unlocked boot into original Android, and DSU state `normal` or
`installed` / `disabled`. When stage records exist, the original Android ID must
match. A reboot requested on the current boot must be resolved before cleanup.
A running/enabled guest is refused. Observe the TV and preserve its receipts;
do not issue another reboot merely because the first request timed out.

The removal stops the DSU installer, removes its installed/incomplete guest and
temporary data through `gsi_tool wipe`, and checks for `normal` state. Only regular
files matching the tool's exact `onn26-(stock|root)-<12 hex digits>.raw.gz` names
in Download are eligible. The prompt lists them, and each file's current size and
hash must still match before deletion. Partial uploads are deliberately eligible.
When original receipts are missing, the prompt explicitly covers removal of the
current DSU; its original ownership cannot be reconstructed from a fresh clone.

Previous guide progress is saved under the selected device's `dsu-restarts/`
directory. All existing attempt journals, session receipts and backups remain.
Only after verified cleanup does an atomic host update select a new DSU cycle,
with separate stage/enable/reboot journals under `dsu-cycles/`. Old stage and
rehearsal receipts are rejected for the new cycle. The guide then starts fresh,
skips unlocking an already-unlocked unit, uploads again, and repeats the stock
rehearsal before root setup. Firmware and stock-partition proof still apply.

If removal or a host update is interrupted, an unfinished restart record blocks
ordinary staging. Reopening the guide requires a new inspection and confirmation.
A saved cleanup completion allows the remaining host updates to finish without
repeating device cleanup. The completion receipt is published atomically, and the
saved fresh guide is checked against its recorded hash. Missing active-cycle
records stop recovery rather than making retired receipts usable again. If the
preserved work folder moves during recovery, select its connection file from the
new location; the resumed guide records that path for the same bound device.
Guided and manual modifying commands share locks for both the work folder and
the checkout's build cache, including when separate checkouts use the same work.
No firmware attempt journal is cleared, and no firmware
write, unlock or reboot is issued by removal. An existing root-install attempt,
post-install guide phase, or detectable permanent root blocks this restart path.
Use the original verification/cleanup flow for an already-rooted device.

The installer stores an exact stock image in its local `work/` session. After
permanent-root verification, `cleanup-root` also installs the same stock image
as Magisk’s compressed backup. These backups support a separately reviewed
restore while a suitable transport is working. An actual restore after a failed
boot was not tested; no automatic rescue promise is made.

USB autoscript experiments did not establish a usable installation route and
are not included. The script does not change saved bootcmd/storeboot, alter
bootloader firmware, install a boot receiver, or leave an experimental startup
script. No temporary-root exploit is used. The intended Magisk root installation
itself persists across normal boots.
