# Multimodal Data Extraction Processing Pipeline

A scalable pipeline for extracting and processing audio, video, and transcripts from YouTube to create datasets for AI model training.

## Overview & Features

Three integrated applications:

1. **YouTube Transcript Extractor** (`app.py`) - Extract official YouTube transcripts/captions
2. **VidRipper** (`audio_vedio.py`) - Download audio/video from playlists and channels with Groq transcription  
3. **AudioClip Studio** (`version2.py`) - Unified transcript extraction and intelligent audio segmentation

Key capabilities:
- High-quality WAV audio downloads (192kbps)
- Batch processing for playlists and channels
- Official YouTube captions + Groq Whisper V3 transcription
- Client-side audio segmentation with timestamp alignment
- Multiple export formats (TXT, JSON, CSV, ZIP)

## Quick Start Guide

### Installation
```bash
pip install flask streamlit yt-dlp groq youtube-transcript-api pandas defusedxml requests
```

### Setup
1. Get [Groq API key](https://console.groq.com/)
2. Update `GROQ_API_KEY` in `audio_vedio.py`

### Running Applications
```bash
# Transcript Extractor
python app.py

# VidRipper  
streamlit run audio_vedio.py

# AudioClip Studio (recommended)
python version2.py
```

## Usage Guide

### Basic Workflow
1. **Extract Transcripts**: Paste YouTube URL → Extract → Download (TXT/JSON/CSV)
2. **Download Audio**: Use playlist/channel URLs for batch processing
3. **Transcribe**: Upload local audio files (max 250MB) → Groq Whisper V3
4. **Segment Audio**: Upload audio + transcript → Auto-segment by timestamps → Download ZIP

### Supported Formats
```
# URLs
https://www.youtube.com/watch?v=VIDEO_ID
https://www.youtube.com/playlist?list=PLAYLIST_ID
https://www.youtube.com/@channelname

# Transcript timestamps
[01:30] Text here
01:30 - 01:35 Text here
00:01:30,500 --> 00:01:35,000 (SRT format)
```

## Architecture

### Data Flow
```
YouTube URL → Extract Info → Download Audio/Video
           → Extract Transcript → Timestamp Parsing
           → Audio Segmentation → Export Segments
```

### Core Technologies
- **Backend**: Flask, Streamlit
- **Processing**: yt-dlp, Web Audio API, FFmpeg
- **Transcription**: Groq Whisper V3
- **Export**: Pandas, JSZip

## API Reference

### Key Endpoints
```http
POST /api/extract              # Extract transcript
POST /api/audio/download       # Start audio download
GET /api/audio/progress/{id}   # Check progress
GET /api/audio/file/{id}       # Download file
```

### Configuration
```python
# Audio settings
'preferredcodec': 'wav'
'preferredquality': '192'

# Groq limits
GROQ_MAX_FILESIZE_BYTES = 250 * 1024 * 1024  # 250MB
```

## Dataset Creation Workflow

### For Speech Recognition
1. **Collect diverse content**: Educational channels, news, podcasts
2. **Extract high-quality audio**: WAV format, preserve sample rates
3. **Generate accurate transcripts**: Official captions preferred, Groq fallback
4. **Create training segments**: 2-30 seconds, balanced across speakers/topics

### For Multimodal Models  
1. **Audio-text alignment**: Precise timestamps, context preservation
2. **Quality filtering**: Remove poor audio, validate transcript accuracy
3. **Batch processing**: Automated channel processing, error recovery

## Legal & Ethical Use

**Important**: Only process content you have permission to use. Respect copyright, follow YouTube ToS, ensure fair use compliance, and credit original creators.

## Troubleshooting

- **Audio download fails**: Update yt-dlp, check video availability
- **Groq API errors**: Verify API key, check file size limits
- **Memory issues**: Use smaller batches, restart if needed
- **No transcript**: Check captions enabled, try audio transcription

---

Built for AI research community. Use responsibly.
