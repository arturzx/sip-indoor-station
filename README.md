# SIP Indoor Station

Minimal SIP server and WebRTC audio bridge with calls history for SIP-capable home door stations on a LAN. It is meant to be a simple direct setup for home deployments where running a full PBX such as Asterisk with complicated configuration would be unnecessary overhead.

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

It intentionally does not implement H264 video bridging, or a full RFC-complete SIP stack. Vendor-specific features should stay optional; HikVision and Dahua HTTP APIs are currently used for optional door control, snapshots, and maintenance actions.

## Run

```bash
pip install -e .
sip-indoor-station
```

Common environment variables:

```bash
export LISTEN_ADDRESS=0.0.0.0
export LOCAL_ADDRESS=
export SIP_PORT=5060
export SIP_REALM=sip.local
export SIP_USERNAME=door
export SIP_PASSWORD=change-me
export SIP_NONCE_TTL=300
export SIP_REGISTRATION_TTL=3600
export SIP_REGISTRATION_STORE_PATH=
export RTP_PORT_MIN=40000
export RTP_PORT_MAX=40100
export CALL_HISTORY_ENABLED=false
export CALL_HISTORY_DAYS=30
export CALL_HISTORY_DB_PATH=/data/call_history.sqlite
export API_ENABLED=false
export DOOR_STATION_VENDOR=
export API_HOST=
export API_PORT=80
export API_USERNAME=admin
export API_PASSWORD=change-me
export API_USE_HTTPS=false
export API_TIMEOUT_SECONDS=5
export API_VERIFY_SSL=false
export RELAYS_COUNT=1
sip-indoor-station
```

Set `SIP_REGISTRATION_STORE_PATH` to a JSON file path to persist unexpired SIP registrations across restarts. The Home Assistant add-on sets this to `/data/sip_registrations.json`.

Set `LOCAL_ADDRESS` when `LISTEN_ADDRESS` is `0.0.0.0` but SIP/SDP must advertise a reachable LAN address. In Docker or Home Assistant add-on setups this is usually the Home Assistant host address, for example `192.168.0.123`. This address is also used as the first configured WebRTC host ICE candidate.

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

### Optional Call History

Call history is disabled by default. Enable it to store recent call records in SQLite:

```bash
export CALL_HISTORY_ENABLED=true
export CALL_HISTORY_DAYS=30
export CALL_HISTORY_DB_PATH=/data/call_history.sqlite
```

The store creates a generated UUID for every incoming call. The SIP `Call-ID` is kept only as metadata, so API operations use the generated history UUID.

Stored statuses:

- `ringing`: incoming call is currently ringing.
- `answered`: call was answered. This status is kept after normal call end.
- `missed`: caller cancelled before answer.
- `rejected`: call was rejected locally.
- `failed`: call failed.
- `ended`: call ended without being marked answered.

Retention cleanup removes entries older than `CALL_HISTORY_DAYS`.

Snapshots are stored in the same SQLite database as BLOB data when a snapshot provider is available. For HikVision snapshots, set:

```bash
export DOOR_STATION_VENDOR=hikvision
export API_ENABLED=true
export API_HOST=192.168.0.234
export API_PORT=80
export API_USERNAME=admin
export API_PASSWORD=change-me
export API_USE_HTTPS=false
export API_TIMEOUT_SECONDS=5
export API_VERIFY_SSL=false
export RELAYS_COUNT=1
```

The HikVision snapshot provider reads:

```text
GET /ISAPI/Streaming/channels/101/picture
```

For Dahua snapshots, use:

```bash
export DOOR_STATION_VENDOR=dahua
export API_ENABLED=true
export API_HOST=192.168.0.235
export API_PORT=80
export API_USERNAME=admin
export API_PASSWORD=change-me
export API_USE_HTTPS=false
export API_TIMEOUT_SECONDS=5
export API_VERIFY_SSL=false
export RELAYS_COUNT=1
```

The Dahua snapshot provider reads:

```text
GET /cgi-bin/snapshot.cgi?channel=1
```

Call history endpoints:

- `GET /api/call_history`
- `DELETE /api/call_history`
- `GET /api/call_history/{history_id}`
- `DELETE /api/call_history/{history_id}`
- `GET /api/call_history/{history_id}/snapshot`

Example list response:

```json
{
  "calls": [
    {
      "id": "33f8f9e5-1ef3-48bb-89cc-2fb592f83b9a",
      "sip_call_id": "door-call-1",
      "status": "missed",
      "started_at": "2026-06-16T12:00:00Z",
      "answered_at": null,
      "ended_at": "2026-06-16T12:00:10Z",
      "remote_ip": "192.168.0.50",
      "has_snapshot": true,
      "snapshot_content_type": "image/jpeg",
      "snapshot_captured_at": "2026-06-16T12:00:01Z",
      "snapshot_url": "/api/call_history/33f8f9e5-1ef3-48bb-89cc-2fb592f83b9a/snapshot"
    }
  ]
}
```

State updates:

```text
GET /api/ws
```

The WebSocket sends the current state immediately after connection:

```json
{"type":"state","state":{"registered":false,"ringing":false,"in_call":false,"call_state":"idle"}}
```

It then sends `state` messages and `event` messages after internal SIP/API events. WebRTC media signaling remains separate on `/webrtc/ws`.

### Optional Vendor API

Vendor APIs are disabled by default. When enabled, `POST /api/open_door` calls the selected vendor door control endpoint.

Configure the door station HTTP API credentials:

```bash
export API_ENABLED=true
export DOOR_STATION_VENDOR=hikvision
export API_HOST=192.168.0.234
export API_PORT=80
export API_USERNAME=admin
export API_PASSWORD=change-me
export API_USE_HTTPS=false
export API_TIMEOUT_SECONDS=5
export API_VERIFY_SSL=false
```

Relay count defaults to one relay and can be changed with `RELAYS_COUNT`.

For HikVision, the door request is:

```text
PUT /ISAPI/AccessControl/RemoteControl/door/<relay>
Content-Type: application/xml
```

with:

```xml
<?xml version="1.0" encoding="UTF-8"?><RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>
```

For Dahua, the door request is sent to:

```text
GET /cgi-bin/accessControl.cgi?action=openDoor&channel=<relay>
```

SIP remains responsible for call handling. Vendor API is currently used for door control plus optional status/fallback helpers.

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
export LOCAL_ADDRESS=
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

For port-forwarded or NAT-crossing access, prefer a TURN server and `WEBRTC_ICE_TRANSPORT_POLICY=relay`. For direct host candidates without TURN, set `WEBRTC_ICE_UDP_PORT` and forward that one UDP port. Do not forward other ports like HTTP/WebSocket or SIP.

Open the browser demo during an active answered SIP call:

```text
http://<sip-indoor-station-host>:8080/
```

Click `Answer` followed by `Connect WebRTC`, allow microphone access, and the browser will exchange audio with the door station through GStreamer RTP-WebRTC bridge.
