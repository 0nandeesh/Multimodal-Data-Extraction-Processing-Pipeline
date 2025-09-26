import os
import re
import streamlit as st
import yt_dlp
from groq import Groq
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

# ------- CONFIG -------
DOWNLOAD_DIR = "downloads"
GROQ_MAX_FILESIZE_BYTES = 250 * 1024 * 1024  # 250MB
GROQ_API_KEY = "GROQ_API_KEY"  # Replace with your Groq API key

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ------- UTILITIES -------
def clear_transcripts_and_audio():
    """
    Remove keys from Streamlit session_state related to downloaded/transcribed content.
    """
    keys_to_clear = [k for k in st.session_state.keys() if (
        k.startswith("transcribed_") or
        k.startswith("transcribed_c_") or
        k.startswith("audio_") or
        k.startswith("audio_c_") or
        k.startswith("captions_") or
        k.startswith("video_") or
        k.startswith("video_c_") or
        k == "uploaded_transcript"
    )]
    for key in keys_to_clear:
        del st.session_state[key]

def sanitize_filename(name, max_len=50):
    """
    Sanitize a filename by keeping alphanumeric characters and a few safe symbols.
    Limit length to max_len. Returns 'unknown_video' if results empty.
    """
    if not isinstance(name, str):
        return "unknown_video"
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    return safe_name[:max_len] if safe_name else "unknown_video"

def download_audio(video_url, video_title):
    """
    Download audio-only stream from YouTube video with yt_dlp.
    Returns (message, filepath or None).
    Skips download if audio file already exists.
    """
    if not video_url or not isinstance(video_url, str):
        return "Invalid video URL", None

    safe_filename = sanitize_filename(video_title)
    audio_exts = ('.webm', '.m4a', '.mp3', '.ogg', '.wav')
    existing_files = [f for f in os.listdir(DOWNLOAD_DIR)
                      if f.startswith(safe_filename) and f.endswith(audio_exts)]

    if existing_files:
        return f"Audio file already exists: {os.path.join(DOWNLOAD_DIR, existing_files[0])}", os.path.join(DOWNLOAD_DIR, existing_files[0])

    audio_file_template = os.path.join(DOWNLOAD_DIR, f"{safe_filename}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': audio_file_template,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(video_url, download=True)

        downloaded_files = [f for f in os.listdir(DOWNLOAD_DIR)
                            if f.startswith(safe_filename) and f.endswith(audio_exts)]

        if not downloaded_files:
            return "Download completed but audio file not found", None

        return "Audio downloaded successfully", os.path.join(DOWNLOAD_DIR, downloaded_files[0])

    except Exception as e:
        return f"Error during audio download: {e}", None

def download_video(video_url, video_title):
    """
    Download video-only stream from YouTube video with yt_dlp (no audio).
    Returns (message, filepath or None).
    Skips download if video file already exists.
    """
    if not video_url or not isinstance(video_url, str):
        return "Invalid video URL", None

    safe_filename = sanitize_filename(video_title)
    video_exts = ('.mp4', '.webm', '.mkv', '.flv', '.avi')

    existing_files = [f for f in os.listdir(DOWNLOAD_DIR)
                      if f.startswith(safe_filename) and f.endswith(video_exts)]
    if existing_files:
        return f"Video file already exists: {os.path.join(DOWNLOAD_DIR, existing_files[0])}", os.path.join(DOWNLOAD_DIR, existing_files[0])

    video_file_template = os.path.join(DOWNLOAD_DIR, f"{safe_filename}.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo',
        'outtmpl': video_file_template,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=True)

            video_file_path = ydl.prepare_filename(info_dict)
            if os.path.exists(video_file_path):
                return "Video downloaded successfully", video_file_path

        downloaded_files = [f for f in os.listdir(DOWNLOAD_DIR)
                            if f.startswith(safe_filename) and f.endswith(video_exts)]
        if downloaded_files:
            return "Video downloaded successfully", os.path.join(DOWNLOAD_DIR, downloaded_files[0])

        return "Download completed but video file not found", None

    except Exception as e:
        return f"Error during video download: {e}", None

def get_playlist_videos(playlist_url):
    """
    Extract video metadata for all videos in a playlist URL.
    Returns list of dicts: {'title', 'url', 'num'}.
    If error, returns list with one dict containing error.
    """
    if not playlist_url or not isinstance(playlist_url, str):
        return [{"title": "Invalid playlist URL", "url": "", "num": ""}]

    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'dump_single_json': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)

            if not info or 'entries' not in info or not info['entries']:
                return [{"title": "No videos found in playlist", "url": "", "num": ""}]

            videos = []
            for idx, entry in enumerate(info.get('entries', []), start=1):
                if not entry or not isinstance(entry, dict):
                    continue
                title = entry.get('title') or f"Video {idx}"
                video_id = entry.get('id')
                if not video_id:
                    continue
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                videos.append({"title": title, "url": video_url, "num": idx})

            if not videos:
                return [{"title": "No videos found in playlist", "url": "", "num": ""}]

            return videos

    except Exception as e:
        return [{"title": f"Error: {e}", "url": "", "num": ""}]

def get_channel_videos(channel_url):
    """
    Extract video metadata for all videos of a YouTube channel (using /videos tab).
    Returns list of dicts: {'title', 'url', 'num'}.
    If error, returns list with one dict containing error.
    """
    if not channel_url or not isinstance(channel_url, str):
        return [{"title": "Invalid channel URL", "url": "", "num": ""}]

    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'dump_single_json': True,
        }
        if not channel_url.rstrip("/").endswith("/videos"):
            normalized_url = channel_url.rstrip("/") + "/videos"
        else:
            normalized_url = channel_url

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=False)

            if not info or 'entries' not in info or not info['entries']:
                return [{"title": "No videos found for channel", "url": "", "num": ""}]

            videos = []
            for idx, entry in enumerate(info.get('entries', []), start=1):
                if not entry or not isinstance(entry, dict):
                    continue
                title = entry.get('title') or f"Video {idx}"
                video_id = entry.get('id')
                if not video_id:
                    continue
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                videos.append({"title": title, "url": video_url, "num": idx})

            if not videos:
                return [{"title": "No videos found for channel", "url": "", "num": ""}]

            return videos

    except Exception as e:
        return [{"title": f"Error: {e}", "url": "", "num": ""}]

def transcribe_audio_groq(file_path, api_key=GROQ_API_KEY, model="whisper-large-v3-turbo"):
    """
    Call Groq's API to transcribe audio given a file path.
    Checks for file existence and size limits.
    Returns transcription text or error string.
    """
    if not file_path or not isinstance(file_path, str):
        return "Invalid file path."

    if not os.path.exists(file_path):
        return "Audio file missing. Please download audio first."

    if os.path.getsize(file_path) == 0:
        return "Audio file is empty or corrupted."

    file_size = os.path.getsize(file_path)
    max_mb = GROQ_MAX_FILESIZE_BYTES / (1024 * 1024)
    if file_size > GROQ_MAX_FILESIZE_BYTES:
        return f"Audio file size {file_size / (1024 * 1024):.2f} MB exceeds {max_mb} MB limit. Please trim file or select smaller audio."

    client = Groq(api_key=api_key)
    try:
        with open(file_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
                model=model,
                response_format="text"
            )
        if isinstance(transcription, str):
            return transcription
        elif hasattr(transcription, "text"):
            return transcription.text
        else:
            return str(transcription) or "Transcription returned empty result."
    except Exception as e:
        return f"Failed to transcribe: {e}"

def get_youtube_subs(video_url):
    """
    Fetch official YouTube transcripts (captions) using youtube_transcript_api.
    Returns transcript text or descriptive error message.
    """
    if not video_url or not isinstance(video_url, str):
        return "Invalid video URL."

    match = re.search(r"(?:v=|\/videos\/|\/embed\/|youtu\.be\/|\/shorts\/)([\w-]{11})", video_url)
    if not match:
        return "Could not extract Video ID from URL."

    video_id = match.group(1)

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        if transcript_list:
            transcript = None
            try:
                transcript = transcript_list.find_transcript(['en'])
            except NoTranscriptFound:
                all_langs = transcript_list._transcripts.keys()
                transcript = transcript_list.find_transcript(all_langs)

            fetched_transcript = transcript.fetch()
            text = "\n".join([entry['text'] for entry in fetched_transcript if entry.get('text')])
            if not text.strip():
                return "Transcript found but it is empty."
            return text
        else:
            return "No transcripts available for this video."
    except TranscriptsDisabled:
        return "Transcripts are disabled for this video."
    except NoTranscriptFound:
        return "No transcript/captions found for this video."
    except Exception as e:
        return f"Error fetching transcript: {e}"


# ------- STREAMLIT UI -------
st.set_page_config(page_title="VidRipper", layout="wide")

st.title("VidRipper")
st.markdown("##### YouTube Downloader (audio/video), Transcriber (Groq), and Official Captions Fetcher")

st.sidebar.title("YouTube Toolkit")
st.sidebar.info("Paste a YouTube playlist or channel URL. Extract video list, download audio/video, fetch captions, or transcribe using Groq Whisper.")

st.sidebar.markdown("""
**Instructions:**  
1. Enter a playlist or channel URL  
2. Click ‘Get Videos’ to load videos  
3. Download audio or video separately  
4. Click ‘Get Captions’ for the official YouTube transcript  
5. Use ‘Transcribe’ to generate with Groq Whisper  
6. Download anything as .txt  
""")

if "playlist_videos" not in st.session_state:
    st.session_state.playlist_videos = []
if "channel_videos" not in st.session_state:
    st.session_state.channel_videos = []

# --- Playlist Section ---
st.subheader("Playlist Videos")
playlist_url = st.text_input("Playlist URL", placeholder="https://www.youtube.com/playlist?list=...")

if st.button("Get Playlist Videos"):
    with st.spinner("Fetching playlist videos..."):
        clear_transcripts_and_audio()
        st.session_state.playlist_videos = get_playlist_videos(playlist_url)

if st.session_state.playlist_videos and not st.session_state.playlist_videos[0]["title"].startswith("Error"):
    st.success(f"Found {len(st.session_state.playlist_videos)} videos.")
    for idx, video in enumerate(st.session_state.playlist_videos):
        with st.expander(f"{video['num']}. {video['title']}"):
            col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

            with col1:
                if st.button("Download Audio", key=f"playlist_dl_audio_{idx}"):
                    with st.spinner(f"Downloading audio: {video['title'][:30]}..."):
                        message, audio_file = download_audio(video['url'], video['title'])
                    if not message.startswith("Error") and audio_file:
                        st.success(message)
                        st.session_state[f"audio_{idx}"] = audio_file
                        st.write(f"File: `{audio_file}`")
                    else:
                        st.error(message)

            with col2:
                video_key = f"video_{idx}"
                if st.button("Download Video", key=f"playlist_dl_video_{idx}"):
                    with st.spinner(f"Downloading video: {video['title'][:30]}..."):
                        message, video_file = download_video(video['url'], video['title'])
                    if not message.startswith("Error") and video_file:
                        st.success(message)
                        st.session_state[video_key] = video_file
                        st.write(f"File: `{video_file}`")
                    else:
                        st.error(message)
                if video_key in st.session_state:
                    file_path = st.session_state[video_key]
                    if os.path.exists(file_path):
                        with open(file_path, "rb") as vf:
                            st.download_button(
                                label="⬇️ Download Video File",
                                data=vf,
                                file_name=os.path.basename(file_path),
                                mime="video/mp4" if file_path.endswith(".mp4")
                                else "video/webm" if file_path.endswith(".webm")
                                else "application/octet-stream",
                                key=f"download_button_{video_key}"
                            )

            with col3:
                trans_key = f"transcribed_{idx}"
                if st.button("Transcribe", key=f"playlist_tr_{idx}"):
                    with st.spinner("Transcribing..."):
                        audio_file = st.session_state.get(f"audio_{idx}")
                        if not audio_file:
                            msg, audio_file = download_audio(video['url'], video['title'])
                            if msg.startswith("Error") or not audio_file:
                                st.error(msg)
                                audio_file = None
                        if audio_file and os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                            transcript = transcribe_audio_groq(audio_file)
                            if not transcript or transcript.startswith("Failed to transcribe:"):
                                st.error(transcript if transcript else "Transcription failed.")
                            else:
                                st.session_state[trans_key] = transcript
                                st.success("✅ Transcription completed!")
                        else:
                            st.error("Audio file missing or corrupted—please try 'Download Audio' first.")
                if trans_key in st.session_state:
                    st.markdown("**Groq Transcription:**")
                    st.text_area("Transcript", st.session_state[trans_key], height=200, key=trans_key)
                    file_name = sanitize_filename(video['title']) + "_transcript.txt"
                    st.download_button(
                        label="⬇️ Download Transcript",
                        data=st.session_state[trans_key],
                        file_name=file_name,
                        mime="text/plain",
                        key=f"download_{trans_key}",
                    )

            with col4:
                cap_key = f"captions_{idx}"
                if st.button("Get Captions (Show Transcript)", key=f"playlist_cap_{idx}"):
                    with st.spinner("Fetching YouTube captions..."):
                        captions = get_youtube_subs(video['url'])
                        if ("No transcript" in captions or "disabled" in captions or
                                "Unable" in captions or captions.startswith("Error")):
                            st.error(captions)
                        else:
                            st.session_state[cap_key] = captions
                            st.success("✅ Captions loaded!")
                if cap_key in st.session_state:
                    st.markdown("**YouTube Captions as Transcript:**")
                    st.text_area("Captions", st.session_state[cap_key], height=200, key=f"text_cap_{cap_key}")
                    file_name = sanitize_filename(video['title']) + "_captions.txt"
                    st.download_button(
                        label="⬇️ Download Captions",
                        data=st.session_state[cap_key],
                        file_name=file_name,
                        mime="text/plain",
                        key=f"download_{cap_key}",
                    )
elif st.session_state.playlist_videos:
    st.error("Could not fetch playlist videos. Please check the link.")

# --- Channel Section ---
st.subheader("Channel Videos")
channel_url = st.text_input("Channel URL", placeholder="https://www.youtube.com/@channelname")

if st.button("Get Channel Videos"):
    with st.spinner("Fetching channel videos..."):
        clear_transcripts_and_audio()
        st.session_state.channel_videos = get_channel_videos(channel_url)

if st.session_state.channel_videos and not st.session_state.channel_videos[0]["title"].startswith("Error"):
    st.success(f"Found {len(st.session_state.channel_videos)} videos.")
    for idx, video in enumerate(st.session_state.channel_videos):
        with st.expander(f"{video['num']}. {video['title']}"):
            col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

            with col1:
                if st.button("Download Audio", key=f"channel_dl_audio_{idx}"):
                    with st.spinner(f"Downloading audio: {video['title'][:30]}..."):
                        message, audio_file = download_audio(video['url'], video['title'])
                    if not message.startswith("Error") and audio_file:
                        st.success(message)
                        st.session_state[f"audio_c_{idx}"] = audio_file
                        st.write(f"File: `{audio_file}`")
                    else:
                        st.error(message)

            with col2:
                video_key = f"video_c_{idx}"
                if st.button("Download Video", key=f"channel_dl_video_{idx}"):
                    with st.spinner(f"Downloading video: {video['title'][:30]}..."):
                        message, video_file = download_video(video['url'], video['title'])
                    if not message.startswith("Error") and video_file:
                        st.success(message)
                        st.session_state[video_key] = video_file
                        st.write(f"File: `{video_file}`")
                    else:
                        st.error(message)
                if video_key in st.session_state:
                    file_path = st.session_state[video_key]
                    if os.path.exists(file_path):
                        with open(file_path, "rb") as vf:
                            st.download_button(
                                label="⬇️ Download Video File",
                                data=vf,
                                file_name=os.path.basename(file_path),
                                mime="video/mp4" if file_path.endswith(".mp4")
                                else "video/webm" if file_path.endswith(".webm")
                                else "application/octet-stream",
                                key=f"download_button_{video_key}"
                            )

            with col3:
                trans_key = f"transcribed_c_{idx}"
                if st.button("Transcribe", key=f"channel_tr_{idx}"):
                    with st.spinner("Transcribing..."):
                        audio_file = st.session_state.get(f"audio_c_{idx}")
                        if not audio_file:
                            msg, audio_file = download_audio(video['url'], video['title'])
                            if msg.startswith("Error") or not audio_file:
                                st.error(msg)
                                audio_file = None
                        if audio_file and os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                            transcript = transcribe_audio_groq(audio_file)
                            if not transcript or transcript.startswith("Failed to transcribe:"):
                                st.error(transcript if transcript else "Transcription failed.")
                            else:
                                st.session_state[trans_key] = transcript
                                st.success("✅ Transcription completed!")
                        else:
                            st.error("Audio file missing or corrupted—please try 'Download Audio' first.")
                if trans_key in st.session_state:
                    st.markdown("**Groq Transcription:**")
                    st.text_area("Transcript", st.session_state[trans_key], height=200, key=trans_key)
                    file_name = sanitize_filename(video['title']) + "_transcript.txt"
                    st.download_button(
                        label="⬇️ Download Transcript",
                        data=st.session_state[trans_key],
                        file_name=file_name,
                        mime="text/plain",
                        key=f"download_{trans_key}",
                    )

            with col4:
                cap_key = f"captions_c_{idx}"
                if st.button("Get Captions (Show Transcript)", key=f"channel_cap_{idx}"):
                    with st.spinner("Fetching YouTube captions..."):
                        captions = get_youtube_subs(video['url'])
                        if ("No transcript" in captions or "disabled" in captions or
                                "Unable" in captions or captions.startswith("Error")):
                            st.error(captions)
                        else:
                            st.session_state[cap_key] = captions
                            st.success("✅ Captions loaded!")
                if cap_key in st.session_state:
                    st.markdown("**YouTube Captions as Transcript:**")
                    st.text_area("Captions", st.session_state[cap_key], height=200, key=f"text_cap_{cap_key}")
                    file_name = sanitize_filename(video['title']) + "_captions.txt"
                    st.download_button(
                        label="⬇️ Download Captions",
                        data=st.session_state[cap_key],
                        file_name=file_name,
                        mime="text/plain",
                        key=f"download_{cap_key}",
                    )
elif st.session_state.channel_videos:
    st.error("Could not fetch channel videos. Please check the link.")

# --- Local Audio Transcription ---
st.markdown("---")
st.subheader("🎧 Transcribe Local Audio File")
uploaded_audio = st.file_uploader("Upload audio file (max 250MB):", type=["mp3", "m4a", "webm", "ogg", "wav"])
if uploaded_audio:
    temp_path = os.path.join(DOWNLOAD_DIR, uploaded_audio.name)
    with open(temp_path, "wb") as f:
        f.write(uploaded_audio.getbuffer())
    st.success(f"File uploaded to {temp_path}")

    if st.button("Transcribe Uploaded File"):
        if os.path.getsize(temp_path) > GROQ_MAX_FILESIZE_BYTES:
            st.error(f"Uploaded audio file size exceeds 250 MB limit. Please upload a smaller file.")
        else:
            with st.spinner("Transcribing..."):
                transcript = transcribe_audio_groq(temp_path)
            if not transcript or transcript.startswith("Failed to transcribe:"):
                st.error(transcript if transcript else "Transcription failed.")
            else:
                st.markdown("**Groq Transcription:**")
                st.text_area("Transcript", transcript, height=200, key="uploaded_transcript")
                file_name = sanitize_filename(uploaded_audio.name) + "_transcript.txt"
                st.download_button(
                    label="⬇️ Download Transcript",
                    data=transcript,
                    file_name=file_name,
                    mime="text/plain",
                    key="download_uploaded_transcript",
                )

# --- Footer / Info ---
if os.path.exists(DOWNLOAD_DIR):
    audio_files = len([f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(('.webm', '.m4a', '.mp3', '.ogg', '.wav'))])
    video_files = len([f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(('.mp4', '.webm', '.mkv', '.flv', '.avi'))])
    st.info(f"Audio files stored locally: {audio_files} in '{DOWNLOAD_DIR}' folder.")
    st.info(f"Video files stored locally: {video_files} in '{DOWNLOAD_DIR}' folder.")

st.markdown("""
---
Transcription powered by **Groq Whisper V3**.  
Audio and video are downloaded separately—no muxing.  
Captions fetched directly from YouTube, where available.
""")
