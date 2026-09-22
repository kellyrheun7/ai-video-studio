import os
import shutil
import subprocess
import requests
import streamlit as st
from gradio_client import Client, handle_file
from google import genai

# Set authentication credentials
GEMINI_KEY = "AIzaSyB5EKKPp_adHMK1hyhBm74k0EsDnb98jgQ"
HF_TOKEN = "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"
os.environ["HF_TOKEN"] = HF_TOKEN

st.set_page_config(
    page_title="Kling Studio Cloud Lab",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Output directory shared with the editing studio
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "AI_Clips")
CACHE_DIR = os.path.join(OUTPUT_DIR, "gen_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ----------------- SESSION STATE -----------------

if "active_video" not in st.session_state:
    st.session_state.active_video = None
if "current_prompt" not in st.session_state:
    st.session_state.current_prompt = "Cinematic shot of a cybernetic warrior standing in the rain, neon reflections, anamorphic lens flare, photorealistic, 8k"
if "start_frame_path" not in st.session_state:
    st.session_state.start_frame_path = None
if "end_frame_path" not in st.session_state:
    st.session_state.end_frame_path = None

# ----------------- CYBERPUNK / NEON KLING STYLING -----------------

st.markdown("""
<style>
    .stApp {
        background-color: #0b0c10;
        color: #e0e6ed;
    }
    h1, h2, h3 {
        color: #00f3ff !important;
        font-family: 'Segoe UI', sans-serif;
        text-shadow: 0 0 10px rgba(0, 243, 255, 0.4);
    }
    div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div {
        background: rgba(18, 22, 34, 0.65);
        border: 1px solid rgba(0, 243, 255, 0.15);
        border-radius: 10px;
        padding: 12px;
    }
    button[kind="primary"] {
        background: linear-gradient(135deg, #00f3ff 0%, #bd00ff 100%) !important;
        color: #000000 !important;
        font-weight: 800 !important;
        border: none !important;
        border-radius: 8px !important;
        box-shadow: 0 0 15px rgba(0, 243, 255, 0.5) !important;
        transition: all 0.2s ease-in-out;
    }
    button[kind="primary"]:hover {
        transform: scale(1.02);
        box-shadow: 0 0 25px rgba(189, 0, 255, 0.8) !important;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- AI DIRECTORS (GEMINI & DEEPSEEK) -----------------

def polish_prompt_with_deepseek(user_idea):
    endpoint = "https://text.pollinations.ai/"
    system_prompt = (
        "Act as an elite AI video cinematographer for Kling/Wan2.1. "
        "Convert this rough concept into an ultra-detailed camera-directed prompt. "
        "Include: Shot scale, camera movement vectors, lighting, dynamic motion, and lens specs. "
        "Under 55 words. Return ONLY the polished prompt string."
    )
    payload = f"{system_prompt}\n\nConcept: {user_idea}"
    try:
        res = requests.get(f"{endpoint}{payload}", timeout=10)
        if res.status_code == 200 and res.text.strip():
            return res.text.strip()
    except Exception:
        pass
    return f"Cinematic {user_idea}, dynamic tracking camera, volumetric lighting, photorealistic 8k"

def polish_prompt_with_gemini(user_idea):
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        p = (
            "You are a cinematic AI video prompt engineer. Rewrite this simple concept into a professional "
            "Kling-style prompt. Focus on: Camera motion (pan, tilt, zoom), atmospheric lighting, physical motion, "
            "and texture. Keep it under 50 words. Do not include markdown formatting or filler text.\n\n"
            f"Concept: {user_idea}"
        )
        response = client.models.generate_content(model="gemini-2.5-flash", contents=p)
        return response.text.strip()
    except Exception:
        return polish_prompt_with_deepseek(user_idea)

# ----------------- FLUX GENERATOR & FRAME UTILS -----------------

def generate_flux_image(prompt_text, width, height, filename="flux_frame.png"):
    clean_p = requests.utils.quote(prompt_text)
    url = f"https://image.pollinations.ai/prompt/{clean_p}?width={width}&height={height}&nologo=true&model=flux"
    dest = os.path.join(CACHE_DIR, filename)
    resp = requests.get(url, timeout=35)
    if resp.status_code == 200:
        with open(dest, "wb") as f:
            f.write(resp.content)
        return dest
    return None

def extract_last_frame(video_file):
    out_img = os.path.join(CACHE_DIR, "last_frame_extracted.png")
    cmd = [
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", video_file,
        "-vframes", "1", "-q:v", "2", out_img
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_img if os.path.exists(out_img) else None

# ----------------- TOP NAVBAR & HEADER -----------------

st.title("⚡ KLING STUDIO CLOUD LAB")
st.caption("Authenticated Priority ZeroGPU • First/Last Frame Interpolation • Gemini & DeepSeek Co-Pilot")

# ----------------- MAIN LAYOUT -----------------

col_studio, col_viewport = st.columns([1.1, 1], gap="large")

with col_studio:
    st.subheader("1. AI Storyboard Director")

    director_mode = st.radio("AI Prompt Co-Pilot Engine:", ["DeepSeek Reasoning", "Google Gemini 2.5"], horizontal=True)
    user_concept = st.text_input("💡 Quick Concept / Action Hook:", placeholder="e.g., samurai cutting rain droplet in half")

    if st.button("🧠 Direct & Polish Prompt", use_container_width=True):
        if user_concept.strip():
            with st.spinner("AI Director optimizing camera trajectories and light diffusion..."):
                if director_mode == "Google Gemini 2.5":
                    st.session_state.current_prompt = polish_prompt_with_gemini(user_concept)
                else:
                    st.session_state.current_prompt = polish_prompt_with_deepseek(user_concept)
                st.rerun()

    active_prompt = st.text_area("Final Video Diffusion Prompt:", value=st.session_state.current_prompt, height=105)
    negative_prompt = st.text_input("Negative Prompt:", value="blurry, morphing, deformed hands, low quality, static, frame jumps")

    st.markdown("---")
    st.subheader("2. Start Frame & End Frame (Kling Style)")

    f_col1, f_col2 = st.columns(2)

    with f_col1:
        st.markdown("**🎬 Start Frame (Image 1)**")
        uploaded_start = st.file_uploader("Upload Image A", type=["jpg", "png", "jpeg", "webp"], key="up_start")
        if uploaded_start:
            s_save = os.path.join(CACHE_DIR, "uploaded_start.png")
            with open(s_save, "wb") as f:
                f.write(uploaded_start.read())
            st.session_state.start_frame_path = s_save

        if st.button("✨ Gen Start via Flux", key="flux_s_btn"):
            with st.spinner("Synthesizing keyframe via Flux..."):
                st.session_state.start_frame_path = generate_flux_image(active_prompt, 704, 512, "flux_start.png")
                st.rerun()

        if st.session_state.start_frame_path and os.path.exists(st.session_state.start_frame_path):
            st.image(st.session_state.start_frame_path, caption="Active Start Frame", use_container_width=True)
            if st.button("🗑️ Remove Start Frame", key="rm_s"):
                st.session_state.start_frame_path = None
                st.rerun()

    with f_col2:
        st.markdown("**🏁 End Frame (Image 2 - Optional)**")
        uploaded_end = st.file_uploader("Upload Image B (Motion Target)", type=["jpg", "png", "jpeg", "webp"], key="up_end")
        if uploaded_end:
            e_save = os.path.join(CACHE_DIR, "uploaded_end.png")
            with open(e_save, "wb") as f:
                f.write(uploaded_end.read())
            st.session_state.end_frame_path = e_save

        if st.session_state.end_frame_path and os.path.exists(st.session_state.end_frame_path):
            st.image(st.session_state.end_frame_path, caption="Active End Frame", use_container_width=True)
            if st.button("🗑️ Remove End Frame", key="rm_e"):
                st.session_state.end_frame_path = None
                st.rerun()

    st.markdown("---")
    st.subheader("3. Camera Motion & Aspect Ratio")

    s_c1, s_c2 = st.columns(2)
    with s_c1:
        aspect_choice = st.selectbox(
            "Aspect Ratio",
            [
                "16:9 Cinema Landscape (YouTube)",
                "9:16 Vertical (Shorts / Reels / TikTok)",
                "4:3 Classic Retro TV",
                "1:1 Square Feed (Instagram)",
                "21:9 Ultrawide Anamorphic"
            ]
        )
        engine_model = st.selectbox(
            "Diffusion Engine",
            ["Auto-Route (Best Speed/Quality)", "LTX-Video (Lightning Turbo)", "Wan 2.1 (Cinematic Physics)"]
        )

    with s_c2:
        duration_val = st.slider("Duration (Seconds)", min_value=2.0, max_value=8.0, value=3.5, step=0.5)
        camera_motion = st.multiselect("Camera Trajectory Tags", ["Dolly In", "Pan Left", "Tilt Up", "Slow Zoom", "Orbit 360"])

    aspect_resolutions = {
        "16:9 Cinema Landscape (YouTube)": (704, 512),
        "9:16 Vertical (Shorts / Reels / TikTok)": (512, 704),
        "4:3 Classic Retro TV": (640, 480),
        "1:1 Square Feed (Instagram)": (512, 512),
        "21:9 Ultrawide Anamorphic": (768, 384)
    }
    render_w, render_h = aspect_resolutions[aspect_choice]

    st.markdown("---")
    render_btn = st.button("🚀 Render Shot on Cloud GPU", type="primary", use_container_width=True)

# ----------------- VIEWPORT & ACTIONS -----------------

with col_viewport:
    st.subheader("📺 Video Monitor")

    if render_btn:
        with st.spinner("Authenticated on Priority ZeroGPU queue... Rendering frames..."):
            try:
                client = Client("Lightricks/ltx-video-distilled")
                
                final_diffusion_prompt = active_prompt
                if camera_motion:
                    final_diffusion_prompt += f", camera movement: {', '.join(camera_motion)}"

                has_image = bool(st.session_state.start_frame_path and os.path.exists(st.session_state.start_frame_path))
                
                # Dynamic routing based on mode
                if has_image:
                    api_endpoint = "/image_to_video"
                    image_input = handle_file(st.session_state.start_frame_path)
                    predict_kwargs = {
                        "prompt": final_diffusion_prompt,
                        "negative_prompt": negative_prompt,
                        "input_image_filepath": image_input,
                        "input_video_filepath": None,
                        "height_ui": render_h,
                        "width_ui": render_w,
                        "mode": "image-to-video",
                        "duration_ui": duration_val,
                        "ui_frames_to_use": 17,
                        "seed_ui": 42,
                        "api_name": api_endpoint
                    }
                else:
                    api_endpoint = "/text_to_video"
                    predict_kwargs = {
                        "prompt": final_diffusion_prompt,
                        "negative_prompt": negative_prompt,
                        "input_image_filepath": None,
                        "input_video_filepath": None,
                        "height_ui": render_h,
                        "width_ui": render_w,
                        "mode": "text-to-video",
                        "duration_ui": duration_val,
                        "ui_frames_to_use": 17,
                        "seed_ui": 42,
                        "api_name": api_endpoint
                    }

                # Retry block for queue stability
                res = None
                for attempt in range(2):
                    try:
                        res = client.predict(**predict_kwargs)
                        break
                    except Exception as queue_err:
                        if "No GPU was available" in str(queue_err) and attempt == 0:
                            st.warning("Server queue full, retrying priority slot...")
                            continue
                        raise queue_err

                # Output path normalization
                if isinstance(res, dict):
                    v_temp = res.get("video") or res.get("path") or list(res.values())[0]
                elif isinstance(res, (list, tuple)):
                    elem = res[0]
                    v_temp = elem.get("video") or elem.get("path") or list(elem.values())[0] if isinstance(elem, dict) else elem
                else:
                    v_temp = res

                out_filename = f"kling_shot_{render_w}x{render_h}.mp4"
                final_dest = os.path.join(OUTPUT_DIR, out_filename)
                shutil.copyfile(v_temp, final_dest)
                
                st.session_state.active_video = final_dest
                st.success("Render completed successfully!")
            except Exception as err:
                st.error(f"Render engine error: {err}")

    # Active Output Monitor
    if st.session_state.active_video and os.path.exists(st.session_state.active_video):
        st.video(st.session_state.active_video)
        st.caption(f"Saved locally: `{st.session_state.active_video}`")

        act_1, act_2 = st.columns(2)
        with act_1:
            if st.button("🔄 Discard & Retry Shot", use_container_width=True):
                st.session_state.active_video = None
                st.rerun()
        with act_2:
            if st.button("🔗 Extend Video (Next Scene)", type="primary", use_container_width=True):
                with st.spinner("Extracting seamless transition frame..."):
                    last_f = extract_last_frame(st.session_state.active_video)
                    if last_f:
                        st.session_state.start_frame_path = last_f
                        st.info("End frame captured and mapped to Start Frame! Change your prompt and click Render.")
                        st.rerun()

        with open(st.session_state.active_video, "rb") as f_dl:
            st.download_button(
                "⬇️ Download Shot (MP4)",
                data=f_dl,
                file_name=os.path.basename(st.session_state.active_video),
                mime="video/mp4",
                use_container_width=True
            )
    else:
        st.info("No active shot in monitor. Set your prompt/images and click 'Render Shot on Cloud GPU'.")