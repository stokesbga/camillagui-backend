"""Post-DSP spectrum from a localhost PCM tap. No audio devices are opened here."""

import asyncio
import logging
import struct
import time

import numpy as np
from aiohttp import web

HEADER = struct.Struct(
    "!4sIHHII"
)  # magic, sample rate, channels, format, sequence, stream id
MAGIC = b"CSP1"
FORMATS = {
    1: ("<i2", 2, 32768),
    2: (None, 3, 8388608),
    3: ("<i4", 4, 8388608),
    4: ("<i4", 4, 2147483648),
    5: ("<f4", 4, 1),
    6: ("<f8", 8, 1),
}
FFT_SIZES = (2048, 8192, 32768)
MAX_SAMPLES = max(FFT_SIZES)
SPECTRUM_KEY = web.AppKey("spectrum", object)


def decode_pcm(payload, format_id, channels):
    dtype, width, scale = FORMATS[format_id]
    if not payload or len(payload) % (width * channels):
        raise ValueError("Incomplete PCM frame")
    if format_id == 2:
        packed = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        samples = packed[:, 0] | (packed[:, 1] << 8) | (packed[:, 2] << 16)
        samples = (samples ^ 0x800000) - 0x800000
    else:
        samples = np.frombuffer(payload, dtype=dtype)
        if format_id == 3:
            samples = ((samples & 0xFFFFFF) ^ 0x800000) - 0x800000
    samples = samples.astype(np.float64).reshape(-1, channels) / scale
    if not np.isfinite(samples).all():
        raise ValueError("Non-finite PCM")
    return samples


class SpectrumReceiver(asyncio.DatagramProtocol):
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.error = None
        self.transport = None
        self.buffer = None
        self.identity = None
        self.sequence = None
        self.position = 0
        self.count = 0
        self.updated = 0
        self.cache = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, packet, _address):
        if len(packet) <= HEADER.size:
            return
        magic, rate, channels, fmt, sequence, stream = HEADER.unpack_from(packet)
        if (
            magic != MAGIC
            or not 8000 <= rate <= 768000
            or not 1 <= channels <= 64
            or fmt not in FORMATS
        ):
            return
        try:
            samples = decode_pcm(packet[HEADER.size :], fmt, channels)
        except ValueError:
            return
        identity = (stream, rate, channels, fmt)
        # Never combine windows across dropped packets, restarts or format changes.
        if (
            identity != self.identity
            or time.monotonic() - self.updated > 1.0
            or sequence != ((self.sequence or 0) + 1) % (2**32)
        ):
            self.position = self.count = 0
        if identity != self.identity:
            self.buffer = np.zeros((MAX_SAMPLES, channels))
        self.identity = identity
        self.sequence = sequence
        samples = samples[-MAX_SAMPLES:]
        first = min(len(samples), MAX_SAMPLES - self.position)
        self.buffer[self.position : self.position + first] = samples[:first]
        self.buffer[: len(samples) - first] = samples[first:]
        self.position = (self.position + len(samples)) % MAX_SAMPLES
        self.count = min(MAX_SAMPLES, self.count + len(samples))
        self.updated = time.monotonic()
        self.cache.clear()

    def snapshot(self, fft_size=8192, channel=None):
        base = {"available": False, "source": "Master output", "fft_size": fft_size}
        if not self.enabled:
            return {
                **base,
                "reason": "disabled",
                "message": "Enable the spectrum tap in the backend configuration.",
            }
        if self.error:
            return {**base, "reason": "error", "message": self.error}
        if self.identity is None or time.monotonic() - self.updated > 1.0:
            return {
                **base,
                "reason": "waiting",
                "message": "Waiting for the ALSA playback tap.",
            }
        _, rate, channels, _ = self.identity
        base.update({"sample_rate": rate, "channels": channels})
        if channel is not None and channel >= channels:
            raise ValueError("Channel is outside the playback channel range")
        if self.count < fft_size:
            return {**base, "reason": "buffering", "message": "Collecting audio…"}
        key = (fft_size, channel)
        if key in self.cache:
            return self.cache[key]
        indexes = (np.arange(fft_size) + self.position - fft_size) % MAX_SAMPLES
        samples = self.buffer[indexes]
        if channel is not None:
            samples = samples[:, channel : channel + 1]
        window = np.hanning(fft_size)
        amplitude = np.abs(np.fft.rfft(samples * window[:, None], axis=0)) * (
            2.0 / window.sum()
        )
        amplitude[0] *= 0.5
        amplitude[-1] *= 0.5
        # Average power across channels, avoiding cancellation of opposite-phase audio.
        power = np.mean(amplitude**2, axis=1)
        bins = np.maximum(-120, 10 * np.log10(np.maximum(power, 1e-12)))
        result = {
            **base,
            "available": True,
            "sequence": self.sequence,
            "channel": channel,
            "bins": np.round(bins, 2).tolist(),
        }
        self.cache[key] = result
        return result


async def get_spectrum(request):
    try:
        fft_size = int(request.query.get("fft_size", "8192"))
        channel_value = request.query.get("channel", "all")
        channel = None if channel_value == "all" else int(channel_value)
        if fft_size not in FFT_SIZES or (channel is not None and not 0 <= channel < 64):
            raise ValueError("Invalid FFT size or channel")
        result = request.app[SPECTRUM_KEY].snapshot(fft_size, channel)
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    return web.json_response(result, headers={"Cache-Control": "no-store"})


def setup_spectrum(app, config):
    config = config or {}
    receiver = SpectrumReceiver(config.get("enabled", False))
    app[SPECTRUM_KEY] = receiver

    async def lifecycle(_app):
        if receiver.enabled:
            try:
                await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: receiver, local_addr=("127.0.0.1", config.get("port", 9878))
                )
            except OSError as error:
                receiver.error = (
                    "Cannot bind the spectrum tap port. Check the backend log."
                )
                logging.error("Spectrum tap: %s", error)
        yield
        if receiver.transport:
            receiver.transport.close()

    app.cleanup_ctx.append(lifecycle)
