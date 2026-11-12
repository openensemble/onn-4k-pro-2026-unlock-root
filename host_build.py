"""Supervise local firmware preparation, without changing device timeouts."""
import os
import signal
import subprocess
import time

import onn2026 as o

PROGRESS_PREFIX = "[build] "
BUILD_TIMEOUT_SECONDS = 15 * 60
CANCEL_GRACE_SECONDS = 2
OUTPUT_TAIL_BYTES = 65536


def stop_process_group(process):
    """Stop this build and its tools, including children of an exited builder."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=CANCEL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A child may ignore TERM even when the group leader has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run(session, label, argv, emit=None):
    """Run one host-only build with live progress, logs and Ctrl-C cleanup.

    The entire build has a 15-minute wall-clock limit. This runner is only used
    for local DSU building; ADB, unlock, flash and reboot timeouts are unchanged.
    """
    if emit is None:
        emit = lambda message: print(message, flush=True)
    session.counter += 1
    stem = f"{session.counter:03d}-{label}"
    log = session.path / (stem + ".log")
    receipt = session.path / (stem + ".json")
    record = {"at": o.now(), "argv": [str(value) for value in argv], "host_only": True,
              "timeout_seconds": BUILD_TIMEOUT_SECONDS, "timed_out": False, "interrupted": False,
              "automatic_retry": False, "output_log": str(log), "returncode": None}
    o.save(receipt, record, exclusive=True)
    started = time.monotonic()
    process = None
    pending = ""

    def show_progress(reader, final=False):
        nonlocal pending
        # Bound each batch so continuous output cannot starve the deadline.
        for _ in range(16):
            chunk = reader.read(65536)
            if not chunk:
                break
            pending += chunk
            lines = pending.split("\n")
            pending = lines.pop()
            for line in lines:
                if line.startswith(PROGRESS_PREFIX):
                    emit(line[len(PROGRESS_PREFIX):].rstrip("\r"))
            # Machine-readable build results stay in the log, not the guide UI.
            pending = pending[-OUTPUT_TAIL_BYTES:]
        if final and pending.startswith(PROGRESS_PREFIX):
            emit(pending[len(PROGRESS_PREFIX):])
            pending = ""

    try:
        with log.open("xb") as output, log.open("r", errors="replace") as reader:
            process = subprocess.Popen(record["argv"], stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            record["pid"] = process.pid
            # Tail a regular file so a descendant retaining stdout cannot block
            # cancellation or prevent us noticing that the builder has exited.
            while process.poll() is None:
                if time.monotonic() - started >= record["timeout_seconds"]:
                    record["timed_out"] = True
                    break
                show_progress(reader)
                time.sleep(0.1)
            stop_process_group(process)
            record["returncode"] = process.returncode
            show_progress(reader, final=True)
    except KeyboardInterrupt:
        record["interrupted"] = True
        raise
    except OSError as error:
        record["error"] = str(error)
        raise o.Stop(f"{label} could not run: {error}. Build log: {log}") from error
    finally:
        if process is not None:
            stop_process_group(process)
            process.wait()
            record["returncode"] = process.returncode
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - OUTPUT_TAIL_BYTES))
                record["stdout"] = stream.read().decode(errors="replace")
            record["output_truncated"] = log.stat().st_size > OUTPUT_TAIL_BYTES
        o.save(receipt, record)
    o.require(not record["timed_out"],
              f"{label} timed out after {record['timeout_seconds']:g} seconds. Build output: {log}\n"
              "The build and its tools were stopped. Keep the saved work folder to resume.")
    o.require(record["returncode"] == 0,
              f"{label} failed (exit {record['returncode']}). Build output: {log}\n"
              "This computer-only step can be rerun. Keep the saved work folder.")
    return record
