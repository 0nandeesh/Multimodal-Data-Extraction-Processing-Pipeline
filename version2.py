from flask import Flask, request, jsonify, render_template_string, Response, send_file
import re, json, requests
from io import BytesIO
import pandas as pd
from defusedxml import ElementTree
import yt_dlp
import os
import threading
import time
import uuid
import tempfile
import shutil

app = Flask(__name__)

# Store download progress and info
download_status = {}

# ======= YouTube Audio Download Functions =======
def progress_hook(d):
    """Progress hook for yt-dlp"""
    download_id = d.get('_download_id')
    if not download_id or download_id not in download_status:
        return
    
    status = download_status[download_id]
    
    if d['status'] == 'downloading':
        if 'total_bytes' in d and d['total_bytes']:
            progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
        elif '_percent_str' in d:
            try:
                progress = float(d['_percent_str'].replace('%', ''))
            except:
                progress = 0
        else:
            progress = 0
            
        speed = d.get('_speed_str', '')
        eta = d.get('_eta_str', '')
        
        status_text = f"Downloading... {progress:.1f}%"
        if speed:
            status_text += f" ({speed})"
        if eta:
            status_text += f" ETA: {eta}"
            
        status.update({
            'progress': min(progress, 99),
            'status': status_text
        })
        
    elif d['status'] == 'finished':
        status.update({
            'progress': 99,
            'status': 'Converting to WAV format...'
        })

def download_audio_background(url, download_id):
    """Background download function - now uses temporary directory"""
    temp_dir = None
    try:
        download_status[download_id]['status'] = 'Extracting video information...'
        
        # Create temporary directory for this download
        temp_dir = tempfile.mkdtemp(prefix=f'audioclip_{download_id}_')
        temp_filename = os.path.join(temp_dir, 'temp_audio')
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192',
            }],
            'outtmpl': temp_filename + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [progress_hook],
        }
        
        download_status[download_id]['status'] = 'Starting download...'
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            info['_download_id'] = download_id
            
            # Clean title for filename
            clean_title = re.sub(r'[^\w\s\-_\.]', '', info['title'])
            clean_title = re.sub(r'\s+', ' ', clean_title).strip()
            final_filename = f"{clean_title}.wav"
            
            download_status[download_id].update({
                'filename': final_filename,
                'status': 'Downloading audio...'
            })
            
            ydl.process_info(info)
        
        # Find the converted WAV file
        wav_file = None
        for file in os.listdir(temp_dir):
            if file.startswith('temp_audio') and file.endswith('.wav'):
                wav_file = os.path.join(temp_dir, file)
                break
        
        if wav_file and os.path.exists(wav_file):
            # Read the file into memory
            with open(wav_file, 'rb') as f:
                audio_data = f.read()
            
            download_status[download_id].update({
                'progress': 100,
                'status': 'Download completed!',
                'success': True,
                'audio_data': audio_data,  # Store in memory instead of file
                'filename': final_filename
            })
        else:
            raise Exception("WAV file not found after conversion")
            
    except Exception as e:
        download_status[download_id].update({
            'progress': 0,
            'status': 'Download failed',
            'success': False,
            'error': str(e)
        })
    finally:
        # Clean up temporary directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

# ======= YouTube Transcript Functions =======
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

# ======= Unified UI =======
AUDIOCLIP_STUDIO_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AudioClip Studio - YouTube Transcript & Audio Segmentation</title>
<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
<style>
:root{--bg:#0a0a0b;--panel:#121316;--card:#16181c;--text:#e8eaed;--muted:#aab0b6;--accent:#4e8cff;--accent-2:#6bd4a8;--border:#23262b;--danger:#e05d5d;--warn:#caa75a}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,"Apple Color Emoji","Segoe UI Emoji";background:linear-gradient(180deg,#0a0a0b,#0b0d12 60%);color:var(--text)}
.container{max-width:1200px;margin:0 auto;padding:28px 20px 80px}
.header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px}
.title{font-size:24px;font-weight:700;letter-spacing:.2px;background:linear-gradient(135deg,var(--accent),var(--accent-2));background-clip:text;-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:var(--muted);font-size:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px}
.card h3{margin:0 0 10px;font-size:16px}
.row{display:flex;gap:10px;align-items:center}
.hint{color:var(--muted);font-size:12px;margin-top:6px}
input[type="file"]{display:none}
input[type="text"]{padding:10px 14px;border-radius:10px;border:1px solid var(--border);background:var(--panel);color:var(--text);width:100%;font-size:14px}
input[type="text"]:focus{outline:none;border-color:var(--accent)}
.uploader{position:relative;border:1px dashed #2a2e36;border-radius:14px;padding:18px;text-align:center;background:#0f1115}
.uploader label{cursor:pointer;display:inline-flex;align-items:center;gap:8px;padding:10px 14px;border-radius:10px;border:1px solid var(--border);background:#111318}
.uploader .name{margin-top:10px;font-size:13px;color:var(--muted)}
.badge{display:inline-block;font-size:12px;padding:4px 8px;border-radius:999px;border:1px solid var(--border);background:#101217;color:var(--muted)}
.btn{appearance:none;border:none;background:var(--accent);color:#fff;padding:10px 14px;border-radius:10px;font-weight:600;cursor:pointer;font-size:14px;transition:all 0.2s}
.btn[disabled]{opacity:.5;cursor:not-allowed}
.btn.secondary{background:#1b1f2a;color:var(--text);border:1px solid var(--border)}
.btn.ghost{background:transparent;color:var(--text);border:1px solid var(--border)}
.btn.danger{background:var(--danger)}
.btn.wav{background:var(--accent-2);color:#fff}
.btn:hover:not([disabled]){transform:translateY(-1px);box-shadow:0 4px 12px rgba(78,140,255,0.3)}
.btn.wav:hover:not([disabled]){box-shadow:0 4px 12px rgba(107,212,168,0.3)}
.status{font-size:13px;color:var(--muted)}
.log{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;font-size:12px;color:#b9c0c7;padding:12px;background:#0e1014;border:1px solid var(--border);border-radius:12px;max-height:190px;overflow:auto}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:8px}
.metric{background:#0e1117;border:1px solid var(--border);border-radius:12px;padding:10px;text-align:center}
.metric .k{font-size:18px;font-weight:700}
.metric .l{font-size:12px;color:var(--muted)}
.results{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.seg{border:1px solid var(--border);border-radius:14px;background:#0e1116;padding:12px}
.seg h4{margin:0 0 6px;font-size:14px}
.seg .meta{font-size:12px;color:var(--muted);margin-bottom:6px}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:8px}
.divider{height:1px;background:var(--border);margin:12px 0}
.expander summary{cursor:pointer;color:var(--muted);font-weight:600}
.progress{height:8px;border-radius:999px;background:#12141b;overflow:hidden;border:1px solid var(--border)}
.progress>div{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .25s ease}
.workflow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px}
.step{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center;position:relative}
.step.active{border-color:var(--accent);background:rgba(78,140,255,.1)}
.step.complete{border-color:var(--accent-2);background:rgba(107,212,168,.1)}
.step-num{width:24px;height:24px;border-radius:50%;background:var(--border);color:var(--muted);display:flex;align-items:center;justify-content:center;margin:0 auto 8px;font-size:12px;font-weight:700}
.step.active .step-num{background:var(--accent);color:#fff}
.step.complete .step-num{background:var(--accent-2);color:#fff}
.step-title{font-size:14px;font-weight:600;margin-bottom:4px}
.step-desc{font-size:12px;color:var(--muted)}
.tabs{display:flex;border-bottom:1px solid var(--border);margin-bottom:16px}
.tab{padding:12px 16px;cursor:pointer;border-bottom:2px solid transparent;color:var(--muted);font-weight:600}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.audio-download-status{margin-top:10px;padding:12px;background:#0e1014;border:1px solid var(--border);border-radius:12px;display:none}
.progress-bar{width:100%;height:8px;background:#1a1d23;border-radius:4px;overflow:hidden;margin:8px 0}
.progress-fill{height:100%;background:linear-gradient(45deg,var(--accent-2),var(--accent));width:0%;transition:width 0.5s ease;border-radius:4px}
@media (max-width:960px){.grid{grid-template-columns:1fr}.results{grid-template-columns:1fr}.workflow{grid-template-columns:1fr}.row{flex-wrap:wrap}}
</style>
</head>
<body>
<div class="container">
<div class="header">
<div>
<div class="title">AudioClip Studio</div>
<div class="sub">Extract YouTube transcripts and automatically segment audio files. Complete workflow in one tool.</div>
</div>
<div class="row">
<span class="badge">YouTube extraction</span>
<span class="badge">Audio segmentation</span>
<span class="badge">Client-side processing</span>
</div>
</div>

<!-- Workflow Steps -->
<div class="workflow">
<div class="step" id="step1">
<div class="step-num">1</div>
<div class="step-title">Extract Transcript</div>
<div class="step-desc">Get transcript from YouTube video</div>
</div>
<div class="step" id="step2">
<div class="step-num">2</div>
<div class="step-title">Upload Audio</div>
<div class="step-desc">Upload corresponding audio file</div>
</div>
<div class="step" id="step3">
<div class="step-num">3</div>
<div class="step-title">Segment Audio</div>
<div class="step-desc">Automatically create audio segments</div>
</div>
</div>

<!-- Tab Navigation -->
<div class="tabs">
<div class="tab active" onclick="switchTab('extract')">YouTube Transcript</div>
<div class="tab" onclick="switchTab('segment')">Audio Segmentation</div>
</div>

<!-- YouTube Transcript Tab -->
<div class="tab-content active" id="extractTab">
<div class="grid">
<div class="card">
<h3>YouTube URL</h3>
<input type="text" id="videoUrl" placeholder="https://youtube.com/watch?v=... or video ID">
<div class="row" style="margin-top:10px">
<button class="btn" onclick="extractTranscript()">Extract Transcript</button>
<button class="btn wav" onclick="downloadAudioWAV()">Download WAV</button>
</div>
<div class="row" style="margin-top:8px">
<button class="btn secondary" onclick="useTranscriptForSegmentation()">Use Transcript for Segmentation</button>
<button class="btn secondary" onclick="useAudioForSegmentation()" id="useAudioBtn" disabled>Use Audio for Segmentation</button>
</div>
<div class="hint">Audio downloads directly to your Downloads folder. Click "Download WAV" as many times as needed.</div>

<!-- Audio Download Progress -->
<div class="audio-download-status" id="audioDownloadStatus">
<div class="status" id="audioDownloadText">Preparing download...</div>
<div class="progress-bar">
<div class="progress-fill" id="audioProgressFill"></div>
</div>
<div class="status" id="audioDownloadPercent">0%</div>
</div>
</div>
<div class="card">
<h3>Transcript Status</h3>
<div class="status" id="transcriptStatus">Ready to extract transcript</div>
<div class="metrics" id="transcriptMetrics" style="display:none;">
<div class="metric"><div class="k" id="tSegments">—</div><div class="l">Segments</div></div>
<div class="metric"><div class="k" id="tLang">—</div><div class="l">Language</div></div>
<div class="metric"><div class="k" id="tASR">—</div><div class="l">Auto-generated</div></div>
</div>
</div>
</div>
<div class="card" style="margin-top:16px">
<div class="topbar">
<h3 style="margin:0">Extracted Transcript</h3>
<div class="row">
<select id="downloadFormat">
<option value="txt">TXT</option>
<option value="json">JSON</option>
<option value="csv">CSV</option>
</select>
<button class="btn ghost" onclick="downloadTranscript()">Download</button>
</div>
</div>
<div class="log" id="transcriptOutput" style="max-height:300px"></div>
</div>
</div>

<!-- Audio Segmentation Tab -->
<div class="tab-content" id="segmentTab">
<div class="grid">
<div class="card">
<h3>Audio file</h3>
<div class="uploader">
<input id="audioInput" type="file" accept="audio/*,video/mp4"/>
<label for="audioInput">Choose audio file</label>
<div class="name" id="audioName">Supported: MP3, WAV, M4A, FLAC, OGG, MP4</div>
</div>
</div>
<div class="card">
<h3>Transcript source</h3>
<div class="row" style="margin-bottom:10px">
<button class="btn secondary" id="useYouTubeBtn" disabled onclick="useYouTubeTranscript()">Use YouTube Transcript</button>
<span style="color:var(--muted)">or</span>
</div>
<div class="uploader">
<input id="transcriptFile" type="file" accept=".txt,text/plain"/>
<label for="transcriptFile">Upload transcript file</label>
<div class="name" id="transcriptFileName">Plain text with timestamps</div>
</div>
</div>
</div>
<div class="card" style="margin-top:16px">
<div class="row" style="justify-content:space-between;align-items:center">
<div>
<h3 style="margin-bottom:4px">Audio Processing</h3>
<div class="status" id="analysisStatus">Waiting for audio and transcript.</div>
</div>
<div>
<button class="btn" id="processBtn" disabled>Process Audio</button>
<button class="btn secondary" id="resetBtn">Reset</button>
</div>
</div>
<div class="divider"></div>
<details class="expander" id="previewExpander">
<summary>Preview segments</summary>
<div id="preview"></div>
</details>
<div class="divider"></div>
<div class="progress"><div id="progressBar"></div></div>
<div style="margin-top:10px" class="log" id="segmentLog"></div>
</div>
<div class="card" style="margin-top:16px">
<div class="topbar">
<div>
<h3 style="margin:0">Audio Segments</h3>
<div class="status" id="resultsStatus">No segments yet.</div>
</div>
<div class="row">
<button class="btn" id="downloadZipBtn" disabled>Download all (ZIP)</button>
</div>
</div>
<div class="metrics">
<div class="metric"><div class="k" id="mTotal">0</div><div class="l">Total segments</div></div>
<div class="metric"><div class="k" id="mDur">0.0 s</div><div class="l">Total duration</div></div>
<div class="metric"><div class="k" id="mAvg">0.0 s</div><div class="l">Average duration</div></div>
</div>
<div class="divider"></div>
<div class="results" id="results"></div>
</div>
</div>

<script>
// Global variables
const $ = s => document.querySelector(s);
let currentTranscript = null;
let currentAudioDownload = null;
let audioFile = null;
let audioBuffer = null;
let sampleRate = 0;
let parsedSegments = [];
let extractedSegments = [];
let audioCtx = null;
let audioPollInterval = null;

// Tab switching
function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    
    if (tab === 'extract') {
        document.querySelector('.tab:nth-child(1)').classList.add('active');
        $('#extractTab').classList.add('active');
    } else {
        document.querySelector('.tab:nth-child(2)').classList.add('active');
        $('#segmentTab').classList.add('active');
    }
}

// Step management
function setStep(step, state) {
    const stepEl = $(`#step${step}`);
    stepEl.classList.remove('active', 'complete');
    if (state === 'active') stepEl.classList.add('active');
    else if (state === 'complete') stepEl.classList.add('complete');
}

// YouTube transcript extraction
async function extractTranscript() {
    const url = $('#videoUrl').value.trim();
    if (!url) return;
    
    setStep(1, 'active');
    $('#transcriptStatus').textContent = 'Extracting transcript...';
    
    try {
        const response = await fetch('/api/extract', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: url})
        });
        const data = await response.json();
        
        if (data.error) {
            $('#transcriptStatus').textContent = `Error: ${data.error}`;
            setStep(1, '');
            return;
        }
        
        currentTranscript = data;
        $('#transcriptOutput').textContent = data.formatted;
        $('#transcriptMetrics').style.display = 'grid';
        $('#tSegments').textContent = data.metrics.segments;
        $('#tLang').textContent = data.metrics.language;
        $('#tASR').textContent = data.metrics.auto_generated ? 'Yes' : 'No';
        $('#transcriptStatus').textContent = `Extracted ${data.metrics.segments} segments`;
        
        $('#useYouTubeBtn').disabled = false;
        setStep(1, 'complete');
        
    } catch (error) {
        $('#transcriptStatus').textContent = `Error: ${error.message}`;
        setStep(1, '');
    }
}

// YouTube audio download - now downloads directly to user's Downloads folder
async function downloadAudioWAV() {
    const url = $('#videoUrl').value.trim();
    if (!url) return;
    
    const audioDownloadStatus = $('#audioDownloadStatus');
    const audioDownloadText = $('#audioDownloadText');
    const audioProgressFill = $('#audioProgressFill');
    const audioDownloadPercent = $('#audioDownloadPercent');
    
    audioDownloadStatus.style.display = 'block';
    audioDownloadText.textContent = 'Starting download...';
    audioProgressFill.style.width = '0%';
    audioDownloadPercent.textContent = '0%';
    
    try {
        const response = await fetch('/api/audio/download', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: url})
        });
        
        if (!response.ok) {
            const error = await response.text();
            throw new Error(error);
        }
        
        const result = await response.json();
        const downloadId = result.download_id;
        
        audioPollInterval = setInterval(async () => {
            try {
                const progressResponse = await fetch(`/api/audio/progress/${downloadId}`);
                
                if (!progressResponse.ok) {
                    throw new Error('Failed to get progress');
                }
                
                const progressData = await progressResponse.json();
                
                const progress = Math.round(progressData.progress || 0);
                audioProgressFill.style.width = progress + '%';
                audioDownloadPercent.textContent = progress + '%';
                audioDownloadText.textContent = progressData.status || 'Processing...';
                
                if (progressData.success) {
                    clearInterval(audioPollInterval);
                    
                    currentAudioDownload = {
                        downloadId: downloadId,
                        filename: progressData.filename,
                        url: url
                    };
                    
                    audioDownloadText.textContent = 'Starting download to your Downloads folder...';
                    
                    // Create download link that goes directly to Downloads folder
                    const downloadUrl = `/api/audio/file/${downloadId}`;
                    const a = document.createElement('a');
                    a.href = downloadUrl;
                    a.download = progressData.filename || 'audio.wav';
                    a.style.display = 'none';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    
                    audioDownloadText.textContent = 'Download completed! File saved to Downloads folder.';
                    $('#useAudioBtn').disabled = false;
                    
                    setTimeout(() => {
                        audioDownloadStatus.style.display = 'none';
                    }, 3000);
                    
                } else if (progressData.error) {
                    clearInterval(audioPollInterval);
                    throw new Error(progressData.error);
                }
                
            } catch (error) {
                clearInterval(audioPollInterval);
                throw error;
            }
        }, 1000);
        
    } catch (error) {
        if (audioPollInterval) {
            clearInterval(audioPollInterval);
        }
        audioDownloadText.textContent = 'Error: ' + error.message;
        setTimeout(() => {
            audioDownloadStatus.style.display = 'none';
        }, 5000);
    }
}

// Use downloaded audio for segmentation - now fetches from server memory
async function useAudioForSegmentation() {
    if (!currentAudioDownload) return;
    
    switchTab('segment');
    
    try {
        const fileResponse = await fetch(`/api/audio/file/${currentAudioDownload.downloadId}`);
        
        if (!fileResponse.ok) {
            throw new Error('Failed to fetch audio file');
        }
        
        const audioBlob = await fileResponse.blob();
        const file = new File([audioBlob], currentAudioDownload.filename, { type: 'audio/wav' });
        
        setStep(2, 'active');
        $('#audioName').textContent = currentAudioDownload.filename + ` (${(audioBlob.size / 1e6).toFixed(2)} MB) - Downloaded from YouTube`;
        log(`Loading downloaded audio: ${currentAudioDownload.filename}`);
        
        const {samples, sr, duration} = await loadAudio(file);
        log(`Loaded. Duration: ${duration.toFixed(1)} s, Sample rate: ${sr} Hz`);
        $('#analysisStatus').textContent = parsedSegments.length ? 
            'Audio ready. Ready to process.' : 'Audio ready. Upload transcript or use YouTube transcript.';
        setStep(2, 'complete');
        
        if (parsedSegments.length > 0) {
            $('#processBtn').disabled = false;
        }
        
        audioFile = file;
        
    } catch (err) {
        log(`Audio load failed: ${err.message}`);
        $('#analysisStatus').textContent = 'Failed to load downloaded audio.';
        setStep(2, '');
    }
}

function useTranscriptForSegmentation() {
    if (!currentTranscript) return;
    
    switchTab('segment');
    
    const segments = currentTranscript.snippets.map(s => ({
        start: s.start,
        end: s.start + s.duration,
        text: s.text
    }));
    
    parsedSegments = segments;
    $('#transcriptFileName').textContent = `YouTube transcript (${segments.length} segments)`;
    $('#analysisStatus').textContent = `YouTube transcript loaded: ${segments.length} segments`;
    
    $('#previewExpander').open = true;
    $('#preview').innerHTML = '';
    segments.slice(0, 5).forEach((s, i) => {
        const p = document.createElement('div');
        p.className = 'status';
        p.textContent = `Segment ${i + 1}: ${s.start.toFixed(1)}s - ${s.end.toFixed(1)}s | ${s.text}`;
        $('#preview').appendChild(p);
    });
    
    if (audioBuffer) {
        $('#processBtn').disabled = false;
    }
}

function downloadTranscript() {
    if (!currentTranscript) return;
    const url = $('#videoUrl').value.trim();
    const fmt = $('#downloadFormat').value;
    const params = new URLSearchParams({url, format: fmt});
    window.location = '/api/download?' + params.toString();
}

// Audio processing functions
function log(msg) {
    const logEl = $('#segmentLog');
    logEl.textContent += (logEl.textContent ? "\\n" : "") + msg;
    logEl.scrollTop = logEl.scrollHeight;
}

function safeFilename(text) {
    let s = text.normalize('NFKD').replace(/[<>:"/\\\\|?*\\x00-\\x1f]/g, '');
    s = s.replace(/[^\\w\\s\\-_.]/g, '_').replace(/[\\-\\s_]+/g, '_').replace(/^[_\\.]+|[_\\.]+$/g, '');
    if (s.length < 2) s = 'audio_segment';
    return s.slice(0, 25);
}

function parseTranscriptFile(text) {
    const lines = text.split(/\\r?\\n/).map(l => l.trim());
    const timestamped = [];
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line) continue;
        let m;
        
        if ((m = line.match(/^\\[(\\d{2}:\\d{2}:\\d{2})\\s*-\\s*(\\d{2}:\\d{2}:\\d{2})\\]\\s*(.+)$/))) {
            const start = hhmmssToSeconds(m[1]);
            const end = hhmmssToSeconds(m[2]);
            timestamped.push({start, end, text: m[3].trim()});
            continue;
        }
        if ((m = line.match(/^(\\d{2}:\\d{2}:\\d{2},\\d{3})\\s*-->\\s*(\\d{2}:\\d{2}:\\d{2},\\d{3})$/))) {
            const next = (i + 1 < lines.length) ? lines[i + 1].trim() : '';
            if (next) {
                timestamped.push({start: srtToSeconds(m[1]), end: srtToSeconds(m[2]), text: next});
            }
            continue;
        }
        if ((m = line.match(/^(\\d{1,2}:\\d{2})\\s*-\\s*(\\d{1,2}:\\d{2})\\s*(.+)$/))) {
            timestamped.push({start: simpleToSeconds(m[1]), end: simpleToSeconds(m[2]), text: m[3].trim()});
            continue;
        }
        if ((m = line.match(/^(\\d+\\.?\\d*)\\s*-\\s*(\\d+\\.?\\d*)\\s*(.+)$/))) {
            timestamped.push({start: Number(m[1]), end: Number(m[2]), text: m[3].trim()});
            continue;
        }
        if ((m = line.match(/^\\[(\\d{1,2}:\\d{2})\\]\\s*(.+)$/))) {
            timestamped.push({start: simpleToSeconds(m[1]), text: m[2].trim()});
            continue;
        }
        if ((m = line.match(/^\\((\\d{1,2}:\\d{2})\\)\\s*(.+)$/))) {
            timestamped.push({start: simpleToSeconds(m[1]), text: m[2].trim()});
            continue;
        }
        if ((m = line.match(/^(\\d{1,2}:\\d{2})\\s+(.+)$/))) {
            timestamped.push({start: simpleToSeconds(m[1]), text: m[2].trim()});
            continue;
        }
        if ((m = line.match(/^(\\d+\\.?\\d*)\\s+(.+)$/))) {
            timestamped.push({start: Number(m[1]), text: m[2].trim()});
            continue;
        }
    }
    
    let segments = [];
    if (timestamped.length && timestamped[0].end === undefined) {
        for (let i = 0; i < timestamped.length; i++) {
            const cur = {...timestamped[i]};
            cur.end = (i + 1 < timestamped.length) ? timestamped[i + 1].start : cur.start + 3.0;
            segments.push(cur);
        }
    } else {
        segments = timestamped;
    }
    
    return segments;
}

function hhmmssToSeconds(t) {
    const [hh, mm, ss] = t.split(':').map(Number);
    return hh * 3600 + mm * 60 + ss;
}

function srtToSeconds(t) {
    const [time, ms] = t.split(',');
    return hhmmssToSeconds(time) + Number(ms) / 1000;
}

function simpleToSeconds(t) {
    const [m, s] = t.split(':').map(Number);
    return m * 60 + s;
}

async function ensureAudioContext() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)({latencyHint: 'interactive'});
    }
    return audioCtx;
}

async function loadAudio(file) {
    const ctx = await ensureAudioContext();
    const arrayBuf = await file.arrayBuffer();
    const decoded = await ctx.decodeAudioData(arrayBuf);
    sampleRate = decoded.sampleRate;
    const chs = decoded.numberOfChannels;
    let mono = new Float32Array(decoded.length);
    for (let c = 0; c < chs; c++) {
        const data = decoded.getChannelData(c);
        for (let i = 0; i < decoded.length; i++) mono[i] += data[i] / chs;
    }
    audioBuffer = mono;
    return {samples: mono, sr: sampleRate, duration: mono.length / sampleRate};
}

function floatTo16BitPCM(float32Array) {
    const out = new DataView(new ArrayBuffer(float32Array.length * 2));
    let offset = 0;
    for (let i = 0; i < float32Array.length; i++) {
        let s = Math.max(-1, Math.min(1, float32Array[i]));
        out.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        offset += 2;
    }
    return out;
}

function encodeWAV(samples, sampleRate) {
    const bytesPerSample = 2;
    const numChannels = 1;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataLen = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataLen);
    const view = new DataView(buffer);
    let offset = 0;
    
    function writeString(s) {
        for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
        offset += s.length;
    }
    function writeUint32(v) { view.setUint32(offset, v, true); offset += 4; }
    function writeUint16(v) { view.setUint16(offset, v, true); offset += 2; }
    
    writeString('RIFF'); writeUint32(36 + dataLen); writeString('WAVE');
    writeString('fmt '); writeUint32(16); writeUint16(1); writeUint16(numChannels); 
    writeUint32(sampleRate); writeUint32(byteRate); writeUint16(blockAlign); writeUint16(16);
    writeString('data'); writeUint32(dataLen);
    
    const pcm = floatTo16BitPCM(samples);
    for (let i = 0; i < dataLen; i += 2) {
        view.setInt16(44 + i, pcm.getInt16(i, true), true);
    }
    return new Blob([view], {type: 'audio/wav'});
}

function extractSegments(y, sr, segments) {
    const out = [];
    for (let i = 0; i < segments.length; i++) {
        const seg = segments[i];
        const start = Math.max(0, Math.floor(seg.start * sr));
        const end = Math.min(y.length, Math.floor(seg.end * sr));
        if (end <= start) {
            log(`Skipped invalid timing for segment ${i + 1}`);
            continue;
        }
        const cut = y.slice(start, end);
        const blob = encodeWAV(cut, sr);
        const filename = `segment_${String(i + 1).padStart(3, '0')}_${safeFilename(seg.text.slice(0, 30))}.wav`;
        out.push({
            filename, blob, text: seg.text,
            start_time: seg.start, end_time: seg.end,
            duration: seg.end - seg.start
        });
    }
    return out;
}

async function makeZip(files) {
    const zip = new JSZip();
    let meta = 'Audio Segmentation Results\\n' + '='.repeat(50) + '\\n\\n';
    files.forEach((f, idx) => {
        zip.file(f.filename, f.blob);
        meta += `Segment ${idx + 1}: ${f.filename}\\n  Text: ${f.text}\\n  Start: ${f.start_time.toFixed(2)}s\\n  End: ${f.end_time.toFixed(2)}s\\n  Duration: ${f.duration.toFixed(2)}s\\n\\n`;
    });
    zip.file('metadata.txt', meta);
    return await zip.generateAsync({type: 'blob'});
}

function setProgress(r) {
    $('#progressBar').style.width = `${Math.round(r * 100)}%`;
}

function clearSegmentationUI() {
    parsedSegments = [];
    extractedSegments = [];
    audioBuffer = null;
    sampleRate = 0;
    setProgress(0);
    $('#preview').innerHTML = '';
    $('#results').innerHTML = '';
    $('#segmentLog').textContent = '';
    $('#mTotal').textContent = '0';
    $('#mDur').textContent = '0.0 s';
    $('#mAvg').textContent = '0.0 s';
    $('#resultsStatus').textContent = 'No segments yet.';
    $('#analysisStatus').textContent = 'Waiting for audio and transcript.';
    $('#processBtn').disabled = true;
    $('#downloadZipBtn').disabled = true;
}

function useYouTubeTranscript() {
    useTranscriptForSegmentation();
}

// Event listeners
$('#audioInput').addEventListener('change', async (e) => {
    audioFile = e.target.files[0] || null;
    if (!audioFile) {
        $('#audioName').textContent = '';
        return;
    }
    
    setStep(2, 'active');
    $('#audioName').textContent = audioFile.name + ` (${(audioFile.size / 1e6).toFixed(2)} MB)`;
    log(`Loading audio: ${audioFile.name}`);
    
    try {
        const {samples, sr, duration} = await loadAudio(audioFile);
        log(`Loaded. Duration: ${duration.toFixed(1)} s, Sample rate: ${sr} Hz`);
        $('#analysisStatus').textContent = parsedSegments.length ? 
            'Audio ready. Ready to process.' : 'Audio ready. Upload transcript or use YouTube transcript.';
        setStep(2, 'complete');
        
        if (parsedSegments.length > 0) {
            $('#processBtn').disabled = false;
        }
    } catch (err) {
        log(`Audio load failed: ${err.message}`);
        $('#analysisStatus').textContent = 'Failed to load audio. Try WAV for best compatibility.';
        setStep(2, '');
    }
});

$('#transcriptFile').addEventListener('change', async (e) => {
    const transcriptFile = e.target.files[0] || null;
    if (!transcriptFile) {
        $('#transcriptFileName').textContent = '';
        return;
    }
    
    $('#transcriptFileName').textContent = transcriptFile.name + ` (${(transcriptFile.size / 1e3).toFixed(1)} KB)`;
    log(`Reading transcript: ${transcriptFile.name}`);
    
    const txt = await transcriptFile.text();
    const segs = parseTranscriptFile(txt);
    
    if (segs.length) {
        parsedSegments = segs;
        $('#analysisStatus').textContent = `Detected ${segs.length} timestamped segments.`;
        $('#previewExpander').open = true;
        $('#preview').innerHTML = '';
        segs.slice(0, 5).forEach((s, i) => {
            const p = document.createElement('div');
            p.className = 'status';
            p.textContent = `Segment ${i + 1}: ${s.start.toFixed(1)}s - ${s.end.toFixed(1)}s | ${s.text}`;
            $('#preview').appendChild(p);
        });
    } else {
        const lines = txt.split(/\\r?\\n/).map(l => l.trim()).filter(Boolean);
        parsedSegments = {_auto: true, lines};
        $('#analysisStatus').textContent = 'No timestamps found. Will use automatic segmentation.';
        $('#preview').innerHTML = '';
    }
    
    if (audioBuffer) {
        $('#processBtn').disabled = false;
    }
});

$('#processBtn').addEventListener('click', async () => {
    if (!audioBuffer || !sampleRate || !parsedSegments.length) return;
    
    setStep(3, 'active');
    setProgress(0.02);
    log('Starting segmentation');
    
    try {
        const segments = parsedSegments;
        if (!segments.length) {
            $('#resultsStatus').textContent = 'No segments to process.';
            setProgress(0);
            return;
        }
        
        setProgress(0.35);
        log('Extracting segments');
        extractedSegments = extractSegments(audioBuffer, sampleRate, segments);
        
        setProgress(0.75);
        log(`Extracted ${extractedSegments.length} segments`);
        
        const totalDur = extractedSegments.reduce((a, b) => a + b.duration, 0);
        $('#mTotal').textContent = String(extractedSegments.length);
        $('#mDur').textContent = `${totalDur.toFixed(1)} s`;
        $('#mAvg').textContent = `${(totalDur / (extractedSegments.length || 1)).toFixed(1)} s`;
        
        $('#results').innerHTML = '';
        extractedSegments.forEach((f) => {
            const url = URL.createObjectURL(f.blob);
            const card = document.createElement('div');
            card.className = 'seg';
            card.innerHTML = `<h4>${f.filename}</h4><div class="meta">${f.start_time.toFixed(2)}s - ${f.end_time.toFixed(2)}s (${f.duration.toFixed(2)}s)</div>`;
            
            const audio = document.createElement('audio');
            audio.controls = true;
            audio.src = url;
            audio.style.width = '100%';
            
            const row = document.createElement('div');
            row.className = 'row';
            const a = document.createElement('a');
            a.href = url;
            a.download = f.filename;
            a.className = 'btn ghost';
            a.textContent = 'Download';
            
            const t = document.createElement('div');
            t.className = 'status';
            t.textContent = `Text: ${f.text}`;
            
            row.appendChild(a);
            card.appendChild(audio);
            card.appendChild(t);
            card.appendChild(row);
            $('#results').appendChild(card);
        });
        
        $('#resultsStatus').textContent = 'Segments ready.';
        $('#downloadZipBtn').disabled = false;
        setStep(3, 'complete');
        setProgress(1);
        
    } catch (err) {
        log(`Error: ${err.message}`);
        $('#resultsStatus').textContent = 'Processing failed.';
        setStep(3, '');
        setProgress(0);
    }
});

$('#resetBtn').addEventListener('click', () => {
    clearSegmentationUI();
    $('#audioInput').value = '';
    $('#transcriptFile').value = '';
    $('#audioName').textContent = 'Supported: MP3, WAV, M4A, FLAC, OGG, MP4';
    $('#transcriptFileName').textContent = 'Plain text with timestamps';
    parsedSegments = [];
    currentTranscript = null;
    currentAudioDownload = null;
    setStep(1, '');
    setStep(2, '');
    setStep(3, '');
    $('#useYouTubeBtn').disabled = true;
    $('#useAudioBtn').disabled = true;
});

$('#downloadZipBtn').addEventListener('click', async () => {
    if (!extractedSegments.length) return;
    
    $('#downloadZipBtn').disabled = true;
    $('#downloadZipBtn').textContent = 'Preparing ZIP...';
    
    const blob = await makeZip(extractedSegments);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audio_segments_${(audioFile ? audioFile.name.replace(/\\.[^.]+$/, '') : 'output')}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    
    $('#downloadZipBtn').disabled = false;
    $('#downloadZipBtn').textContent = 'Download all (ZIP)';
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (audioPollInterval) {
        clearInterval(audioPollInterval);
    }
});

</script>
</body>
</html>"""

# ======= API Routes =======
@app.route("/")
def index():
    return render_template_string(AUDIOCLIP_STUDIO_PAGE)

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

# ======= Audio Download Routes =======
@app.route('/api/audio/download', methods=['POST'])
def start_audio_download():
    """Start the audio download process"""
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({"success": False, "error": "No URL provided"}), 400
    
    download_id = str(uuid.uuid4())
    
    download_status[download_id] = {
        'progress': 0,
        'status': 'Initializing download...',
        'success': False,
        'error': None,
        'filename': None,
        'audio_data': None,  # Store audio data in memory
        'created_at': time.time()
    }
    
    thread = threading.Thread(target=download_audio_background, args=(url, download_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "download_id": download_id})

@app.route('/api/audio/progress/<download_id>')
def get_audio_progress(download_id):
    """Get download progress"""
    if download_id not in download_status:
        return jsonify({"success": False, "error": "Download not found"}), 404
    
    status = download_status[download_id]
    return jsonify({
        "success": True,
        "progress": status['progress'],
        "status": status['status'],
        "success": status['success'],
        "error": status['error'],
        "filename": status['filename']
    })

@app.route('/api/audio/file/<download_id>')
def get_audio_file(download_id):
    """Download the completed audio file - streams from memory"""
    if download_id not in download_status:
        return 'Download not found', 404
    
    status = download_status[download_id]
    if not status['success'] or not status['audio_data']:
        return 'Download not ready or failed', 400
    
    filename = status['filename'] or 'audio.wav'
    
    try:
        # Create BytesIO object from stored audio data
        audio_stream = BytesIO(status['audio_data'])
        
        # Send file with proper headers for direct download
        return send_file(
            audio_stream,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/wav'
        )
    except Exception as e:
        return str(e), 500

@app.route('/api/cleanup')
def cleanup_old_downloads():
    """Clean up old download status entries - now only cleans memory"""
    current_time = time.time()
    to_remove = []
    
    for download_id, status in download_status.items():
        # Clean up downloads older than 1 hour
        if current_time - status.get('created_at', current_time) > 3600:
            to_remove.append(download_id)
    
    for download_id in to_remove:
        del download_status[download_id]
    
    return jsonify({"success": True, "cleaned": len(to_remove)})

# Background cleanup task
def background_cleanup():
    """Periodic cleanup of old downloads"""
    while True:
        time.sleep(1800)  # Run every 30 minutes
        current_time = time.time()
        to_remove = []
        
        for download_id, status in download_status.items():
            if current_time - status.get('created_at', current_time) > 3600:
                to_remove.append(download_id)
        
        for download_id in to_remove:
            del download_status[download_id]

# Start cleanup thread
cleanup_thread = threading.Thread(target=background_cleanup, daemon=True)
cleanup_thread.start()

if __name__ == "__main__":
    app.run(debug=True, threaded=True)