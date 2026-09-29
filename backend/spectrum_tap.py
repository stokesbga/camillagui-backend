#!/usr/bin/env python3
"""ALSA file-plugin helper. Drain stdin even when the GUI backend is unavailable.

Standalone, standard-library only; install this file on the CamillaDSP host.
"""

import argparse
import os
import socket
import struct
import sys

HEADER = struct.Struct("!4sIHHII")
FORMATS = {
    "S16_LE": (1, 2),
    "S24_3LE": (2, 3),
    "S24_LE": (3, 4),
    "S32_LE": (4, 4),
    "FLOAT_LE": (5, 4),
    "FLOAT64_LE": (6, 8),
}


def forward_audio(source, sock, rate, channels, sample_format, port):
    format_id, width = FORMATS[sample_format]
    frame_size = width * channels
    block_size = max(1, 4096 // frame_size) * frame_size
    stream = int.from_bytes(os.urandom(4), "big")
    sequence = 0
    pending = b""
    while True:
        block = source.read(block_size)
        if not block:
            break
        pending += block
        size = len(pending) // frame_size * frame_size
        if size:
            header = HEADER.pack(b"CSP1", rate, channels, format_id, sequence, stream)
            if sock is not None:
                try:
                    sock.sendto(header + pending[:size], ("127.0.0.1", port))
                except OSError:
                    pass  # Drop analysis data; never wait for the backend or a browser.
            pending = pending[size:]
            sequence = (sequence + 1) % (2**32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--channels", type=int, required=True)
    parser.add_argument("--format", required=True)
    parser.add_argument("--port", type=int, default=9878)
    args = parser.parse_args()
    sock = None
    try:
        if (
            args.format not in FORMATS
            or not 1 <= args.channels <= 64
            or not 8000 <= args.rate <= 768000
            or not 1 <= args.port <= 65535
        ):
            print(
                "Spectrum tap: unsupported stream parameters; analysis disabled",
                file=sys.stderr,
            )
            while sys.stdin.buffer.read(4096):
                pass
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
        except OSError:
            print("Spectrum tap: UDP unavailable; analysis disabled", file=sys.stderr)
            if sock:
                sock.close()
            sock = None
        forward_audio(
            sys.stdin.buffer, sock, args.rate, args.channels, args.format, args.port
        )
    finally:
        if sock:
            sock.close()


if __name__ == "__main__":
    main()
