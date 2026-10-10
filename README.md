# QRM Hunter

See, record, identify and hunt down HF interference (QRM) with common SDRs.

Sub-project 1 (this release): a live spectrum and waterfall with click-to-tune, zoom/pan, HF band presets and AM/SSB/CW audio. It runs as a desktop window or headless on a Raspberry Pi. Recording, automatic QRM classification, a 24/7 logger, guided source hunting and a community QRM library.

![Live spectrum and waterfall on 40 m](docs/screenshots/sp1-40m.png)

## Supported hardware

| Device | How |
|---|---|
| RTL-SDR v3 / v4 / clones | Built in (pyrtlsdr). v3: set **HF → Direct Q** below 24 MHz. v4: leave on **Tuner**. Windows: install the WinUSB driver with [Zadig](https://zadig.akeo.ie/). |
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
