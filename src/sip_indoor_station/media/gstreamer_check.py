from __future__ import annotations

from dataclasses import dataclass


REQUIRED_GSTREAMER_ELEMENTS = [
    "webrtcbin",
    "nicesrc",
    "nicesink",
    "udpsrc",
    "udpsink",
    "rtpjitterbuffer",
    "rtppcmudepay",
    "rtppcmupay",
    "rtppcmadepay",
    "rtppcmapay",
    "mulawdec",
    "mulawenc",
    "alawdec",
    "alawenc",
    "audioconvert",
    "audioresample",
    "opusenc",
    "opusdec",
    "rtpopuspay",
    "rtpopusdepay",
    "queue",
    "capsfilter",
]


@dataclass(frozen=True)
class GStreamerCheckResult:
    available: bool
    missing: list[str]
    error: str | None = None

    def assert_available(self) -> None:
        if not self.available:
            details = self.error or "missing elements: " + ", ".join(self.missing)
            raise RuntimeError(f"GStreamer WebRTC audio bridge requirements are not available: {details}")


_CACHED_DEFAULT_RESULT: GStreamerCheckResult | None = None


def check_required_elements(required: list[str] | None = None) -> GStreamerCheckResult:
    global _CACHED_DEFAULT_RESULT
    if required is None:
        if _CACHED_DEFAULT_RESULT is not None and _CACHED_DEFAULT_RESULT.available:
            return _CACHED_DEFAULT_RESULT
        result = _check_required_elements(REQUIRED_GSTREAMER_ELEMENTS)
        if result.available:
            _CACHED_DEFAULT_RESULT = result
        return result
    return _check_required_elements(required)


def _check_required_elements(required: list[str]) -> GStreamerCheckResult:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except Exception as exc:
        return GStreamerCheckResult(False, list(required), f"PyGObject/GStreamer import failed: {exc}")

    Gst.init(None)
    missing = [name for name in required if Gst.ElementFactory.find(name) is None]
    if missing:
        return GStreamerCheckResult(False, missing)

    registry = Gst.Registry.get()
    if registry.find_plugin("nice") is None:
        return GStreamerCheckResult(
            False,
            ["webrtcbin", "nicesrc", "nicesink"],
            "GStreamer libnice plugin is not registered; install/enable libnice-gstreamer",
        )
    return GStreamerCheckResult(True, [])
