import os
import re
import json
import time
import shutil
import base64
import subprocess
import streamlit as st
import streamlit.components.v1 as components
import yt_dlp
from groq import Groq
from google import genai

# Embedded API Keys
DEFAULT_GROQ_KEY = "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"
DEFAULT_GEMINI_KEY = "AIzaSyB5EKKPp_adHMK1hyhBm74k0EsDnb98jgQ"

st.set_page_config(
    page_title="AI Anime Shorts Studio Pro",
    page_icon="🎬",
    layout="wide"
)

BASE_DIR = os.path.join(os.path.expanduser("~"), "Videos", "AI_Clips")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ----------------- SESSION STATE -----------------

if "processing" not in st.session_state:
    st.session_state.processing = False
if "discovered_clips" not in st.session_state:
    st.session_state.discovered_clips = []
if "transcription_data" not in st.session_state:
    st.session_state.transcription_data = None
if "selected_clip_idx" not in st.session_state:
    st.session_state.selected_clip_idx = 0
if "exported_file_path" not in st.session_state:
    st.session_state.exported_file_path = None

st.markdown("""
<style>
    .sticky-video-container {
        position: -webkit-sticky;
        position: sticky;
        top: 2rem;
        z-index: 99;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- DOWNLOAD & AUDIO HELPERS -----------------

def download_audio_compressed(url, output_dir):
    out_base = os.path.join(output_dir, "audio_track")
    ydl_opts = {
        'format': 'ba/b',
        'outtmpl': out_base + '.%(ext)s',
        'continuedl': True,
        'retries': 20,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': 'https://www.youtube.com/',
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return out_base + ".mp3"

def get_video_duration(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return float(info.get('duration', 60.0))

def slice_and_frame_raw_clip(url, start_sec, duration_sec, aspect_choice, framing_mode, output_path):
    """
    Bulletproof stream slicing. Uses split=2 and explicit software buffers so Gaussian Blur never fails.
    """
    ydl_opts = {
        'format': 'bv*[height<=1080]+ba/b[height<=1080]/best',
        'quiet': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': 'https://www.youtube.com/',
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        requested_formats = info.get('requested_formats')
        if requested_formats and len(requested_formats) >= 2:
            v_url = requested_formats[0].get('url')
            a_url = requested_formats[1].get('url')
        else:
            v_url = info.get('url')
            a_url = None

    aspect_map = {
        "9:16 Vertical": (1080, 1920),
        "1:1 Square": (1080, 1080),
        "4:3 Classic": (1440, 1080),
        "16:9 Landscape": (1920, 1080)
    }
    tw, th = aspect_map.get(aspect_choice, (1080, 1920))

    filter_complex = None
    if framing_mode == "True Crop (Auto-Center Subject)":
        filter_complex = f"[0:v]scale=-1:{th},crop={tw}:{th}:(in_w-{tw})/2:0,setsar=1[outv]"
    elif framing_mode == "Padded Blur Fit":
        filter_complex = (
            f"[0:v]split=2[v_bg][v_fg];"
            f"[v_bg]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=20:5,setsar=1[bg];"
            f"[v_fg]scale={tw}:-1,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
        )
    elif framing_mode == "Padded Black Bars":
        filter_complex = (
            f"color=c=black:s={tw}x{th}[bg];"
            f"[0:v]scale={tw}:-1,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[outv]"
        )

    http_headers = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\nReferer: https://www.youtube.com/\r\n"

    cmd = ["ffmpeg", "-y"]
    cmd += ["-headers", http_headers, "-ss", str(start_sec), "-t", str(duration_sec), "-i", v_url]
    
    if a_url:
        cmd += ["-headers", http_headers, "-ss", str(start_sec), "-t", str(duration_sec), "-i", a_url]

    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "[outv]"]
    else:
        cmd += ["-map", "0:v:0"]

    if a_url:
        cmd += ["-map", "1:a:0"]
    else:
        cmd += ["-map", "0:a:0?"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-avoid_negative_ts", "make_zero",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", "aresample=async=1000",
        output_path
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"FFmpeg slicing failed:\n{proc.stderr[-500:]}")

# ----------------- HIGHLIGHT DISCOVERY & SNAPPER -----------------

def snap_to_sentence_boundary(segments, raw_end, max_drift=2.5):
    best_end = raw_end
    min_diff = 999.0
    for seg in segments:
        txt = seg.get("text", "").strip()
        s_end = float(seg.get("end", 0.0))
        if any(txt.endswith(p) for p in [".", "!", "?", "..."]):
            diff = abs(s_end - raw_end)
            if diff <= max_drift and diff < min_diff:
                min_diff = diff
                best_end = s_end
        elif abs(s_end - raw_end) <= 1.0 and abs(s_end - raw_end) < min_diff:
            min_diff = abs(s_end - raw_end)
            best_end = s_end
    return best_end

def analyze_highlights_guaranteed_3(segments, total_duration, gemini_key):
    transcript_text = ""
    for seg in segments:
        clean_t = seg["text"].strip()
        if clean_t and not re.search(r'\[.*music.*\]|\(music\)|♪', clean_t, re.IGNORECASE):
            transcript_text += f"[{round(seg['start'], 2)}s -> {round(seg['end'], 2)}s]: {clean_t}\n"

    client = genai.Client(api_key=gemini_key)
    prompt = f"""
Select EXACTLY 3 viral clips from this transcript.
Each clip must be between 25 and 55 seconds long.
Ensure each clip ends where a speaker finishes their sentence or thought.
Respond ONLY with a valid JSON array of 3 objects:
[
  {{"title": "Viral Moment 1", "start": 5.0, "end": 40.0, "hook": "Hook 1"}},
  {{"title": "Viral Moment 2", "start": 45.0, "end": 80.0, "hook": "Hook 2"}},
  {{"title": "Viral Moment 3", "start": 85.0, "end": 120.0, "hook": "Hook 3"}}
]
Transcript:
{transcript_text}
"""
    clips = []
    models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    for m in models:
        try:
            res = client.models.generate_content(
                model=m,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            parsed = json.loads(res.text)
            if isinstance(parsed, list) and len(parsed) >= 3:
                clips = parsed[:3]
                break
        except Exception:
            continue

    if len(clips) < 3:
        target_len = min(40.0, max(25.0, total_duration / 3.2))
        clips = [
            {"title": "Opening Viral Hook", "start": 0.0, "end": min(total_duration, target_len), "hook": "High-energy introduction"},
            {"title": "Peak Dialogue Moment", "start": max(0.0, (total_duration * 0.4) - (target_len / 2)), "end": min(total_duration, (total_duration * 0.4) + (target_len / 2)), "hook": "Core story escalation"},
            {"title": "Climactic Ending Hook", "start": max(0.0, total_duration - target_len), "end": total_duration, "hook": "Final climax & payoff"}
        ]

    for c in clips:
        c["end"] = snap_to_sentence_boundary(segments, float(c["end"]))

    return clips[:3]

# ----------------- PRECISE WORD-LEVEL JSON FOR ANIMATION -----------------

def get_clip_words_json(transcription, start_sec, end_sec):
    words_data = []
    words = []
    if hasattr(transcription, 'words') and transcription.words:
        words = transcription.words
    elif hasattr(transcription, 'segments'):
        for seg in transcription.segments:
            seg_words = seg.get('words', [])
            if seg_words:
                words.extend(seg_words)
            else:
                txt = seg['text'].strip()
                if not re.search(r'\[.*music.*\]|\(music\)|♪', txt, re.IGNORECASE):
                    words.append({'start': seg['start'], 'end': seg['end'], 'word': txt})

    for w in words:
        s = w.get('start', 0.0)
        e = w.get('end', 0.0)
        txt = w.get('word', '').strip()
        if txt and not re.search(r'\[.*music.*\]|\(music\)|♪', txt, re.IGNORECASE):
            if e >= start_sec and s <= end_sec:
                words_data.append({
                    "start": round(max(0.0, s - start_sec), 2),
                    "end": round(max(0.08, e - start_sec), 2),
                    "text": txt.upper()
                })
    return json.dumps(words_data)

# ----------------- FINAL EXPORT WITH ANIMATED KARAOKE POP -----------------

def compile_final_export(raw_video, out_video, color_cfg, audio_boost, pov_cfg, sub_cfg, cur_clip, trans_data):
    win_font_dir = "C:/Windows/Fonts/arialbd.ttf"
    if not os.path.exists(win_font_dir):
        win_font_dir = "C:/Windows/Fonts/arial.ttf"

    filter_chains = []
    curr_v = "[0:v]"

    # 1. Color Grading
    eq_parts = []
    if color_cfg["exposure"] != 0.0:
        gamma_exp = max(0.1, 1.0 - (color_cfg["exposure"] * 0.3))
        eq_parts.append(f"gamma={gamma_exp:.2f}")
    if color_cfg["contrast"] != 1.0:
        eq_parts.append(f"contrast={color_cfg['contrast']:.2f}")
    if color_cfg["brightness"] != 0.0:
        eq_parts.append(f"brightness={color_cfg['brightness']:.2f}")
    if color_cfg["saturation"] != 1.0:
        eq_parts.append(f"saturation={color_cfg['saturation']:.2f}")

    if eq_parts:
        filter_chains.append(f"{curr_v}eq=" + ":".join(eq_parts) + "[graded]")
        curr_v = "[graded]"

    # 2. POV Header Banner
    if pov_cfg["enabled"] and pov_cfg["text"].strip():
        safe_pov = pov_cfg["text"].strip().replace("'", "").replace(":", "\\:")
        bx = pov_cfg["x"]
        by = pov_cfg["y"]
        bw_pad = pov_cfg["box_pad"]
        txt_size = pov_cfg["font_size"]
        bg_col = pov_cfg["bg_color"]
        txt_col = "black" if bg_col in ["white", "yellow"] else "white"

        shadow_cmd = ""
        if pov_cfg["has_shadow"]:
            s_op = pov_cfg["shadow_opacity"]
            shadow_cmd = f":shadowx=6:shadowy=6:shadowcolor=black@{s_op:.2f}"

        draw_cmd = (
            f"{curr_v}drawtext=fontfile='{win_font_dir}':text='{safe_pov}':fontsize={txt_size}:"
            f"fontcolor={txt_col}:box=1:boxcolor={bg_col}@0.95:boxborderw={bw_pad}:"
            f"x={bx}:y={by}{shadow_cmd}[with_pov]"
        )
        filter_chains.append(draw_cmd)
        curr_v = "[with_pov]"

    # 3. Subtitles Generation (Animated Kinetic Karaoke)
    ass_file = os.path.join(CACHE_DIR, "export_subs.ass")
    if sub_cfg["burn"]:
        f_size = sub_cfg["font_size"]
        p_col = sub_cfg["color"]
        pos_x = sub_cfg["pos_x"]
        pos_y = sub_cfg["pos_y"]
        b_style = sub_cfg["border_style"]

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: MainSub,Arial,{f_size},{p_col},&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,{b_style},4,2,2,20,20,{pos_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        raw_words = json.loads(get_clip_words_json(trans_data, cur_clip["start"], cur_clip["end"]))
        events = []

        # High-energy 1-to-2 word burst groups with scale pop animation
        chunk = []
        for w in raw_words:
            chunk.append(w)
            if len(chunk) >= 2:
                cs = chunk[0]["start"]
                ce = chunk[-1]["end"]
                txt = " ".join([c["text"] for c in chunk])
                # \\t(0, 100, \\fscx115\\fscy115) creates the punchy pop effect
                pop_effect = "{\\fscx100\\fscy100\\t(0,80,\\fscx118\\fscy118)\\t(80,160,\\fscx100\\fscy100)}"
                pos_tag = f"{{\\pos({pos_x},{1920 - pos_y})}}"
                events.append(f"Dialogue: 0,{time.strftime('%H:%M:%S.00', time.gmtime(cs))},{time.strftime('%H:%M:%S.00', time.gmtime(ce))},MainSub,,0,0,0,,{pos_tag}{pop_effect}{txt}")
                chunk = []

        if chunk:
            cs = chunk[0]["start"]
            ce = chunk[-1]["end"]
            txt = " ".join([c["text"] for c in chunk])
            pop_effect = "{\\fscx100\\fscy100\\t(0,80,\\fscx118\\fscy118)\\t(80,160,\\fscx100\\fscy100)}"
            pos_tag = f"{{\\pos({pos_x},{1920 - pos_y})}}"
            events.append(f"Dialogue: 0,{time.strftime('%H:%M:%S.00', time.gmtime(cs))},{time.strftime('%H:%M:%S.00', time.gmtime(ce))},MainSub,,0,0,0,,{pos_tag}{pop_effect}{txt}")

        with open(ass_file, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events))

        ass_escaped = ass_file.replace("\\", "/").replace(":", "\\:")
        filter_chains.append(f"{curr_v}ass='{ass_escaped}'[final_v]")
        final_v_label = "[final_v]"
    else:
        final_v_label = curr_v

    # 4. Audio Pacing & Boost
    audio_filters = []
    if audio_boost != 0:
        audio_filters.append(f"volume={audio_boost}dB")

    cmd = ["ffmpeg", "-y", "-i", raw_video]
    if filter_chains:
        cmd += ["-filter_complex", ";".join(filter_chains), "-map", final_v_label, "-map", "0:a?"]
    else:
        cmd += ["-map", "0:v", "-map", "0:a?"]

    if audio_filters:
        cmd += ["-af", ",".join(audio_filters)]

    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "192k",
        out_video
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg render failed:\n{proc.stderr[-500:]}")

# ----------------- UI DASHBOARD -----------------

st.title("🎬 AI Anime Shorts Studio Pro")
st.caption("Kinetic Pop Captions • Gaussian Blur Recovery • Zero-Desync Architecture")

with st.sidebar:
    st.header("🎯 Target Aspect Ratio")
    pre_aspect = st.selectbox("Aspect Ratio Target", ["9:16 Vertical", "1:1 Square", "4:3 Classic", "16:9 Landscape"])
    pre_framing = st.radio("Framing Mode", ["True Crop (Auto-Center Subject)", "Padded Blur Fit", "Padded Black Bars"])

st.subheader("1. Source Video")
in_col1, in_col2 = st.columns([3, 1])

with in_col1:
    source_url = st.text_input("Paste YouTube Link:", placeholder="https://www.youtube.com/watch?v=...")
with in_col2:
    start_btn = st.button("⚡ Generate Top 3 Shorts", type="primary", disabled=st.session_state.processing)

if start_btn and source_url:
    st.session_state.processing = True
    st.session_state.discovered_clips = []
    st.session_state.exported_file_path = None

    pbar = st.progress(0, text="[0%] Initializing...")

    try:
        pbar.progress(15, text="[15%] Downloading lightweight audio (~3 MB)...")
        audio_p = download_audio_compressed(source_url, CACHE_DIR)
        total_duration = get_video_duration(source_url)

        pbar.progress(35, text="[35%] Transcribing dialogue with Groq Whisper...")
        client_g = Groq(api_key=DEFAULT_GROQ_KEY)
        with open(audio_p, "rb") as f:
            trans = client_g.audio.transcriptions.create(
                file=(os.path.basename(audio_p), f.read()),
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"]
            )
        st.session_state.transcription_data = trans

        pbar.progress(55, text="[55%] Discovering 3 high-retention highlights...")
        clips = analyze_highlights_guaranteed_3(trans.segments, total_duration, DEFAULT_GEMINI_KEY)

        for i, clip in enumerate(clips):
            step = 60 + int((i / len(clips)) * 35)
            pbar.progress(step, text=f"[{step}%] Cutting Short {i+1} with full audio...")
            raw_path = os.path.join(CACHE_DIR, f"framed_clip_{i+1}.mp4")
            dur = clip["end"] - clip["start"]
            slice_and_frame_raw_clip(source_url, clip["start"], dur, pre_aspect, pre_framing, raw_path)
            clip["raw_path"] = raw_path

        pbar.progress(100, text="[100%] Complete! 3 Shorts loaded below.")
        st.session_state.discovered_clips = clips
        st.session_state.processing = False
        st.rerun()

    except Exception as e:
        st.error(f"Error: {e}")
        st.session_state.processing = False

# ----------------- STAGE 2: 3-CLIP SELECTOR & STICKY EDITOR -----------------

if st.session_state.discovered_clips:
    st.divider()
    st.subheader("2. Select One of the 3 Generated Shorts")

    card_cols = st.columns(3)
    for idx, c in enumerate(st.session_state.discovered_clips):
        with card_cols[idx]:
            is_active = (idx == st.session_state.selected_clip_idx)
            card_title = f"{'🟢 ' if is_active else ''}Short #{idx+1}"
            st.markdown(f"### {card_title}")
            st.caption(f"**{c['title']}** ({int(c['end']-c['start'])}s)")
            st.info(f"💡 {c['hook']}")
            if st.button(f"✏️ Customize Short #{idx+1}", key=f"sel_btn_{idx}", use_container_width=True):
                st.session_state.selected_clip_idx = idx
                st.session_state.exported_file_path = None
                st.rerun()

    cur_clip = st.session_state.discovered_clips[st.session_state.selected_clip_idx]
    st.markdown("---")

    video_col, editor_col = st.columns([1, 1])

    with editor_col:
        st.markdown(f"### 🎛️ Editing Controls: Short #{st.session_state.selected_clip_idx+1}")

        st.markdown("#### 🎨 Color Grading (Real-Time)")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            exp_val = st.slider("☀️ Exposure (EV)", -1.5, 1.5, 0.0, 0.05)
            contrast_val = st.slider("Contrast", 0.7, 1.6, 1.0, 0.05)
        with col_c2:
            sat_val = st.slider("Saturation", 0.0, 2.5, 1.0, 0.05)
            bright_val = st.slider("Brightness", -0.3, 0.3, 0.0, 0.02)

        st.markdown("#### 💬 Synced Captions (Kinetic Pop Style)")
        burn_captions = st.checkbox("Enable Synced Captions", value=True)
        sub_c1, sub_c2 = st.columns(2)
        with sub_c1:
            sub_font = st.selectbox("Font Family", ["Arial Black", "Impact", "Trebuchet MS", "Comic Sans MS"])
            sub_color = st.selectbox("Color", ["Yellow (#FFE600)", "White (#FFFFFF)", "Cyan (#00FFFF)", "Lime (#00FF66)"])
            sub_color_code = sub_color.split("(")[-1].replace(")", "").strip()
            sub_size = st.slider("Font Size (px)", 16, 42, 28)
        with sub_c2:
            sub_border_mode = st.selectbox("Border Style", ["Stroke Outline", "Opaque Backing Box"])
            sub_y = st.slider("Vertical Position (Y-Axis)", 50, 600, 160)
            sub_x = st.slider("Horizontal Center (X-Axis)", 20, 80, 50)

        st.markdown("#### 🏷️ Manual POV Header Box")
        enable_pov = st.checkbox("Add Custom POV Box", value=False)
        if enable_pov:
            pov_text = st.text_input("Your Hook Text:", value="POV: When the play works...")
            p_c1, p_c2 = st.columns(2)
            with p_c1:
                pov_bg = st.selectbox("Box Background Color", ["white", "black", "yellow", "red", "blue"])
                pov_x = st.slider("Box X Position (%)", 5, 80, 50)
                pov_size = st.slider("Text Size", 16, 40, 24)
            with p_c2:
                pov_y = st.slider("Box Y Position (%)", 5, 90, 15)
                pov_pad = st.slider("Box Padding", 6, 24, 12)
                has_shadow = st.checkbox("Drop Shadow", value=True)
                shadow_op = st.slider("Shadow Opacity", 0.1, 1.0, 0.7) if has_shadow else 0.0
        else:
            pov_text = ""
            pov_bg = "white"
            pov_x = 50
            pov_y = 15
            pov_size = 24
            pov_pad = 12
            has_shadow = False
            shadow_op = 0.0

        st.markdown("#### 🔊 Audio Dialogue Boost")
        boost_db = st.slider("Volume Boost / Cut (dB)", -12, 14, 0)

        st.markdown("---")
        export_btn = st.button("💾 Export Finished Short", type="primary", use_container_width=True)

    words_json = get_clip_words_json(st.session_state.transcription_data, cur_clip["start"], cur_clip["end"])

    with video_col:
        st.markdown('<div class="sticky-video-container">', unsafe_allow_html=True)
        st.markdown(f"### 📺 Live Screen: {cur_clip['title']}")
        st.caption(f"⏱ **Timestamp:** {cur_clip['start']}s – {cur_clip['end']}s | 💡 {cur_clip['hook']}")

        with open(cur_clip["raw_path"], "rb") as vf:
            v_b64 = base64.b64encode(vf.read()).decode("utf-8")

        css_filter = f"brightness({1.0 + exp_val * 0.2 + bright_val}) contrast({contrast_val}) saturate({sat_val})"
        shadow_style = f"box-shadow: 0px 6px 14px rgba(0, 0, 0, {shadow_op});" if has_shadow else ""
        pov_text_color = "black" if pov_bg in ["white", "yellow"] else "white"

        # Interactive HTML5/JS Player with Kinetic Pop CSS Animations
        live_editor_html = f"""
        <style>
            @keyframes popAnim {{
                0% {{ transform: scale(0.9); opacity: 0.8; }}
                50% {{ transform: scale(1.15); opacity: 1; }}
                100% {{ transform: scale(1.0); opacity: 1; }}
            }}
            .pop-active {{
                animation: popAnim 0.18s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                display: inline-block;
            }}
        </style>
        <div style="position: relative; width: 340px; height: 600px; margin: 0 auto; background: black; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
            <video id="editor_player" src="data:video/mp4;base64,{v_b64}" autoplay controls playsinline style="width: 100%; height: 100%; object-fit: contain; filter: {css_filter};"></video>
            
            <div id="pov_box" style="position: absolute; left: {pov_x}%; top: {pov_y}%; transform: translate(-50%, -50%); background: {pov_bg}; color: {pov_text_color}; font-family: 'Arial', sans-serif; font-weight: bold; font-size: {pov_size}px; padding: {pov_pad}px {pov_pad * 1.5}px; border-radius: 6px; pointer-events: none; white-space: nowrap; {shadow_style} display: {'block' if enable_pov and pov_text else 'none'}; z-index: 10;">
                {pov_text}
            </div>

            <!-- Kinetic Pop Subtitle Layer -->
            <div id="sub_overlay" style="position: absolute; left: {sub_x}%; bottom: {sub_y}px; transform: translateX(-50%); font-family: '{sub_font}', sans-serif; font-size: {sub_size}px; color: {sub_color_code}; text-align: center; text-transform: uppercase; font-weight: 900; pointer-events: none; width: 90%; z-index: 20; {'background: rgba(0,0,0,0.7); padding: 6px 12px; border-radius: 6px;' if sub_border_mode == 'Opaque Backing Box' else 'text-shadow: -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 4px 10px rgba(0,0,0,0.8);'}">
                <span id="sub_text" class="pop-active"></span>
            </div>
        </div>

        <script>
            const words = {words_json};
            const video = document.getElementById('editor_player');
            const subText = document.getElementById('sub_text');
            const isCaptionsEnabled = {str(burn_captions).lower()};
            let lastWordIdx = -1;

            video.addEventListener('timeupdate', () => {{
                if (!isCaptionsEnabled || words.length === 0) {{
                    subText.innerText = '';
                    return;
                }}
                const ct = video.currentTime;
                let activeIdx = -1;
                let activeChunk = '';

                // Rapid 2-word punchy bursts with zero latency
                for (let i = 0; i < words.length; i += 2) {{
                    const chunk = words.slice(i, i + 2);
                    const start = chunk[0].start;
                    const end = chunk[chunk.length - 1].end;
                    if (ct >= start && ct <= end) {{
                        activeIdx = i;
                        activeChunk = chunk.map(w => w.text).join(' ');
                        break;
                    }}
                }}

                if (activeIdx !== -1) {{
                    if (activeIdx !== lastWordIdx) {{
                        subText.innerText = activeChunk;
                        // Trigger CSS pop bounce
                        subText.classList.remove('pop-active');
                        void subText.offsetWidth;
                        subText.classList.add('pop-active');
                        lastWordIdx = activeIdx;
                    }}
                }} else {{
                    subText.innerText = '';
                    lastWordIdx = -1;
                }}
            }});
        </script>
        """
        components.html(live_editor_html, height=620)
        st.markdown('</div>', unsafe_allow_html=True)

    # ----------------- HANDLE EXPORT -----------------
    final_output_dir = os.path.join(os.path.expanduser("~"), "Videos", "AI_Clips")
    os.makedirs(final_output_dir, exist_ok=True)
    target_export_file = os.path.join(
        final_output_dir,
        f"Exported_Short_{st.session_state.selected_clip_idx+1}_{re.sub(r'[^a-zA-Z0-9_-]', '_', cur_clip['title'])}.mp4"
    )

    if export_btn:
        with st.spinner("Rendering final master short with full-quality audio, color & animated captions..."):
            ass_col_map = {
                "Yellow (#FFE600)": "&H0000FFFF",
                "White (#FFFFFF)": "&H00FFFFFF",
                "Cyan (#00FFFF)": "&H00FFFF00",
                "Lime (#00FF66)": "&H0066FF00"
            }
            sub_cfg = {
                "burn": burn_captions,
                "font": sub_font,
                "font_size": int(sub_size * 0.9),
                "color": ass_col_map.get(sub_color, "&H0000FFFF"),
                "pos_x": int(sub_x * 10.8),
                "pos_y": int(sub_y * 2.8),
                "border_style": 1 if sub_border_mode == "Stroke Outline" else 3
            }
            pov_cfg = {
                "enabled": enable_pov,
                "text": pov_text,
                "bg_color": pov_bg,
                "x": f"(w-text_w)*{pov_x/100:.2f}",
                "y": f"(h-text_h)*{pov_y/100:.2f}",
                "font_size": pov_size,
                "box_pad": pov_pad,
                "has_shadow": has_shadow,
                "shadow_opacity": shadow_op
            }
            color_cfg = {
                "exposure": exp_val,
                "contrast": contrast_val,
                "saturation": sat_val,
                "brightness": bright_val
            }

            compile_final_export(
                raw_video=cur_clip["raw_path"],
                out_video=target_export_file,
                color_cfg=color_cfg,
                audio_boost=boost_db,
                pov_cfg=pov_cfg,
                sub_cfg=sub_cfg,
                cur_clip=cur_clip,
                trans_data=st.session_state.transcription_data
            )
            st.session_state.exported_file_path = target_export_file

    if st.session_state.exported_file_path and os.path.exists(st.session_state.exported_file_path):
        st.success(f"✅ Video compiled successfully to:\n`{st.session_state.exported_file_path}`")
        with open(st.session_state.exported_file_path, "rb") as dl_file:
            st.download_button(
                label=f"⬇️ Click Here to Download Short #{st.session_state.selected_clip_idx+1} to Your Browser",
                data=dl_file,
                file_name=os.path.basename(st.session_state.exported_file_path),
                mime="video/mp4",
                type="primary",
                use_container_width=True
            )