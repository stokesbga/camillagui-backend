"""Check the frozen server, static frontend and FFT without a CamillaDSP device."""

import json
import math
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def unused_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def smoke_test(bundle):
    executable = bundle / "camillagui_backend"
    if not executable.exists():
        executable = executable.with_suffix(".exe")
    subprocess.run(
        [str(executable), "--help"], check=True, capture_output=True, timeout=30
    )
    http_port = unused_port(socket.SOCK_STREAM)
    udp_port = unused_port(socket.SOCK_DGRAM)
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        # JSON is valid YAML; all mutable paths stay in this temporary directory.
        config = {
            "camilla_host": "127.0.0.1",
            "camilla_port": unused_port(socket.SOCK_STREAM),
            "bind_address": "127.0.0.1",
            "port": http_port,
            "config_dir": str(temp),
            "coeff_dir": str(temp),
            "gui_config_file": None,
            "default_config": None,
            "statefile_path": None,
            "spectrum": {"enabled": True, "port": udp_port},
        }
        config_path = temp / "config.yml"
        config_path.write_text(json.dumps(config))
        with (temp / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [str(executable), "-c", str(config_path)],
                cwd=temp,
                stdout=log,
                stderr=log,
            )
            base = f"http://127.0.0.1:{http_port}"
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Frozen server exited during startup")
                    try:
                        with urllib.request.urlopen(base + "/", timeout=1) as response:
                            html = response.read().decode()
                            document_url = response.url
                        if '<div id="root"' not in html:
                            raise RuntimeError("Frontend index did not load")
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() > deadline:
                            raise RuntimeError(
                                "Frozen server failed to start"
                            ) from None
                        time.sleep(0.1)
                assets = re.findall(r'(?:src|href)="([^\"]+\.(?:js|css))"', html)
                if not assets:
                    raise RuntimeError("Frontend index has no compiled assets")
                for asset in assets:
                    url = urllib.parse.urljoin(document_url, asset)
                    with urllib.request.urlopen(url, timeout=5) as response:
                        if not response.read():
                            raise RuntimeError(f"Empty frontend asset: {asset}")
                # Exercise the shipped ALSA helper and the bundled NumPy FFT together.
                samples = [
                    0.5 * math.sin(2 * math.pi * 750 * n / 48000) for n in range(8192)
                ]
                pcm = struct.pack(f"<{len(samples)}f", *samples)
                subprocess.run(
                    [
                        sys.executable,
                        str(bundle / "spectrum_tap.py"),
                        "--rate",
                        "48000",
                        "--channels",
                        "1",
                        "--format",
                        "FLOAT_LE",
                        "--port",
                        str(udp_port),
                    ],
                    input=pcm,
                    check=True,
                    timeout=10,
                )
                deadline = time.monotonic() + 5
                while True:
                    with urllib.request.urlopen(
                        base + "/api/spectrum?fft_size=2048&channel=all", timeout=2
                    ) as response:
                        frame = json.load(response)
                    if frame.get("available"):
                        bins = frame["bins"]
                        peak = max(range(len(bins)), key=bins.__getitem__)
                        if peak != 32 or abs(bins[peak] + 6.0206) > 0.2:
                            raise RuntimeError(
                                f"Unexpected FFT peak: {peak}, {bins[peak]}"
                            )
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"Frozen FFT did not produce data: {frame}")
                    time.sleep(0.05)
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print(
        "Frozen bundle smoke test passed: frontend, ALSA helper, 750 Hz / -6.02 dBFS FFT"
    )
