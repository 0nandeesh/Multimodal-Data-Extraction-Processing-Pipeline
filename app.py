from flask import Flask, request, jsonify, render_template_string, Response
import re, json, requests
from io import BytesIO
import pandas as pd
from defusedxml import ElementTree

app = Flask(__name__)

# ======= Core Functions =======
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
INNERTUBE_API_URL = "https://www.youtube.com/youtubei/v1/player?key={api_key}"
INNERTUBE_CONTEXT = {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38"}}

def extract_video_id(url):
    patterns = [r'(?:v=|\/)([0-9A-Za-z_-]{11})', r'youtu\.be\/([0-9A-Za-z_-]{11})']
    for p in patterns:
        m = re.search(p, url)
        if m: return m.group(1)
    return url if re.match(r'^[0-9A-Za-z_-]{11}$', url) else None

def fetch_html(video_id):
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(WATCH_URL.format(video_id=video_id), headers=headers)
    resp.raise_for_status()
    return resp.text

def extract_api_key(html):
    patterns = [r'"INNERTUBE_API_KEY":\s*"([A-Za-z0-9_\-]+)"', r'"innertubeApiKey":\s*"([A-Za-z0-9_\-]+)"']
    for pattern in patterns:
        m = re.search(pattern, html)
        if m: return m.group(1)
    raise RuntimeError("Could not find API key")

def fetch_innertube_data(video_id, api_key):
    url = INNERTUBE_API_URL.format(api_key=api_key)
    payload = {"context": INNERTUBE_CONTEXT, "videoId": video_id}
    headers = {'Content-Type': 'application/json','User-Agent': 'Mozilla/5.0'}
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

def extract_caption_tracks(data):
    try:
        return data.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
    except: return []

def select_track(tracks, langs=["en"]):
    if not tracks: return None
    for code in langs:
        for t in tracks:
            if t.get("languageCode") == code and t.get("kind") != "asr": return t
    for code in langs:
        for t in tracks:
            if t.get("languageCode") == code and t.get("kind") == "asr": return t
    return tracks[0]

def fetch_transcript_xml(url):
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.text

def parse_transcript(xml_str):
    snippets = []
    root = ElementTree.fromstring(xml_str)
    for elem in root:
        if elem.tag == 'text':
            text = elem.text or ""
            start = float(elem.attrib.get("start", "0"))
            dur = float(elem.attrib.get("dur", "0"))
            snippets.append({"text": text.strip(), "start": start, "duration": dur})
    return snippets

def format_text(snippets):
    lines = []
    for s in snippets:
        if s["text"]:
            m, sec = divmod(int(s["start"]), 60)
            lines.append(f"[{m:02d}:{sec:02d}] {s['text']}")
    return "\n".join(lines)

def build_transcript(url):
    vid = extract_video_id(url.strip())
    if not vid: raise ValueError("Invalid YouTube URL or Video ID")

    html = fetch_html(vid)
    api_key = extract_api_key(html)
    data = fetch_innertube_data(vid, api_key)
    tracks = extract_caption_tracks(data)
    if not tracks: raise RuntimeError("No transcripts available for this video")

    track = select_track(tracks)
    if not track: raise RuntimeError("No suitable transcript found")

    t_url = track["baseUrl"].replace("&fmt=srv3", "")
    xml = fetch_transcript_xml(t_url)
    snippets = parse_transcript(xml)
    if not snippets: raise RuntimeError("Failed to parse transcript")

    formatted = format_text(snippets)
    return {
        "video_id": vid,
        "snippets": snippets,
        "formatted": formatted,
        "track_lang": track.get("languageCode", "").upper(),
        "is_asr": (track.get("kind") == "asr"),
        "segments": len(snippets)
    }

# ======= UI =======
PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>YouTube Transcript Extractor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body{background:#0a0a0b;color:#e8eaed;font-family:Inter,Arial,sans-serif;margin:0;padding:20px}
    h1{color:#4e8cff}
    input{padding:10px;width:60%;border-radius:8px;border:1px solid #333;background:#121316;color:#fff}
    button{padding:10px 16px;margin:6px;border:none;border-radius:8px;background:#4e8cff;color:#fff;cursor:pointer;font-weight:600}
    button:hover{background:#3a6fd6}
    .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px}
    .metric{background:#16181c;padding:12px;border-radius:10px;text-align:center}
    .metric .k{font-size:18px;font-weight:700}
    .metric .l{font-size:12px;color:#aab0b6}
    .transcript{margin-top:20px}
    details{background:#16181c;margin-bottom:8px;padding:10px;border-radius:8px}
    summary{cursor:pointer;font-weight:600;color:#4e8cff}
    .download{margin-top:20px}
    select{padding:8px;border-radius:6px;background:#121316;color:#fff;border:1px solid #333}
  </style>
</head>
<body>
  <h1>🎬 YouTube Transcript Extractor</h1>
  <input type="text" id="videoUrl" placeholder="Enter YouTube URL or ID">
  <button onclick="getTranscript()">Extract Transcript</button>

  <div class="metrics" id="metrics" style="display:none;">
    <div class="metric"><div class="k" id="mSegments">—</div><div class="l">Segments</div></div>
    <div class="metric"><div class="k" id="mLang">—</div><div class="l">Language</div></div>
    <div class="metric"><div class="k" id="mASR">—</div><div class="l">Auto-generated</div></div>
  </div>

  <h2>Transcript</h2>
  <div class="transcript" id="output"></div>

  <div class="download">
    <label>Download as:</label>
    <select id="fmt">
      <option value="txt">TXT</option>
      <option value="json">JSON</option>
      <option value="csv">CSV</option>
    </select>
    <button onclick="downloadTranscript()">📥 Download</button>
  </div>

<script>
async function getTranscript() {
    const url = document.getElementById("videoUrl").value;
    let res = await fetch("/api/extract", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({url:url})
    });
    let data = await res.json();
    if(data.error){ alert("Error: " + data.error); return; }

    // metrics
    document.getElementById("metrics").style.display = "grid";
    document.getElementById("mSegments").innerText = data.metrics.segments;
    document.getElementById("mLang").innerText = data.metrics.language;
    document.getElementById("mASR").innerText = data.metrics.auto_generated ? "Yes" : "No";

    // transcript
    let out = document.getElementById("output");
    out.innerHTML = "";
    data.snippets.forEach((s, i) => {
        let mm = Math.floor(s.start/60).toString().padStart(2,"0");
        let ss = Math.floor(s.start%60).toString().padStart(2,"0");
        out.innerHTML += `<details><summary>[${mm}:${ss}]</summary><div>${s.text}</div></details>`;
    });
}

function downloadTranscript() {
    const url = document.getElementById("videoUrl").value;
    const fmt = document.getElementById("fmt").value;
    if(!url){ alert("Enter a YouTube URL first"); return; }
    const qs = new URLSearchParams({url, format: fmt});
    window.location = "/api/download?" + qs.toString();
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/extract", methods=["POST"])
def api_extract():
    try:
        payload = request.get_json(force=True)
        url = payload.get("url", "").strip()
        result = build_transcript(url)
        return jsonify({
            "formatted": result["formatted"],
            "snippets": result["snippets"],
            "metrics": {
                "segments": result["segments"],
                "language": result["track_lang"],
                "auto_generated": result["is_asr"]
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/download", methods=["GET"])
def api_download():
    try:
        url = request.args.get("url", "").strip()
        fmt = (request.args.get("format") or "txt").lower()
        result = build_transcript(url)
        vid = result["video_id"]

        if fmt == "txt":
            return Response(result["formatted"].encode("utf-8"),
                mimetype="text/plain",
                headers={"Content-Disposition": f"attachment; filename=transcript_{vid}.txt"})
        elif fmt == "json":
            return Response(json.dumps(result["snippets"], indent=2).encode("utf-8"),
                mimetype="application/json",
                headers={"Content-Disposition": f"attachment; filename=transcript_{vid}.json"})
        elif fmt == "csv":
            df = pd.DataFrame(result["snippets"])
            buf = BytesIO()
            df.to_csv(buf, index=False)
            buf.seek(0)
            return Response(buf.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=transcript_{vid}.csv"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(debug=True)
