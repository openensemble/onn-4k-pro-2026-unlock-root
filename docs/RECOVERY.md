# Recovery and failure handling

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
