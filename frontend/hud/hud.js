/* JARVIS HUD client.
   Polls the local backend via HTTP (/api/state). No WebSocket.
   SECURITY: same-origin relative URL only — the page is served from
   http://127.0.0.1:8765, so the HUD can never talk to a remote server. */
(function () {
  "use strict";

  var POLL_MS = 500; // fast enough to see idle → listening → thinking → speaking

  var core = document.getElementById("core");
  var coreLabel = document.getElementById("core-label");
  var transcriptEl = document.getElementById("transcript");
  var upNextEl = document.getElementById("upnext");
  var logEl = document.getElementById("log");
  var connDot = document.getElementById("conn-dot");
  var connLabel = document.getElementById("conn-label");

  var STATES = {
    idle: "IDLE — SAY \"JARVIS, …\"",
    listening: "LISTENING — SPEAK NOW",
    thinking: "THINKING…",
    working: "WORKING…",
    speaking: "SPEAKING…",
    starting: "STARTING UP…",
    error: "VOICE OFFLINE — SEE LOG",
  };

  var currentState = null;
  function setState(s) {
    if (s === currentState) return;
    currentState = s;
    core.className = "core " + s;
    coreLabel.textContent = STATES[s] || String(s).toUpperCase();
  }

  function log(msg, cls, ts) {
    var line = document.createElement("div");
    if (cls) line.className = cls;
    var t = (ts ? new Date(ts * 1000) : new Date()).toLocaleTimeString("en-GB");
    line.textContent = "[" + t + "] " + msg;
    logEl.prepend(line);
    while (logEl.children.length > 40) logEl.lastChild.remove();
  }

  /* waveform — draws mic-level ripple inside the core */
  var canvas = document.getElementById("waveform");
  var ctx = canvas.getContext("2d");
  var micLevel = 0;
  function drawWave() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "rgba(84,216,255,0.8)";
    ctx.lineWidth = 2;
    ctx.shadowColor = "#54d8ff";
    ctx.shadowBlur = 8;
    ctx.beginPath();
    var t = Date.now() / 300;
    for (var x = 0; x < canvas.width; x += 4) {
      var amp = 8 + micLevel * 44;
      var y = canvas.height / 2 + Math.sin(x / 28 + t) * amp * Math.sin(x / 130 + t * 0.6);
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
    requestAnimationFrame(drawWave);
  }
  drawWave();

  /* clock */
  function tickClock() {
    document.getElementById("sys-time").textContent =
      new Date().toLocaleTimeString("en-GB");
  }
  tickClock();
  setInterval(tickClock, 1000);

  function setBar(id, v) {
    if (v == null || isNaN(v)) return;
    v = Math.max(0, Math.min(100, v));
    document.getElementById(id + "-bar").style.width = v + "%";
    document.getElementById(id + "-val").textContent = Math.round(v) + "%";
  }

  function setStats(d) {
    setBar("cpu", d.cpu);
    setBar("mem", d.mem);
    if (d.mic != null) {
      micLevel = Math.max(0, Math.min(100, d.mic)) / 100;
      setBar("mic", d.mic);
    }
  }

  var lastTimetable = "";
  function setTimetable(items, upNext) {
    var key = JSON.stringify(items || []);
    if (key !== lastTimetable) {
      lastTimetable = key;
      var box = document.getElementById("timetable");
      box.innerHTML = "";
      if (!items || !items.length) {
        box.innerHTML = '<div class="t-empty mono">No events scheduled.</div>';
      } else {
        items.forEach(function (it) {
          var div = document.createElement("div");
          div.className = "t-item";
          div.innerHTML = '<span class="t-time mono"></span><span class="t-name"></span>';
          div.querySelector(".t-time").textContent = it.time;
          div.querySelector(".t-name").textContent = it.name;
          box.appendChild(div);
        });
      }
    }
    upNextEl.textContent = upNext || "—";
  }

  function setTranscript(s) {
    var parts = [];
    if (s.transcript) parts.push("YOU: " + s.transcript);
    if (s.reply) parts.push("JARVIS: " + s.reply);
    transcriptEl.textContent = parts.join("  ·  ");
  }

  var lastLogId = 0;
  function setLog(entries) {
    (entries || []).forEach(function (e) {
      if (e.id > lastLogId) {
        lastLogId = e.id;
        log(e.msg, e.level, e.ts);
      }
    });
  }

  function setConn(kind) {
    if (kind === "live") {
      connDot.className = "dot live";
      connLabel.className = "mono live";
      connLabel.textContent = "LIVE";
    } else if (kind === "starting") {
      connDot.className = "dot idle";
      connLabel.className = "mono";
      connLabel.textContent = "STARTING";
    } else {
      connDot.className = "dot idle";
      connLabel.className = "mono";
      connLabel.textContent = "OFFLINE";
    }
  }

  // HTTP polling — the single connection method.
  var inFlight = false;
  function poll() {
    if (inFlight) return;
    inFlight = true;
    fetch("/api/state", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        setConn(s.voice_online ? "live" : "starting");
        if (s.state) setState(s.state);
        setTranscript(s);
        setStats(s);
        setTimetable(s.timetable, s.up_next);
        setLog(s.log);
        if (s.brain_model) document.getElementById("llm-engine").textContent = s.brain_model;
      })
      .catch(function () {
        setConn("offline");
      })
      .then(function () { inFlight = false; });
  }
  setInterval(poll, POLL_MS);
  poll();
  log("HUD loaded. Connecting to JARVIS core…", "info");
})();
