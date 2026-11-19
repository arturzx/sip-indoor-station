# Changelog

All notable changes in this fork are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The fork is based on
[arturzx/sip-indoor-station](https://github.com/arturzx/sip-indoor-station) v0.1.11.

## [Unreleased]

### Added

- **Akuvox door station support** (tested with an Akuvox R20A), `DOOR_STATION_VENDOR=akuvox`:
  - Open relays with SIP INFO DTMF digits (`AKUVOX_RELAY1_DTMF`, default `*`, and
    `AKUVOX_RELAY2_DTMF`, default `#`), so the device HTTP API does not have to be enabled.
  - ONVIF snapshots through the shared ONVIF provider, with configurable `ONVIF_PORT` and
    `ONVIF_PROFILE_INDEX`.
  - `GET /api/snapshot` test endpoint.
- `LOG_LEVEL` setting.
- `media_prepare_duration` and `gstreamer_warmup_done` log entries for measuring call setup time.
- Tests for Akuvox keepalive handling, the ONVIF snapshot client and media pre-warming.

### Changed

- **Faster call answering.** The WebRTC media pipeline is now built in a worker thread as soon as the
  INVITE is accepted, while the door station is still ringing, instead of when the call is answered.
  On a real call `answer` to SIP 200 OK dropped from about 3.4 s to about 1 ms, and `answer` to the first
  WebRTC audio from about 4.7 s to about 1.0 s.
- GStreamer, its plugins and the key element factories are loaded once at startup, and a successful
  element check is cached.
- `GStreamerWebRtcBridge.prepare()` runs in an executor under a lock, so it no longer blocks SIP and
  HTTP handling. `stop()` waits for a prepare in progress and marks the session stopped, so a late
  pre-warm cannot leak a pipeline.
- ONVIF snapshots: SOAP 1.1, media XAddr parsing, `GetProfiles` fallback and WSSE authentication.

### Fixed

- Akuvox devices send short binary keepalive packets (containing NUL bytes) to the SIP port. They are
  now ignored at debug level instead of logging a parse warning about every 30 seconds.
- Test input for the malformed-SIP case now really fails to parse instead of producing a
  `405 Method Not Allowed` response.
