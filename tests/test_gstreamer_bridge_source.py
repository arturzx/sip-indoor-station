from pathlib import Path

import pytest

from sip_indoor_station.media.gstreamer_webrtc_bridge import GStreamerWebRtcBridge


def test_gstreamer_bridge_uses_gstsdp_sdpresult_namespace() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert "self._gst.SDPResult" not in source
    assert "self._sdp_api.SDPResult.OK" in source


def test_gstreamer_bridge_outgoing_opus_caps_include_clock_rate() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert "clock-rate=48000,encoding-name=OPUS,payload={payload_type}" in source
    assert "name=browser_audio_pay" in source
    assert "name=browser_audio_caps" in source
    assert "udpsrc name=station_rtp_src port=" in source


def test_gstreamer_bridge_builds_pcmu_pipeline_by_default() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="192.168.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
    )
    pipeline = bridge._pipeline_description()
    assert "encoding-name=PCMU,payload=0" in pipeline
    assert "! rtppcmudepay ! mulawdec !" in pipeline


def test_gstreamer_bridge_builds_pcma_pipeline_when_selected() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="192.168.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        selected_codec="PCMA",
        selected_payload_type=8,
    )
    pipeline = bridge._pipeline_description()
    assert "encoding-name=PCMA,payload=8" in pipeline
    assert "! rtppcmadepay ! alawdec !" in pipeline
    assert bridge.sip_audio_elements.encoder == "alawenc"
    assert bridge.sip_audio_elements.pay == "rtppcmapay"


def test_gstreamer_bridge_reports_actual_ice_mline_mid() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert '"sdpMid": str(mline_index)' in source


def test_gstreamer_bridge_finds_browser_offered_opus_payload_type() -> None:
    sdp = (
        "v=0\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 111 63 9 0\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
    )
    assert GStreamerWebRtcBridge.find_opus_payload_type(sdp) == 111


def test_gstreamer_bridge_prepare_sets_pipeline_ready_to_bind_udp() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert "set_state(self._gst.State.READY)" in source
    assert "failed to set GStreamer WebRTC bridge pipeline to READY" in source


def test_gstreamer_bridge_sends_station_rtp_from_advertised_port() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert 'get_by_name("station_rtp_src")' in source
    assert 'get_property("used-socket")' in source
    assert 'set_property("socket", used_socket)' in source
    assert 'set_property("bind-port", int(self.local_rtp_port))' in source
    assert 'set_property("bind-address", self.local_bind_ip)' in source
    assert "station_rtp_out_shared_socket" in source


def test_gstreamer_bridge_keeps_advertised_media_ip_separate_from_bind_ip() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="192.168.0.2",
        local_bind_ip="0.0.0.0",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
    )
    assert bridge.local_media_ip == "192.168.0.2"
    assert bridge.local_bind_ip == "0.0.0.0"


def test_gstreamer_bridge_builds_configured_host_ice_candidate() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="172.17.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        ice_candidates=["192.168.0.2"],
    )
    candidate = "candidate:1 1 UDP 2122260223 172.17.0.2 50000 typ host"
    assert bridge.ice_candidate_for_first_configured_host(candidate) == "candidate:1 1 UDP 2122260223 192.168.0.2 50000 typ host"


def test_gstreamer_bridge_does_not_change_relay_ice_candidate_for_configured_host() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="172.17.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        ice_candidates=["192.168.0.2"],
    )
    candidate = "candidate:1 1 UDP 2122260223 10.0.0.5 50000 typ relay"
    assert bridge.ice_candidate_for_first_configured_host(candidate) == candidate


def test_gstreamer_bridge_formats_turn_server_with_credentials() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="192.168.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        turn_servers=["turn:turn.example.com:3478"],
        turn_username="user",
        turn_password="pass",
    )
    assert bridge.gstreamer_turn_server() == "turn://user:pass@turn.example.com:3478"


def test_gstreamer_bridge_formats_multiple_turn_servers_with_credentials() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="192.168.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        turn_servers=["turn:turn1.example.com:3478", "turns:turn2.example.com:5349"],
        turn_username="user",
        turn_password="pass",
    )
    assert bridge.gstreamer_turn_servers() == [
        "turn://user:pass@turn1.example.com:3478",
        "turns://user:pass@turn2.example.com:5349",
    ]


def test_gstreamer_bridge_prepends_configured_host_ice_candidates() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="172.17.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        ice_candidates=["192.168.0.2", "10.0.0.20:18556"],
    )
    candidate = "candidate:1 1 UDP 2122260223 172.17.0.2 50000 typ host"
    assert bridge.local_ice_candidates(candidate) == [
        "candidate:1 1 UDP 2122260223 192.168.0.2 50000 typ host",
        "candidate:1 1 UDP 2122260223 10.0.0.20 18556 typ host",
        "candidate:1 1 UDP 2122260223 172.17.0.2 50000 typ host",
    ]


def test_gstreamer_bridge_parses_configured_ice_candidate_host_and_optional_port() -> None:
    assert GStreamerWebRtcBridge.parse_configured_ice_candidate("192.168.0.2") == ("192.168.0.2", None)
    assert GStreamerWebRtcBridge.parse_configured_ice_candidate("10.0.0.20:18556") == ("10.0.0.20", 18556)


def test_gstreamer_bridge_fixes_ice_udp_port_when_configured() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="172.17.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        ice_udp_port=8555,
    )

    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    pipeline = bridge._pipeline_description()

    assert "webrtcbin name=webrtc" in pipeline
    assert 'ice_agent.set_property("min-rtp-port", self.ice_udp_port)' in source
    assert 'ice_agent.set_property("max-rtp-port", self.ice_udp_port)' in source
    assert 'ice_agent.set_property("ice-tcp", False)' in source
    assert 'webrtc.get_property("ice-agent")' in source
    assert "make_with_properties" not in source


def test_gstreamer_bridge_parses_pipeline_and_configures_ice_agent_before_ready() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    build_index = source.index("self.pipeline, self.webrtc = self._build_pipeline()")
    ready_index = source.index("set_state(self._gst.State.READY)")

    assert build_index < ready_index
    assert "self._gst.parse_launch(self._pipeline_description())" in source
    assert 'pipeline.get_by_name("webrtc")' in source
    assert 'webrtc.get_property("ice-agent")' in source
    assert "ctypes.pythonapi.Py_IncRef(py_object)" in source
    assert "self._ice_agent" not in source
    assert source.index('webrtc.get_property("ice-agent")') < source.index("ctypes.pythonapi.Py_IncRef(py_object)")
    assert 'ice_agent.set_property("min-rtp-port", self.ice_udp_port)' in source
    assert 'ice_agent.set_property("max-rtp-port", self.ice_udp_port)' in source
    assert 'ice_agent.set_property("ice-tcp", False)' in source
    assert "ice-agent::min-rtp-port" not in source
    assert "ice-agent::max-rtp-port" not in source


def test_gstreamer_bridge_uses_simple_null_cleanup() -> None:
    source = Path("src/sip_indoor_station/media/gstreamer_webrtc_bridge.py").read_text()
    assert "self._disconnect_gstreamer_signals()" in source
    assert "self._bus.remove_signal_watch()" in source
    assert "self.pipeline.set_state(self._gst.State.NULL)" in source
    assert "self.pipeline = None" in source
    assert "self.webrtc = None" in source
    assert "self._close_webrtcbin()" not in source
    assert "self._wait_for_pipeline_null_state()" not in source
    assert "_RETIRED_GSTREAMER_GRAPHS" not in source
    assert "pipeline.remove" not in source
    assert source.index("self._disconnect_gstreamer_signals()") < source.index("set_state(self._gst.State.NULL)")
    assert source.index("set_state(self._gst.State.NULL)") < source.index("self.pipeline = None")


def test_gstreamer_bridge_rejects_invalid_fixed_ice_udp_port() -> None:
    bridge = GStreamerWebRtcBridge(
        call_id="call-1",
        local_media_ip="172.17.0.2",
        local_rtp_port=40000,
        remote_rtp_ip="192.168.8.163",
        remote_rtp_port=9654,
        ice_udp_port=0,
    )

    with pytest.raises(ValueError, match="invalid WebRTC ICE UDP port"):
        bridge._validate_ice_udp_port()
