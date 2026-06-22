let ws = null;
let apiWs = null;
let pc = null;
let localStream = null;
let remoteStream = null;

const statusEl = document.getElementById("status");
const apiStatusEl = document.getElementById("apiStatus");
const registeredEl = document.getElementById("registered");
const ringingEl = document.getElementById("ringing");
const inCallEl = document.getElementById("inCall");
const callStateEl = document.getElementById("callState");
const callIdEl = document.getElementById("callId");
const remoteIpEl = document.getElementById("remoteIp");
const codecEl = document.getElementById("codec");
const lastEventEl = document.getElementById("lastEvent");
const logEl = document.getElementById("log");
const remoteAudio = document.getElementById("remoteAudio");
const scriptUrl = document.currentScript ? document.currentScript.src : window.location.href;
const baseUrl = new URL(".", scriptUrl);

function httpUrl(path) {
  return new URL(path.replace(/^\/+/, ""), baseUrl).toString();
}

function websocketUrl(path) {
  const url = new URL(httpUrl(path));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function log(message) {
  logEl.textContent += `${new Date().toISOString()} ${message}\n`;
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return String(value);
}

function updateApiState(state) {
  registeredEl.textContent = state.registered ? "yes" : "no";
  ringingEl.textContent = state.ringing ? "yes" : "no";
  inCallEl.textContent = state.in_call ? "yes" : "no";
  callStateEl.textContent = formatValue(state.call_state);
  callIdEl.textContent = formatValue(state.call_id);
  remoteIpEl.textContent = formatValue(state.remote_ip);
  codecEl.textContent = state.selected_audio_codec
    ? `${state.selected_audio_codec}/${state.selected_audio_payload_type}`
    : "-";
  lastEventEl.textContent = state.last_event
    ? `${state.last_event} ${state.last_event_at || ""}`
    : "-";
}

async function loadApiState() {
  const response = await fetch(httpUrl("api/state"), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`state failed: ${response.status}`);
  }
  const state = await response.json();
  updateApiState(state);
  log(`api state: ${JSON.stringify(state)}`);
}

function connectApi() {
  if (apiWs) return;
  apiStatusEl.textContent = "Connecting";
  apiWs = new WebSocket(websocketUrl("api/ws"));
  apiWs.onopen = () => {
    apiStatusEl.textContent = "Connected";
    log("api websocket open");
  };
  apiWs.onerror = () => log("api websocket error");
  apiWs.onclose = () => {
    apiWs = null;
    apiStatusEl.textContent = "Disconnected";
    log("api websocket closed");
  };
  apiWs.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      updateApiState(message.state);
      log(`api state: ${JSON.stringify(message.state)}`);
    } else if (message.type === "event") {
      log(`api event: ${JSON.stringify(message)}`);
    } else {
      log(`api message: ${JSON.stringify(message)}`);
    }
  };
}

async function postCommand(path) {
  const response = await fetch(httpUrl(path), { method: "POST" });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  log(`${path} -> ${response.status} ${JSON.stringify(payload)}`);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
}

function waitForIceGatheringComplete(peerConnection, timeoutMs = 1500) {
  if (peerConnection.iceGatheringState === "complete") {
    return Promise.resolve();
  }
  return new Promise(resolve => {
    const timeout = window.setTimeout(done, timeoutMs);
    function done() {
      window.clearTimeout(timeout);
      peerConnection.removeEventListener("icegatheringstatechange", onStateChange);
      resolve();
    }
    function onStateChange() {
      log(`iceGatheringState=${peerConnection.iceGatheringState}`);
      if (peerConnection.iceGatheringState === "complete") {
        done();
      }
    }
    peerConnection.addEventListener("icegatheringstatechange", onStateChange);
  });
}

async function connect() {
  if (ws || pc) return;
  statusEl.textContent = "Connecting";
  const webrtcConfig = await loadWebRtcConfig();
  ws = new WebSocket(websocketUrl("webrtc/ws"));
  pc = new RTCPeerConnection({
    iceServers: webrtcConfig.iceServers,
    iceTransportPolicy: webrtcConfig.iceTransportPolicy,
  });
  remoteStream = new MediaStream();
  remoteAudio.srcObject = remoteStream;
  remoteAudio.controls = true;
  remoteAudio.autoplay = true;
  let websocketReady = false;
  let mediaReady = false;

  async function maybeSendOffer() {
    if (!websocketReady || !mediaReady || !ws || ws.readyState !== WebSocket.OPEN || !pc || pc.localDescription) {
      return;
    }
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitForIceGatheringComplete(pc);
    ws.send(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp }));
    statusEl.textContent = "Offer sent";
    log("offer sent");
  }

  ws.onopen = () => {
    websocketReady = true;
    log("websocket open");
    maybeSendOffer().catch(error => log(`offer failed: ${error.message}`));
  };
  ws.onerror = () => log("websocket error");
  ws.onclose = disconnect;
  ws.onmessage = async event => {
    const message = JSON.parse(event.data);
    if (message.type === "answer") {
      await pc.setRemoteDescription({ type: "answer", sdp: message.sdp });
      statusEl.textContent = "Connected";
      log("answer applied");
    } else if (message.type === "ice") {
      log("remote ICE");
      await pc.addIceCandidate(message.candidate);
    } else if (message.type === "error") {
      log(`error: ${message.message}`);
    } else if (message.type === "state") {
      log(`state: ${JSON.stringify(message)}`);
    }
  };

  pc.onconnectionstatechange = () => {
    log(`connectionState=${pc.connectionState}`);
    statusEl.textContent = pc.connectionState;
  };
  pc.oniceconnectionstatechange = () => log(`iceConnectionState=${pc.iceConnectionState}`);
  pc.onsignalingstatechange = () => log(`signalingState=${pc.signalingState}`);
  localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  const [audioTrack] = localStream.getAudioTracks();
  pc.addTransceiver(audioTrack, { direction: "sendrecv", streams: [localStream] });
  log(`local audio track ready=${audioTrack.readyState}`);
  pc.ontrack = event => {
    log(`remote track kind=${event.track.kind} streams=${event.streams.length}`);
    if (event.streams[0]) {
      remoteAudio.srcObject = event.streams[0];
    } else if (!remoteStream.getTracks().includes(event.track)) {
      remoteStream.addTrack(event.track);
    }
    log(`remote audio tracks=${remoteAudio.srcObject.getAudioTracks().length}`);
    remoteAudio.play().catch(error => log(`audio playback: ${error.message}`));
  };
  pc.onicecandidate = event => {
    if (event.candidate && ws && ws.readyState === WebSocket.OPEN) {
      log(`local ICE ${event.candidate.type || ""} ${event.candidate.protocol || ""}`);
      ws.send(JSON.stringify({ type: "ice", candidate: event.candidate.toJSON() }));
    } else if (!event.candidate) {
      log("local ICE complete");
    }
  };
  mediaReady = true;
  await maybeSendOffer();
}

async function loadWebRtcConfig() {
  const response = await fetch(httpUrl("webrtc/config"), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`WebRTC config failed: ${response.status}`);
  }
  const config = await response.json();
  log(`ICE servers=${config.iceServers.length} policy=${config.iceTransportPolicy}`);
  return {
    iceServers: config.iceServers,
    iceTransportPolicy: config.iceTransportPolicy || "all",
  };
}

function disconnect() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "close" }));
  }
  if (ws) ws.close();
  ws = null;
  if (pc) pc.close();
  pc = null;
  if (localStream) {
    for (const track of localStream.getTracks()) track.stop();
  }
  localStream = null;
  if (remoteStream) {
    for (const track of remoteStream.getTracks()) track.stop();
  }
  remoteStream = null;
  remoteAudio.srcObject = null;
  statusEl.textContent = "Disconnected";
}

function playAudio() {
  remoteAudio.play().then(
    () => log("audio play requested"),
    error => log(`audio play failed: ${error.message}`),
  );
}

document.getElementById("connect").addEventListener("click", () => connect().catch(error => log(error.message)));
document.getElementById("playAudio").addEventListener("click", playAudio);
document.getElementById("disconnect").addEventListener("click", disconnect);
document.getElementById("answer").addEventListener("click", () => postCommand("api/answer").catch(error => log(error.message)));
document.getElementById("reject").addEventListener("click", () => postCommand("api/reject").catch(error => log(error.message)));
document.getElementById("hangup").addEventListener("click", () => postCommand("api/hangup").catch(error => log(error.message)));
document.getElementById("openDoor").addEventListener("click", () => postCommand("api/open_door").catch(error => log(error.message)));
document.getElementById("reboot").addEventListener("click", () => postCommand("api/reboot").catch(error => log(error.message)));

loadApiState().catch(error => log(error.message));
connectApi();
