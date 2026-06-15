# SIP Indoor Station

Minimal SIP server and WebRTC audio bridge for SIP-capable home door stations on a LAN. It is meant to be a simple direct setup for home deployments where running a full PBX such as Asterisk would be unnecessary overhead.

The project is intended to stay vendor-neutral at the SIP/WebRTC layer. 

It has currently only been tested with HikVision DS-KV6113-WPE1(B) firmware 2.2.53.

This project currently implements:

- SIP UDP listener on `0.0.0.0:5060` by default
- SIP message parsing/building
- Digest-authenticated `REGISTER`
- Optional persistent registration store
- Basic `OPTIONS`, `INVITE`, `ACK`, `BYE`, and `CANCEL` handling
- SDP parsing and a minimal PCMA/PCMU SDP answer with video rejection
- GStreamer WebRTC audio bridge for PCMU/8000 / PCMA/8000 RTP audio

It intentionally does not implement tablet UI, H264 video bridging, or a full RFC-complete SIP stack. Vendor-specific features should stay optional; HikVision ISAPI support is currently used only for optional door control and maintenance actions.

## Run

```bash
pip install -e .
sip-indoor-station
```

Common environment variables:

```bash
export LISTEN_ADDRESS=0.0.0.0
export SIP_ADVERTISED_ADDRESS=
export SIP_PORT=5060
export SIP_REALM=sip.local
export SIP_USERNAME=door
export SIP_PASSWORD=change-me
export SIP_NONCE_TTL=300
export SIP_REGISTRATION_TTL=3600
export SIP_REGISTRATION_STORE_PATH=
export RTP_PORT_MIN=40000
export RTP_PORT_MAX=40100
sip-indoor-station
```

Set `SIP_REGISTRATION_STORE_PATH` to a JSON file path to persist unexpired SIP registrations across restarts. The Home Assistant add-on sets this to `/data/sip_registrations.json`.

Set `SIP_ADVERTISED_ADDRESS` when `LISTEN_ADDRESS` is `0.0.0.0` but SIP/SDP must advertise a reachable LAN address. In Docker or Home Assistant add-on setups this is usually the Home Assistant host address, for example `192.168.8.3`.

## Home Assistant Add-on

The Home Assistant add-on is maintained in a separate add-on repository:

```text
https://github.com/arturzx/sip-indoor-station-addon
```

## Door Station SIP Account

Configure the door station as a SIP client:

- SIP server / registrar: Host where SIP indoor station software running
- SIP server port: `5060`
- Transport: UDP
- SIP username / extension: value of `SIP_USERNAME`
- SIP authentication username: value of `SIP_USERNAME`
- SIP authentication password: value of `SIP_PASSWORD`
- SIP realm/domain: value of `SIP_REALM`

## HTTP API

Install runtime dependencies:

```bash
pip install -e .
```

The service exposes a HTTP API for control and WebRTC signaling. Is small site for debugging purposes.

State endpoint:

```text
GET /api/state
```

The response includes `version`, `registered`, `ringing`, `in_call`, `call_state`, registration data, selected codec data, and last event metadata.

Command endpoints:

- `POST /api/answer`
- `POST /api/reject`
- `POST /api/hangup`
- `POST /api/open_door`
- `POST /api/reboot`

State updates:

```text
GET /api/ws
```

The WebSocket sends the current state immediately after connection:

```json
{"type":"state","state":{"registered":false,"ringing":false,"in_call":false,"call_state":"idle"}}
```

It then sends `state` messages and `event` messages after internal SIP/ISAPI events. WebRTC media signaling remains separate on `/webrtc/ws`.

### Optional HikVision ISAPI

ISAPI is disabled by default and is vendor-specific. When enabled, `POST /api/open_door` calls the HikVision ISAPI remote door-control endpoint. Local ISAPI access must be enabled on the HikVision device.

Configure the door station HTTP API credentials:

```bash
export ISAPI_ENABLED=true
export ISAPI_HOST=192.168.8.163
export ISAPI_PORT=80
export ISAPI_USERNAME=admin
export ISAPI_PASSWORD=change-me
export ISAPI_USE_HTTPS=false
export ISAPI_TIMEOUT_SECONDS=5
export ISAPI_VERIFY_SSL=false
export ISAPI_DOOR_ID=1
```

The request sent is:

```text
PUT /ISAPI/AccessControl/RemoteControl/door/1
Content-Type: application/xml
```

with:

```xml
<?xml version="1.0" encoding="UTF-8"?><RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>
```

SIP remains responsible for call handling. ISAPI is currently used for door control plus optional status and fallback call-signal helpers.

## WebRTC Audio Bridge

The media bridge is audio-only. It bridges:

```text
Door station RTP PCMU/8000 or PCMA/8000
<-> GStreamer
<-> WebRTC Opus audio
<-> browser
```

Video is not implemented. If the station offers H264 video, the SIP SDP answer explicitly rejects it with `m=video 0`.

Required Debian/Ubuntu/Home Assistant host packages:

```bash
sudo apt install \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  python3-gi \
  gir1.2-gstreamer-1.0
```

Python dependencies:

```bash
pip install -e .
```

If your Python environment does not use the system `python3-gi`, install PyGObject according to your distribution’s guidance. The app self-checks required GStreamer elements before creating the real bridge and reports missing elements clearly.

Common WebRTC/media environment variables:

```bash
export LISTEN_ADDRESS=0.0.0.0
export SIP_ADVERTISED_ADDRESS=
export RTP_PORT_MIN=40000
export RTP_PORT_MAX=40100
export RTP_JITTER_BUFFER_MS=60
export HTTP_PORT=8080
export WEBRTC_SINGLE_PEER=true
export WEBRTC_STUN_SERVERS=
export WEBRTC_TURN_SERVERS=
export WEBRTC_TURN_USERNAME=
export WEBRTC_TURN_PASSWORD=
export WEBRTC_ICE_TRANSPORT_POLICY=all
export WEBRTC_ICE_CANDIDATES=
export WEBRTC_ICE_UDP_PORT=
```

ICE configuration:

- `WEBRTC_ICE_CANDIDATES`, `WEBRTC_STUN_SERVERS` and `WEBRTC_TURN_SERVERS` accept comma-separated lists.
- `WEBRTC_ICE_TRANSPORT_POLICY=relay` forces both browser and GStreamer toward TURN relay candidates.
- `WEBRTC_ICE_CANDIDATES=192.168.0.2,10.0.0.20:18556` prepends one GStreamer host ICE candidate for each listed host while keeping the original candidate. A plain host keeps the original ICE port; `host:port` overrides the port for that candidate. This can help when running in Docker with host-reachable networking.
- `WEBRTC_ICE_UDP_PORT=8555` forces GStreamer/libnice to use one fixed UDP port for local WebRTC ICE host candidates. Publish/forward this UDP port when running behind Docker or NAT.

Example with multiple STUN servers:

```bash
export WEBRTC_STUN_SERVERS=stun:stun1.example.com:3478,stun:stun2.example.com:3478
```

The browser receives all configured STUN servers. GStreamer `webrtcbin` currently exposes one `stun-server` property, so the bridge uses the first configured STUN server for GStreamer and logs when additional STUN servers are present.

For port-forwarded or NAT-crossing access, prefer a TURN server and `WEBRTC_ICE_TRANSPORT_POLICY=relay`. For direct host candidates without TURN, set `WEBRTC_ICE_UDP_PORT` and forward that UDP port; forwarding only the HTTP/WebSocket port is not enough.

Open the browser demo during an active answered SIP call:

```text
http://<sip-indoor-station-host>:8080/
```

Click `Connect WebRTC`, allow microphone access, and the browser will exchange audio with the door station through GStreamer.
