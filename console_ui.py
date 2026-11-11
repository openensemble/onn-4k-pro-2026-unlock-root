"""Accept/Decline terminal buttons. Uses only the existing terminal streams."""
import os
import select
import sys
import termios
import tty


def clear_screen(output_stream=None):
    """Replace the current terminal page without launching a shell command."""
    output_stream = output_stream or sys.stdout
    if output_stream.isatty():
        output_stream.write("\x1b]0;onn 2026 - Unlock and root\x07\x1b[0m\x1b[2J\x1b[H")
        output_stream.flush()


def accept_decline(input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    fd = input_stream.fileno()
    if not os.isatty(fd) or not output_stream.isatty():
        raise OSError("Open an interactive terminal to use the Accept/Decline buttons.")
    previous = termios.tcgetattr(fd)
    accepted = False

    def draw():
        accept = "\x1b[7m Accept \x1b[0m" if accepted else " Accept "
        decline = " Decline " if accepted else "\x1b[7m Decline \x1b[0m"
        output_stream.write("\r\x1b[2K  [" + accept + "]   [" + decline + "]")
        output_stream.flush()

    output_stream.write("Use Left/Right or Tab to choose, then press Enter. Esc leaves this step.\n")
    output_stream.flush()
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        draw()
        while True:
            key = os.read(fd, 1)
            if key in (b"\r", b"\n"):
                return accepted
            if key in (b"", b"\x04", b"q", b"Q"):
                return False
            if key == b"\t":
                accepted = not accepted
            elif key == b"\x1b":
                sequence = b""
                while len(sequence) < 3 and select.select([fd], [], [], 0.08)[0]:
                    sequence += os.read(fd, 1)
                    if sequence in (b"[A", b"[B", b"[C", b"[D", b"[Z", b"OA", b"OB", b"OC", b"OD"):
                        break
                if not sequence:
                    return False
                if sequence in (b"[D", b"OD"):
                    accepted = True
                elif sequence in (b"[C", b"OC"):
                    accepted = False
                elif sequence in (b"[A", b"[B", b"[Z", b"OA", b"OB"):
                    accepted = not accepted
            draw()
    finally:
        # Discard queued keys so one press/paste cannot approve another step.
        termios.tcsetattr(fd, termios.TCSAFLUSH, previous)
        output_stream.write("\x1b[0m\n")
        output_stream.flush()
