import os
import io
import cv2
import difflib
import json
import numpy as np
# Switched from pytesseract to easyocr
import easyocr 
import asyncio
import aiohttp
import time
from discord import Embed
import discord
from discord.ext import commands
from brawl_data import map_dict, brawlers_with_emojiid

# -----------------------------
# CONFIG
# -----------------------------

BOT_TOKEN = "MTQ0NDg3MjY4NDMxOTE1MDEzMw.GODWr0.O1cYd9I2480mAsrSJFYOWMD11gWcjsDnlQpyno"
API_URL = "http://127.0.0.1:7001/predict" 

# Target channel ID for automatic analysis
TARGET_CHANNEL_ID = 1446262368362565747 

# File paths
borderless_folder = "borderless" 
portraits_folder = "portraits" # KEPT FOR DEFINITION, BUT REMOVED FROM USAGE BELOW
IS_PICKED_TEMPLATE_PATH = "IsPicked.png"
CONFIG_FILE = "rois_config.json" 
MAP_DATA_FILE = "backend/src/out/brawlers/map_data.json"

# Settings
threshold = 0 
is_picked_threshold = 0.8

# Global Cooldown Setting
IMAGE_ANALYSIS_COOLDOWN_SECONDS = 5
LAST_ANALYSIS_TIME = 0.0 # Global variable to track the last time an analysis was successfully started

# Load ROIs from configuration file
ROI_CONFIGS = {}
try:
    with open(CONFIG_FILE, 'r') as f:
        ROI_CONFIGS = json.load(f)
    print(f"Loaded {len(ROI_CONFIGS)} ROI configurations.")
except FileNotFoundError:
    print(f"ERROR: {CONFIG_FILE} not found. Bot functionality will be limited.")
except json.JSONDecodeError:
    print(f"ERROR: Could not decode JSON from {CONFIG_FILE}. Check file integrity.")

# Load Map Data for Images
MAP_DATA = {}
try:
    with open(MAP_DATA_FILE, 'r', encoding='utf-8') as f:
        MAP_DATA = json.load(f)
    print(f"Loaded details for {len(MAP_DATA)} maps.")
except FileNotFoundError:
    print(f"WARNING: {MAP_DATA_FILE} not found. Map images will not display.")
except Exception as e:
    print(f"WARNING: Error loading map data: {e}")

# Draft Data
team_positions = ["a1", "a2", "a3"]
enemy_positions = ["b1", "b2", "b3"]
draft_order = ["a1", "b1", "b2", "a2", "a3", "b3"]
draft_ordernotfirst = ["b1", "a1", "a2", "b2", "b3", "a3"]

is_picked_template = None
if os.path.exists(IS_PICKED_TEMPLATE_PATH):
    is_picked_template = cv2.imread(IS_PICKED_TEMPLATE_PATH)

# Custom Brawler Name and Emoji Mapping
BRAWLER_EMOJI_MAP = {
    brawler["name"].lower(): f'<:{brawler["id"]}:{brawler["emojiid"]}> {brawler["name"].capitalize()}'
    for brawler in brawlers_with_emojiid
}
BRAWLER_EMOJI_MAP["unknown"] = "Unknown" 
# Fix for R-T capitalization
r_t_info = next((b for b in brawlers_with_emojiid if b["name"] == "R-T"), None)
if r_t_info:
    BRAWLER_EMOJI_MAP["r-t"] = f'<:{r_t_info["id"]}:{r_t_info["emojiid"]}> R-T'

# Initialize EasyOCR reader once
try:
    # Set languages to English ('en') and disable GPU usage if not available
    reader = easyocr.Reader(['en'], gpu=False) 
    print("EasyOCR reader initialized successfully.")
except Exception as e:
    print(f"ERROR: Could not initialize EasyOCR: {e}")
    reader = None

# -----------------------------
# Helper Functions
# -----------------------------
def crop_portrait(img):
    h, w = img.shape[:2]
    x1 = int(w * 0.26)
    y1 = 0
    x2 = w
    y2 = int(h * 0.85)
    return img[y1:y2, x1:x2]

def find_best_match(roi_image, folders, threshold=0):
    roi_h, roi_w = roi_image.shape[:2]
    roi_hsv = cv2.cvtColor(roi_image, cv2.COLOR_BGR2HSV)
    roi_hist = cv2.calcHist([roi_hsv], [0,1,2], None, [8,8,8], [0,180,0,256,0,256])
    cv2.normalize(roi_hist, roi_hist)
    best_match = None
    best_score = -1

    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for file in os.listdir(folder):
            portrait_path = os.path.join(folder, file)
            img = cv2.imread(portrait_path)
            if img is None: continue

            img_cropped = crop_portrait(img)
            try:
                img_resized = cv2.resize(img_cropped, (roi_w, roi_h))
            except: continue

            img_hsv = cv2.cvtColor(img_resized, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([img_hsv], [0,1,2], None, [8,8,8], [0,180,0,256,0,256])
            cv2.normalize(hist, hist)
            score = cv2.compareHist(roi_hist, hist, cv2.HISTCMP_CORREL)

            if score > best_score:
                best_score = score
                best_match = file

    if best_score < threshold or best_match is None:
        return "Unknown", None
    return best_match, None

def get_brawler_name_from_file(file_name):
    if file_name.lower() == "notpicked.png": return "Unknown"
    if file_name in ["Unknown", "Not picked yet"]: return "Unknown"
    try:
        brawler_id = int(os.path.splitext(file_name)[0])
        for b in brawlers_with_emojiid: 
            if b["id"] == brawler_id: return b["name"].lower()
    except: return "Unknown"
    return "Unknown"

def format_brawler_with_emoji(brawler_name_lower):
    """Returns the custom emoji string + capitalized name for a brawler."""
    return BRAWLER_EMOJI_MAP.get(brawler_name_lower, "Unknown")
    
def get_map_name(img, roi):
    if reader is None:
        print("EasyOCR reader not initialized.")
        return "Unknown"

    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    crop = img[y:y+h, x:x+w]

    # Text Scaling Logic (Upscale for better OCR accuracy)
    scale_factor = 3
    scaled_crop = cv2.resize(crop, (w * scale_factor, h * scale_factor), interpolation=cv2.INTER_CUBIC)

    # Optional pre-processing
    gray_scaled = cv2.cvtColor(scaled_crop, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray_scaled, 3)

    # Use EasyOCR to read text
    result = reader.readtext(denoised, detail=0, paragraph=True) 
    
    if result:
        text = " ".join(result).strip().replace("\n", " ")
        print(f"OCR Raw Text: '{text}'")
        return text
    
    return "Unknown"

def closest_map_name(ocr_text):
    if not ocr_text: return "Unknown"
    matches = difflib.get_close_matches(ocr_text, map_dict.keys(), n=1, cutoff=0.4)
    return matches[0] if matches else "Unknown"

def build_sequence(order, brawler_dict):
    return [brawler_dict.get(pos, "Unknown") for pos in order]

def has_middle_gap(seq):
    seen_known = False
    seen_unknown_after_known = False
    for item in seq:
        if item != "Unknown":
            if seen_unknown_after_known: return True
            seen_known = True
        else:
            if seen_known: seen_unknown_after_known = True
    return False

async def download_image_as_cv2(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200: return None
            data = await resp.read()
    arr = np.frombuffer(data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

def find_closest_config(image_w, image_h):
    """Finds the closest resolution config to the input image's dimensions."""
    best_match = None
    min_diff = float('inf')

    if image_h == 0: return None
    
    for key, config in ROI_CONFIGS.items():
        config_w = config["FORCE_WIDTH"]
        config_h = config["FORCE_HEIGHT"]
        
        if config_h == 0: continue
        
        # Calculate a combined metric (Euclidean distance)
        res_diff = np.sqrt((image_w - config_w)**2 + (image_h - config_h)**2)
        diff_metric = res_diff
        
        if diff_metric < min_diff:
            min_diff = diff_metric
            best_match = config
            
    if best_match:
        print(f"Closest config found: {best_match['FORCE_WIDTH']}x{best_match['FORCE_HEIGHT']}")
    else:
        print("ERROR: No suitable ROI config found.")

    return best_match

def analyze_image_cv2(image):
    if image is None: raise ValueError("Image is None")

    # 1. Determine Image Size and Find Closest Config
    original_h, original_w = image.shape[:2]
    config = find_closest_config(original_w, original_h)
    
    if config is None:
        raise ValueError("Could not load or find a suitable ROI configuration.")

    # 2. Get target resolution and ROIs from the config
    FORCE_WIDTH = config["FORCE_WIDTH"]
    FORCE_HEIGHT = config["FORCE_HEIGHT"]
    rois = config["rois"]
    roisCheckPick = config["roisCheckPick"]
    map_roi = config["map_roi"]

    # 3. Scale the input image to the target resolution
    image = cv2.resize(image, (FORCE_WIDTH, FORCE_HEIGHT))
    
    # 4. Map Name OCR (using EasyOCR)
    ocr_map_name = get_map_name(image, map_roi)
    official_map_name = closest_map_name(ocr_map_name)

    team_picks = []
    enemy_picks = []
    
    # --- MODIFICATION APPLIED HERE ---
    folders_to_use = [borderless_folder] 
    # ---------------------------------

    global is_picked_template
    if is_picked_template is None and os.path.exists(IS_PICKED_TEMPLATE_PATH):
        is_picked_template = cv2.imread(IS_PICKED_TEMPLATE_PATH)

    # 5. Process Picks
    for name, info in rois.items():
        x, y, w, h = info["x"], info["y"], info["w"], info["h"]
        roi_image = image[y:y+h, x:x+w]
        match_file, _ = find_best_match(roi_image, folders_to_use, threshold)
        brawler_name = get_brawler_name_from_file(match_file) # Returns lowercase name

        # Check for green "Picked" indicator
        if name in roisCheckPick and is_picked_template is not None:
            chk = roisCheckPick[name]
            cx, cy, cw, ch = chk["x"], chk["y"], chk["w"], chk["h"]
            roi_check = image[cy:cy+ch, cx:cx+cw]
            try:
                chk_h, chk_w = roi_check.shape[:2]
                is_picked_resized = cv2.resize(is_picked_template, (chk_w, chk_h))
                hsv_roi = cv2.cvtColor(roi_check, cv2.COLOR_BGR2HSV)
                hsv_pick = cv2.cvtColor(is_picked_resized, cv2.COLOR_BGR2HSV)
                hist_roi = cv2.calcHist([hsv_roi], [0,1,2], None, [8,8,8], [0,180,0,256,0,256])
                hist_pick = cv2.calcHist([hsv_pick], [0,1,2], None, [8,8,8], [0,180,0,256,0,256])
                cv2.normalize(hist_roi, hist_roi)
                cv2.normalize(hist_pick, hist_pick)
                if cv2.compareHist(hist_roi, hist_pick, cv2.HISTCMP_CORREL) > is_picked_threshold:
                    brawler_name = "Unknown"
            except Exception as e: 
                pass

        if "TeamPick" in name: team_picks.append(brawler_name)
        else: enemy_picks.append(brawler_name)

    # Sequence reconstruction logic (uses lowercase names)
    brawler_dict = {}
    for pos, pick in zip(team_positions, team_picks):
        if pick != "Unknown": brawler_dict[pos] = pick
    for pos, pick in zip(enemy_positions, enemy_picks):
        if pick != "Unknown": brawler_dict[pos] = pick

    seq_first = build_sequence(draft_order, brawler_dict)
    seq_second = build_sequence(draft_ordernotfirst, brawler_dict)
    gap_first = has_middle_gap(seq_first)
    gap_second = has_middle_gap(seq_second)
    unknown_first = seq_first.count("Unknown")
    unknown_second = seq_second.count("Unknown")

    if gap_first and not gap_second:
        chosen_seq = seq_second
        first_pick_value = False
    elif gap_second and not gap_first:
        chosen_seq = seq_first
        first_pick_value = True
    else:
        if unknown_second < unknown_first:
            chosen_seq = seq_second
            first_pick_value = False
        else:
            chosen_seq = seq_first
            first_pick_value = True

    return {
        "map_ocr": ocr_map_name,
        "map_official": official_map_name,
        "team_picks": team_picks, 
        "enemy_picks": enemy_picks, 
        "chosen_sequence": chosen_seq, 
        "first_pick_value": first_pick_value
    }

# -----------------------------
# ASYNC API
# -----------------------------
async def post_prediction(session, payload):
    try:
        async with session.post(API_URL, json=payload, timeout=10) as resp:
            return await resp.json()
    except Exception as e:
        print(f"Prediction API Error: {e}")
        return None

async def post_pickrate(session, map_name):
    pickrate_url = API_URL.replace('/predict', '/pickrate')
    try:
        async with session.post(pickrate_url, json={"map": map_name}, timeout=5) as resp:
            return await resp.json()
    except Exception as e:
        print(f"Pickrate API Error: {e}")
        return None

# -----------------------------
# Discord Bot
# -----------------------------
intents = discord.Intents.default()
# Must enable message content intent to read attachments from non-command messages
intents.message_content = True 
bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready. Logged in as {bot.user} ({bot.user.id})")
    try:
        # Syncing only needed if you use slash commands
        await bot.tree.sync()
    except Exception as e: 
        print(f"Error syncing commands: {e}")

# --- Unified Analysis and Response Function ---
async def analyze_draft_image(context, img_url):
    """Handles image analysis, API calls, and embedding for both commands and messages."""
    global LAST_ANALYSIS_TIME
    
    is_interaction = isinstance(context, discord.Interaction)
    
    # 1. Handle Cooldown for non-command messages (i.e., on_message trigger)
    if not is_interaction:
        time_now = time.time()
        if time_now - LAST_ANALYSIS_TIME < IMAGE_ANALYSIS_COOLDOWN_SECONDS:
            remaining = IMAGE_ANALYSIS_COOLDOWN_SECONDS - (time_now - LAST_ANALYSIS_TIME)
            print(f"Cooldown active. Ignoring image. Remaining: {remaining:.2f}s")
            # Optionally send a message about the cooldown
            # await context.channel.send(f"Analysis is on cooldown. Please wait {remaining:.1f} seconds.", delete_after=5)
            return
        
        # Update last analysis time *before* starting the intensive work
        LAST_ANALYSIS_TIME = time_now

    if is_interaction:
        await context.response.defer(thinking=True, ephemeral=False)
    else:
        # For on_message, send a typing indicator
        await context.channel.typing()

    # 2. Download
    try:
        img = await download_image_as_cv2(img_url)
        if img is None:
            response_text = "Failed to download image."
            if is_interaction:
                await context.followup.send(response_text)
            else:
                await context.channel.send(response_text)
            return
    except Exception as e:
        response_text = f"Error downloading: {e}"
        if is_interaction:
            await context.followup.send(response_text)
        else:
            await context.channel.send(response_text)
        return

    # 3. Analyze
    t_start = time.perf_counter()
    try:
        analysis = analyze_image_cv2(img)
    except Exception as e:
        response_text = f"Error analyzing: {e}"
        if is_interaction:
            await context.followup.send(response_text)
        else:
            await context.channel.send(response_text)
        return
    print(f"[TIMING] CV2 Analysis: {time.perf_counter() - t_start:.4f}s")

    # 4. Async API (Parallel)
    t_api_start = time.perf_counter()
    payload = {
        "map": analysis["map_official"],
        "brawlers": analysis["chosen_sequence"],
        "first_pick": analysis["first_pick_value"]
    }

    async with aiohttp.ClientSession() as session:
        task_pred = asyncio.create_task(post_prediction(session, payload))
        task_pick = asyncio.create_task(post_pickrate(session, analysis["map_official"]))
        api_json, pickrate_json = await asyncio.gather(task_pred, task_pick)
    
    print(f"[TIMING] Parallel API: {time.perf_counter() - t_api_start:.4f}s")

    # 5. Process Recommendations
    recommendations = []
    if api_json and "probabilities" in api_json and isinstance(api_json["probabilities"], dict):
        probs = api_json["probabilities"]
        probs_lower = {k.lower(): v for k, v in probs.items()}
        taken = set([b.lower() for b in analysis["chosen_sequence"] if b != "Unknown"])
        sorted_probs = sorted(probs_lower.items(), key=lambda kv: kv[1], reverse=True)
        for name, score in sorted_probs:
            if name in taken: continue
            recommendations.append((name, float(score)))
            if len(recommendations) >= 5: break

    # 6. Process Pickrates
    pickrates_text = "Unavailable"
    if pickrate_json and pickrate_json.get("pickrate"):
        pickrates = pickrate_json["pickrate"]
        taken = set([b.lower() for b in analysis["chosen_sequence"] if b != "Unknown"])
        filtered_pickrates = {k: v for k, v in pickrates.items() if k.lower() not in taken}
        sorted_pickrates = sorted(filtered_pickrates.items(), key=lambda kv: kv[1], reverse=True)
        pickrates_text = "\n".join(f"{i+1}. {format_brawler_with_emoji(b.lower()).replace(' ', '')} — {v:.2f}" for i, (b, v) in enumerate(sorted_pickrates[:5]))

    # 7. Build Embed
    embed = Embed(title="<:rd:1444805398321168565> Draft Analysis", color=0x1abc9c)
    
    map_name = analysis["map_official"] or "Unknown"
    first_pick_val = str(analysis["first_pick_value"])

    # Set Map Thumbnail
    if map_name in MAP_DATA:
        map_info = MAP_DATA[map_name]
        if "img_url" in map_info:
            embed.set_thumbnail(url=map_info["img_url"])
    
    embed.add_field(name="<:3v3:1434627404273549342>Map:", value=map_name, inline=True)
    embed.add_field(name="<:cr:1444812859992445119>First Pick:", value=first_pick_val, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Format brawler names with emojis and capitalization
    team_text = "\n".join(f"{i+1}. {format_brawler_with_emoji(p)}" for i,p in enumerate(analysis["team_picks"])) if analysis["team_picks"] else "None"
    enemy_text = "\n".join(f"{i+1}. {format_brawler_with_emoji(p)}" for i,p in enumerate(analysis["enemy_picks"])) if analysis["enemy_picks"] else "None"
    
    embed.add_field(name="<:te:1444811734069280991>Team Picks:", value=team_text, inline=True)
    embed.add_field(name="<:em:1444811763462836224>Enemy Picks:", value=enemy_text, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    if recommendations:
        rec_text = "\n".join(f"{i+1}. {format_brawler_with_emoji(name)} — **{score:.2f}**" for i, (name, score) in enumerate(recommendations))
    else:
        rec_text = "No recommendations."
    embed.add_field(name="<:ws:1444431287980003449>Top 5 Recommendations:", value=rec_text, inline=False)
    embed.add_field(name="<:pl:1434701133980631060>Top 5 Pickrates:", value=pickrates_text, inline=False)
    
    # 8. Send Embed
    if is_interaction:
        await context.followup.send(embed=embed)
    else:
        await context.channel.send(embed=embed)

# --- New on_message handler for automatic analysis in the target channel ---
@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Check if the message is in the target channel AND has an image attachment
    if message.channel.id == TARGET_CHANNEL_ID and message.attachments:
        attachment = message.attachments[0]
        # Basic check for image content type
        if attachment.content_type and attachment.content_type.startswith('image'):
            print(f"Image received in target channel {TARGET_CHANNEL_ID}. URL: {attachment.url}")
            # Use the message object as the context for the unified analysis function
            await analyze_draft_image(message, attachment.url)
            return # Stop processing after handling the image

    # Process any other commands (like slash commands)
    await bot.process_commands(message)

# --- Slash command to manually trigger analysis (Calls unified logic) ---
@bot.tree.command(name="draft", description="Analyze draft image and return picks + top 5 recommendations")
@discord.app_commands.describe(image="Attach an image or paste an image URL")
async def draft_command(interaction: discord.Interaction, image: discord.Attachment = None):
    print("Received slash command /draft")
    
    img_url = None
    if image:
        img_url = image.url

    if not img_url:
        # Fallback to check message attachments if image parameter was not used directly
        if interaction.message and interaction.message.attachments:
            img_url = interaction.message.attachments[0].url
        
    if not img_url:
        await interaction.response.send_message("No image found. Please attach an image or provide a URL.", ephemeral=True)
        return
    
    # Slash commands do not respect the global cooldown (they use Discord's built-in cooldowns)
    await analyze_draft_image(interaction, img_url)
    
if __name__ == "__main__":
    bot.run(BOT_TOKEN)