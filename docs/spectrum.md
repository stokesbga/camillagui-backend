# Master-output spectrum (ALSA / HiFiBerry)

The frontend opens Live spectrum on Home and polls `/api/spectrum` automatically.
The existing CamillaDSP control API supplies RMS/peak values, not PCM or FFT data.
This backend adds an optional copy of the actual post-DSP playback stream, using
ALSA's `file` PCM plugin. The underlying playback PCM stays the same.

The GUI backend and CamillaDSP must run on the **same Linux host** for this setup.
The browser may run elsewhere. No microphone permission or input selection is needed.
This does not measure the DAC's analog output, amplifier, speakers, or room.

## Setup on the Linux host

1. Deploy the updated backend and frontend. Enable this in the backend's
   `config/camillagui.yml` (or the config passed with `-c`), then restart the backend:

   ```yaml
   spectrum:
     enabled: true
     port: 9878
   ```

2. Install `backend/spectrum_tap.py` at a stable absolute path readable by the
   CamillaDSP service user. It usevs only Python 3's standard library and can be
   copied independently of the rest of the backend. Release bundles include it as
   `camillagui_backend/spectrum_tap.py`; use that installed absolute path in the
   ALSA command below. Check `/usr/bin/python3` exists.

3. Add a named PCM to the CamillaDSP service user's `.asoundrc`, or to
   `/etc/asound.conf` if the service has no home configuration. Preserve all
   existing definitions. Replace **both placeholders** below:

   ```conf
   pcm.camilla_spectrum {
       type file
       slave.pcm "YOUR_EXISTING_ALSA_PLAYBACK_PCM"
       file "|/usr/bin/python3 /ABSOLUTE/PATH/backend/spectrum_tap.py --rate %r --channels %c --format %f --port 9878"
       format "raw"
   }
   ```

   `YOUR_EXISTING_ALSA_PLAYBACK_PCM` is the exact device currently shown under
   **Devices → Playback → Device**, e.g. a `hw:...` or `plughw:...` HiFiBerry PCM.
   Do not use `camilla_spectrum` as its own slave. ALSA substitutes `%r`, `%c`, and
   `%f` with the real sample rate, channel count, and sample format.

4. Save a copy of the working DSP configuration. In **Devices → Playback**, keep
   type `Alsa`, sample format, and channel count unchanged; change only Device to
   `camilla_spectrum`. Apply the configuration. This reopens the playback device
   and can interrupt playback briefly. Home should show **Live · post-DSP** when
   audio is playing. Select individual output channels or their power average.

The helper continually drains ALSA's pipe and uses nonblocking UDP. It drops
analysis packets when the receiver is unavailable; it does not wait for browsers.
A missing helper, wrong Python path, or a stalled helper can still affect playback:
ALSA's pipe is in the playback path. Verify normal playback and CPU/underrun behavior
on the actual HiFiBerry host, including while the backend is stopped, before relying
on this configuration. No ALSA or live DSP configuration is changed by installing
the code or merely opening the GUI.

## Formats and behavior

Supported ALSA format names: `S16_LE`, `S24_3LE`, `S24_LE` (24 bits in a 32-bit
container), `S32_LE`, `FLOAT_LE`, and `FLOAT64_LE`. Unsupported formats are drained
without forwarding audio, and logged once on the helper's stderr.
Up to 64 channels and 8–768 kHz are accepted. The receiver listens only on IPv4
localhost. The helper and receiver ports must match; one playback tap should feed
a given receiver. Only FFT values, not raw audio, are sent to browsers.

The backend keeps at most 32,768 frames per channel in memory and retains no audio
files. Packet loss, playback restarts, and format changes reset the analysis window.
A stream with no data for one second is marked unavailable; silent but continuing
PCM produces a -120 dBFS floor. CamillaDSP's silence pause may stop the tap until
playback resumes. The GUI recovers automatically.

FFT sizes: 2,048 / 8,192 / 32,768. A Hann window is amplitude-normalized: a coherent
full-scale sine reads 0 dBFS. `All outputs` averages spectral **power**, avoiding
cancellation between opposite-phase channels. A tone in only one of N equally
weighted channels reads 10·log10(N) dB lower in the average than in that channel.
Smoothing, peak hold and display freeze are frontend operations. Freezing keeps
polling but holds the displayed/exported frame.

## API

`GET /api/spectrum?fft_size=8192&channel=all` (`channel` may also be a zero-based index).
Successful frames include `available: true`, `source`, `sample_rate`, `fft_size`,
`channels`, `channel`, `sequence`, and `bins` in dBFS, including DC and Nyquist.
Bin k corresponds to k·sample_rate/fft_size Hz. Responses use `Cache-Control: no-store`.
Unavailable states return `available: false`, a `reason`, and a short `message`.
Invalid FFT sizes or channel indexes return HTTP 400. The feature is disabled by
default, so existing installations continue to start without a tap or open UDP port.

The local tap protocol is a big-endian `!4sIHHII` header: magic `CSP1`, sample rate,
channel count, format ID, wrapping uint32 sequence, and random uint32 stream ID.
Interleaved little-endian PCM follows. Format IDs 1–6 match the order above. The
helper sends frame-aligned packets of at most 4,096 PCM bytes. This interface is
local-only; do not expose the raw receiver to the network.

## Verify and revert

Check `curl 'http://127.0.0.1:5005/api/spectrum?channel=all'` on the host.
`disabled`: enable backend configuration. `waiting`: check the helper's path,
service permissions, ALSA config, matching port, and that playback is running.
`buffering`: waiting for one full contiguous FFT window. `error`: inspect backend
logs for a port-binding failure.

To revert, restore the previous playback Device in CamillaDSP and apply it. Set
`spectrum.enabled: false` and restart the backend. The named ALSA PCM can remain
unused. No loopback kernel module or replacement DSP build is required.

Reference: [ALSA file-plugin documentation and implementation](https://github.com/alsa-project/alsa-lib/blob/master/src/pcm/pcm_file.c).
