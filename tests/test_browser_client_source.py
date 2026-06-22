from pathlib import Path


def test_browser_client_uses_single_bidirectional_audio_transceiver() -> None:
    source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    assert "addTransceiver(audioTrack, { direction: \"sendrecv\"" in source
    assert "addTransceiver(\"audio\", { direction: \"recvonly\" })" not in source
    assert "direction: \"sendonly\"" not in source
    assert "pc.addTrack" not in source


def test_browser_client_waits_for_ice_before_sending_offer() -> None:
    source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    assert "waitForIceGatheringComplete" in source
    assert "await waitForIceGatheringComplete(pc);" in source


def test_browser_client_sets_websocket_open_handler_before_media_await() -> None:
    source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    assert source.index("ws.onopen") < source.index("navigator.mediaDevices.getUserMedia")
    assert "maybeSendOffer" in source


def test_browser_client_loads_webrtc_ice_config_before_peer_connection() -> None:
    source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    assert 'fetch(httpUrl("webrtc/config")' in source
    assert "new RTCPeerConnection({" in source
    assert "iceServers: webrtcConfig.iceServers" in source
    assert source.index("await loadWebRtcConfig()") < source.index("new RTCPeerConnection({")


def test_browser_client_uses_relative_urls_for_ingress() -> None:
    client_source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    html_source = Path("src/sip_indoor_station/web/static/index.html").read_text()
    assert 'src="client.js"' in html_source
    assert 'src="/client.js"' not in html_source
    assert 'fetch(httpUrl("api/state")' in client_source
    assert 'new WebSocket(websocketUrl("api/ws"))' in client_source
    assert 'new WebSocket(websocketUrl("webrtc/ws"))' in client_source
    assert 'postCommand("api/open_door")' in client_source
    assert 'fetch("/api/state"' not in client_source
    assert 'fetch("/webrtc/config"' not in client_source
    assert "${location.host}/api/ws" not in client_source
    assert "${location.host}/webrtc/ws" not in client_source


def test_browser_debug_page_points_users_to_home_assistant_integration() -> None:
    html_source = Path("src/sip_indoor_station/web/static/index.html").read_text()
    assert "only for debugging and development" in html_source
    assert "SIP Indoor Station Integration" in html_source
    assert "https://github.com/arturzx/sip-indoor-station-integration" in html_source


def test_browser_client_uses_websocket_ice_candidates_only() -> None:
    source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    assert "addConfiguredIceCandidates" not in source
    assert "iceCandidates: config.iceCandidates || []" not in source
    assert 'message.type === "ice"' in source
    assert "await pc.addIceCandidate(message.candidate)" in source


def test_browser_client_keeps_remote_audio_stream_and_play_button() -> None:
    client_source = Path("src/sip_indoor_station/web/static/client.js").read_text()
    html_source = Path("src/sip_indoor_station/web/static/index.html").read_text()
    assert "let remoteStream = null;" in client_source
    assert "remoteAudio.srcObject = remoteStream;" in client_source
    assert "remoteStream.addTrack(event.track)" in client_source
    assert "playAudio" in client_source
    assert 'id="playAudio"' in html_source
