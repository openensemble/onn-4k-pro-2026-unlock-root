"""Interpret Android's DSU status messages without authorizing device actions."""
import re
from diagnostics import readable


STALE_PROGRESS_SECONDS = 600
STATUS = re.compile(r"\bstatus: ([A-Z_]+), cause: ([A-Z_]+)\b([^\n]*)")


def installation(log, remote, current, same_boot, device_epoch, started_epoch=None):
    report = {"installation_status": "unverified", "log_matches_archive": False,
              "last_event": None, "last_event_age_seconds": None,
              "progress_stale_after_seconds": STALE_PROGRESS_SECONDS,
              "message": "Installation completion could not be verified. Android's installation log is missing "
                         "or does not identify this image. Check the TV for a pending installation prompt."}
    # A completion for a different archive, or before this archive's last log
    # marker, cannot prove this installation. Keep legacy stage receipts usable.
    marker = re.compile(r"(?<![\w.-])" + re.escape(remote.rsplit("/", 1)[-1]) + r"(?![\w.-])")
    lines = log.splitlines()
    marked = [index for index, line in enumerate(lines) if marker.search(line)]
    events = []
    if marked:
        segment = lines[marked[-1]:]
        another_image = any("URL:" in line and not marker.search(line) for line in segment)
        report["log_matches_archive"] = not another_image
        if not another_image:
            for line in segment:
                match = STATUS.search(line)
                if not match:
                    continue
                stamp = re.match(r"\s*(\d+\.\d+)\s+", line)
                epoch = float(stamp[1]) if stamp else None
                # New stages record Android's clock, avoiding host/device clock
                # differences and completion messages from a previous attempt.
                if started_epoch is not None and (epoch is None or epoch < started_epoch):
                    continue
                events.append({"status": match[1], "cause": match[2],
                               "detail": readable(match[3].removeprefix(", ")),
                               "epoch": epoch})
    if events:
        event = events[-1]
        report["last_event"] = event
        age = device_epoch - event["epoch"] if event["epoch"] is not None else None
        report["last_event_age_seconds"] = round(max(0, age)) if age is not None else None
        if event["cause"].startswith("ERROR_"):
            report.update(installation_status="failed",
                          message="Temporary Android installation failed: " + event["cause"] + ".")
        elif event["cause"] == "INSTALL_CANCELLED":
            report.update(installation_status="cancelled", message="Temporary Android installation was cancelled.")
        elif event["status"] == "READY" and event["cause"] == "INSTALL_COMPLETED":
            if current == "installed\ndisabled":
                report.update(installation_status="ready", message="Temporary Android installation is complete and disabled.")
            else:
                report["message"] = "Android logged completion, but the temporary system is not installed and disabled."
        elif event["status"] == "IN_PROGRESS":
            if age is None or age < -5:
                report["message"] = "Android logged installation progress, but its age could not be verified."
            elif age >= STALE_PROGRESS_SECONDS:
                report.update(installation_status="stalled",
                              message="Installation may have stalled: the last recorded progress is "
                                      + str(int(age // 60)) + " minutes old.")
            else:
                report.update(installation_status="installing",
                              message="Android recently reported installation progress. Keep the onn powered on.")
        else:
            report["message"] = "Installation completion is unverified. Android last reported " + event["status"] + "."
    elif report["log_matches_archive"]:
        report["message"] = "The image appears in Android's log, but no installation result can be verified. " \
                            "The installer may not have started or its status messages may be missing."
    if current not in ("normal", "installed\ndisabled"):
        report.update(installation_status="unverified",
                      message="The temporary system is enabled, running, or in an unexpected state. "
                              "Inspect the TV and saved diagnostics before continuing.")
    if not same_boot:
        report.update(installation_status="unverified",
                      message="The onn restarted since staging or during this check. "
                              "The saved installation cannot be verified on this boot.")
    return report
