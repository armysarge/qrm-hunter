# QRM Hunter

See, record, identify and hunt down HF interference (QRM) with an RTL-SDR or any SoapySDR radio.

Built by a ham for hams who want to know whether that S7 noise floor is an inverter, a phone charger or the neighbour's VDSL. Right now it gives you a fast live spectrum and waterfall with click-to-tune, zoom/pan, HF band presets, AM/SSB/CW/FM audio and frequency memories. It runs as a desktop window or headless on a Raspberry Pi. Recording with automatic QRM identification, a 24/7 logger, step-by-step source hunting and a shared library of known QRM signatures are on the way.

![Live spectrum and waterfall on 40 m](assets/screenshot.png)

## Supported hardware

| Device | How |
|---|---|
| RTL-SDR v3 / v4 / clones | Built in (pyrtlsdr). A v3 or clone switches to Direct Q below 24 MHz on its own; a v4 uses its built-in upconverter. Windows: install the WinUSB driver with [Zadig](https://zadig.akeo.ie/). |
| SDRplay, Airspy, HackRF, others | Through SoapySDR plus the device's Soapy module (see below). |
| No hardware | **Synthetic demo**, a built-in generator of typical QRM (SMPS combs, mains buzz, VDSL, impulses). |

### Installing SoapySDR (optional)

SoapySDR is not on PyPI. Pick one:
- **Windows:** install [radioconda](https://github.com/ryanvolz/radioconda) or PothosSDR, then make sure its Python bindings are importable from the app's environment.
- **Linux (Debian/Ubuntu/Raspberry Pi OS):** `sudo apt install python3-soapysdr soapysdr-module-all`, then create the venv with `uv venv --system-site-packages` so the app can see the system bindings.
- **conda:** `conda install -c conda-forge soapysdr soapysdr-module-airspy soapysdr-module-hackrf`. SDRplay also needs the SDRplay API and the `soapysdr-module-sdrplay3` plugin.

## Run

```bash
uv sync
cd frontend && npm ci && npm run build && cd ..
uv run qrm-hunter            # desktop window
uv run qrm-hunter --serve    # headless: open http://127.0.0.1:7070
```

To reach a headless Pi from another machine, run `uv run qrm-hunter --serve --host 0.0.0.0`. It prints a URL containing an access token; keep that token private.

Other options: `--port` (default 7070), `--config` (settings file) and `--log-level`.

## Develop

```bash
uv run pytest                     # backend tests (add -m hardware with an SDR attached)
cd frontend
npm test && npm run check
npm run dev                       # UI on :5173, proxied to a backend on :7070 (uv run qrm-hunter --serve)
```

Settings are stored in your per-user config directory (`settings.toml`).

## Author

Shaun Scholtz, **ZS6SHZ**. Bug reports, QRM captures and pull requests are welcome.

73!
