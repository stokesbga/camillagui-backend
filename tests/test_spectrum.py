import asyncio
import io
import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from aiohttp import web
from jsonschema import Draft202012Validator

from backend.spectrum import (
    HEADER,
    MAGIC,
    SPECTRUM_KEY,
    SpectrumReceiver,
    decode_pcm,
    get_spectrum,
    setup_spectrum,
)
from backend.spectrum_tap import forward_audio
from backend.settings_schemas import BACKEND_CONFIG_SCHEMA


def packet(samples, sequence=0, rate=48000, stream=1):
    return (
        HEADER.pack(MAGIC, rate, samples.shape[1], 5, sequence, stream)
        + samples.astype("<f4").tobytes()
    )


def feed(receiver, samples, first_sequence=0, **kwargs):
    for index, start in enumerate(range(0, len(samples), 256)):
        receiver.datagram_received(
            packet(samples[start : start + 256], first_sequence + index, **kwargs), None
        )


def tone(count=8192):
    mono = 0.5 * np.sin(2 * np.pi * 128 * np.arange(count) / 8192)
    return np.stack([mono, -mono], axis=1)


@pytest.mark.parametrize(
    "format_id,payload",
    [
        (1, np.array([16384, -16384], dtype="<i2").tobytes()),
        (2, bytes([0, 0, 64, 0, 0, 192])),
        (3, np.array([4194304, -4194304], dtype="<i4").tobytes()),
        (4, np.array([1073741824, -1073741824], dtype="<i4").tobytes()),
        (5, np.array([0.5, -0.5], dtype="<f4").tobytes()),
        (6, np.array([0.5, -0.5], dtype="<f8").tobytes()),
    ],
)
def test_pcm_decoding(format_id, payload):
    np.testing.assert_allclose(decode_pcm(payload, format_id, 2), [[0.5, -0.5]])


def test_fft_frequency_level_and_opposite_phase_channels():
    receiver = SpectrumReceiver(True)
    feed(receiver, tone())
    frame = receiver.snapshot()
    assert frame["available"] is True
    assert np.argmax(frame["bins"]) == 128
    assert frame["bins"][128] == pytest.approx(-6.02, abs=0.03)
    assert frame["bins"][128] == receiver.snapshot(channel=0)["bins"][128]
    assert frame["channels"] == 2
    assert receiver.snapshot() is frame  # Same audio snapshot is shared across clients.


def test_silence_and_ring_wrap():
    receiver = SpectrumReceiver(True)
    feed(receiver, tone(40960))
    assert receiver.snapshot()["bins"][128] == pytest.approx(-6.02, abs=0.03)
    feed(receiver, np.zeros((32768, 2)), first_sequence=160)
    assert all(level == -120 for level in receiver.snapshot()["bins"])


def test_gaps_restarts_and_format_changes_never_join_audio():
    receiver = SpectrumReceiver(True)
    feed(receiver, tone())
    receiver.datagram_received(packet(tone(256), sequence=99), None)
    assert receiver.snapshot()["reason"] == "buffering"
    feed(receiver, tone(), first_sequence=100)
    assert receiver.snapshot()["available"]
    receiver.datagram_received(packet(tone(256), rate=96000, sequence=132), None)
    assert receiver.snapshot()["reason"] == "buffering"
    receiver.datagram_received(packet(tone(256), stream=2, sequence=0), None)
    assert receiver.count == 256


def test_disabled_stale_and_bad_packets():
    assert SpectrumReceiver().snapshot()["reason"] == "disabled"
    receiver = SpectrumReceiver(True)
    for bad in [
        b"broken",
        HEADER.pack(MAGIC, 48000, 0, 5, 0, 1) + b"1234",
        packet(np.full((2, 2), np.nan)),
    ]:
        receiver.datagram_received(bad, None)
    assert receiver.snapshot()["reason"] == "waiting"
    feed(receiver, tone())
    receiver.updated -= 2
    assert receiver.snapshot()["reason"] == "waiting"
    receiver.datagram_received(packet(tone(256), sequence=32), None)
    assert receiver.snapshot()["reason"] == "buffering"


def test_helper_preserves_pcm_and_drops_packets_when_backend_unavailable():
    sock = Mock()
    raw = tone().astype("<f4").tobytes()
    forward_audio(io.BytesIO(raw), sock, 48000, 2, "FLOAT_LE", 9878)
    packets = [call.args[0] for call in sock.sendto.call_args_list]
    assert b"".join(item[HEADER.size :] for item in packets) == raw
    assert [HEADER.unpack_from(item)[4] for item in packets] == list(
        range(len(packets))
    )
    sock.sendto.side_effect = BlockingIOError()
    source = io.BytesIO(raw)
    forward_audio(source, sock, 48000, 2, "FLOAT_LE", 9878)
    assert source.tell() == len(raw)


async def test_http_stream_from_real_udp_and_cleanup(aiohttp_client):
    app = web.Application()
    setup_spectrum(app, {"enabled": True, "port": 0})
    app.router.add_get("/api/spectrum", get_spectrum)
    client = await aiohttp_client(app)
    receiver = app[SPECTRUM_KEY]
    assert receiver.transport.get_extra_info("sockname")[0] == "127.0.0.1"
    port = receiver.transport.get_extra_info("sockname")[1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for index, start in enumerate(range(0, 8192, 256)):
            sock.sendto(packet(tone()[start : start + 256], index), ("127.0.0.1", port))
            await asyncio.sleep(0)
    for _ in range(20):
        response = await client.get("/api/spectrum")
        body = await response.json()
        if body["available"]:
            break
        await asyncio.sleep(0.01)
    assert body["bins"][128] == pytest.approx(-6.02, abs=0.03)
    assert response.headers["Cache-Control"] == "no-store"
    for query in [
        "fft_size=17",
        "fft_size=abc",
        "channel=-1",
        "channel=2",
        "channel=99",
    ]:
        response = await client.get("/api/spectrum?" + query)
        assert response.status == 400
    response = await client.get("/api/spectrum?fft_size=2048&channel=1")
    assert (await response.json())["available"]
    await client.close()
    assert receiver.transport.is_closing()


async def test_disabled_endpoint_does_not_open_a_socket(aiohttp_client):
    app = web.Application()
    setup_spectrum(app, None)
    app.router.add_get("/api/spectrum", get_spectrum)
    client = await aiohttp_client(app)
    assert (await (await client.get("/api/spectrum")).json())["reason"] == "disabled"
    assert app[SPECTRUM_KEY].transport is None


def test_invalid_spectrum_settings():
    schema = BACKEND_CONFIG_SCHEMA["properties"]["spectrum"]
    validator = Draft202012Validator(schema)
    for config in [
        {"port": 0},
        {"port": 65536},
        {"enabled": "yes"},
        {"host": "0.0.0.0"},
    ]:
        assert list(validator.iter_errors(config))
    assert not list(validator.iter_errors({"enabled": True, "port": 9878}))


async def test_standalone_alsa_helper_to_http(aiohttp_client):
    app = web.Application()
    setup_spectrum(app, {"enabled": True, "port": 0})
    app.router.add_get("/api/spectrum", get_spectrum)
    client = await aiohttp_client(app)
    port = app[SPECTRUM_KEY].transport.get_extra_info("sockname")[1]
    helper = Path(__file__).parent.parent / "backend" / "spectrum_tap.py"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(helper),
        "--rate",
        "48000",
        "--channels",
        "2",
        "--format",
        "FLOAT_LE",
        "--port",
        str(port),
        stdin=asyncio.subprocess.PIPE,
    )
    await process.communicate(tone(2048).astype("<f4").tobytes())
    assert process.returncode == 0
    response = await client.get("/api/spectrum?fft_size=2048")
    result = await response.json()
    assert result["available"]
    assert np.argmax(result["bins"]) == 32
    assert result["bins"][32] == pytest.approx(-6.02, abs=0.03)
