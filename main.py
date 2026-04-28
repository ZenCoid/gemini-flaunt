"""
FLAUNT.FIT - AI Stylist Backend
"Don't Rate. Match." Philosophy
"""

import os
import json
import re
import uuid
import base64
import logging
import asyncio
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from PIL import Image, ExifTags
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from supabase import create_client, Client

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flaunt")

# Environment validation
def validate_env():
    required = ["SUPABASE_URL", "SUPABASE_KEY"]
    optional = ["OPENROUTER_API_KEY", "GROQ_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing required: {missing}")
    # At least one AI key needed
    if not os.getenv("OPENROUTER_API_KEY") and not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError("Need at least one: OPENROUTER_API_KEY or GROQ_API_KEY")

try:
    validate_env()
except EnvironmentError as e:
    logger.warning(f"Environment warning: {e}")

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")  # FREE alternative

# Fallback model list - using Groq (FREE) + OpenRouter as backup
# Groq is 100% free with Llama 3.2 Vision support
GROQ_MODELS = [
    "llama-3.2-11b-vision-preview",  # FREE on Groq - supports images!
]

OPENROUTER_MODELS = [
    "google/gemini-2.5-flash-lite",   # $0.40/M if you add credits later
    "qwen/qwen3.5-flash-02-23",       # $0.26/M
]

# Initialize Supabase client
db: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        db = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase connected successfully")
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")

# HTTP client for OpenRouter
http_client = httpx.AsyncClient(timeout=60.0)


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await http_client.aclose()


app = FastAPI(
    title="FLAUNT.FIT API",
    description="AI Stylist - Don't Rate. Match.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


# ============ MODELS ============

class AnalysisResult(BaseModel):
    status: str
    occasion_match: int  # Percentage 0-100
    color_harmony: str
    formality_calibration: str
    the_fix: str
    items_spotted: list[str]
    vibe_check: str
    confidence: str  # "High", "Medium", "Low"
    roast: Optional[str] = None  # ROAST MODE - savage fashion critique
    image_url: Optional[str] = None
    fit_id: Optional[str] = None


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str


class ChatMessage(BaseModel):
    message: str
    context: Optional[dict] = None


class CalendarPlan(BaseModel):
    date: str
    occasion: str
    fit_id: Optional[str] = None
    notes: Optional[str] = None


class QuizAnswer(BaseModel):
    question_id: str
    answer: str


class QuizResult(BaseModel):
    answers: List[QuizAnswer]


# ============ UTILITIES ============

def fix_image_orientation(img: Image.Image) -> Image.Image:
    """Fix EXIF orientation for mobile photos."""
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if tag in ExifTags.TAGS and ExifTags.TAGS[tag] == 'Orientation':
                    if value == 3: img = img.rotate(180, expand=True)
                    elif value == 6: img = img.rotate(270, expand=True)
                    elif value == 8: img = img.rotate(90, expand=True)
                    break
    except Exception:
        pass
    return img


def extract_json_from_response(text: str) -> Optional[dict]:
    """Safely extract JSON from AI response."""
    try:
        # Try direct parse first
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON block
    try:
        # Remove markdown code blocks
        cleaned = re.sub(r'```(?:json)?\s*', '', text)
        cleaned = re.sub(r'```\s*', '', cleaned)
        
        # Find JSON object
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    
    return None


# ============ TODO #1: SUPABASE STORAGE UPLOAD WITH UPSERT ============

async def upload_to_supabase(file_bytes: bytes, file_path: str, content_type: str = "image/jpeg") -> Optional[str]:
    """
    Upload file to Supabase storage with upsert (overwrite if exists).
    Returns public URL or None on failure.
    """
    if not db:
        logger.warning("Database not configured for storage upload")
        return None
    
    try:
        # Try upload with upsert=True to handle duplicates
        db.storage.from_("outfits").upload(
            file_path, 
            file_bytes, 
            {"content-type": content_type},
            upsert=True  # This fixes the duplicate key error
        )
        image_url = db.storage.from_("outfits").get_public_url(file_path)
        logger.info(f"Successfully uploaded to Supabase: {file_path}")
        return image_url
    except Exception as e:
        logger.error(f"Supabase upload failed: {e}")
        # Try without upsert as fallback (older supabase versions)
        try:
            db.storage.from_("outfits").upload(
                file_path, 
                file_bytes, 
                {"content-type": content_type}
            )
            return db.storage.from_("outfits").get_public_url(file_path)
        except Exception as e2:
            logger.error(f"Supabase upload fallback also failed: {e2}")
            return None


def build_analysis_prompt(occasion: str, vibe_goal: Optional[str] = None, is_roast: bool = False) -> str:
    """Build the 'Don't Rate, Match' prompt with dynamic personality."""
    
    vibe_section = ""
    if vibe_goal:
        vibe_section = f"""
    User's Desired Vibe: "{vibe_goal}"
    IMPORTANT: Judge whether this outfit achieves their desired vibe. Be specific about what's working or missing.
    """
    
    # DYNAMIC PERSONALITY
    if is_roast:
        personality = "a Gen Z brain-rotted fashion roaster"
        voice_rules = """VOICE RULES (BRAIN ROT MODE):
- You are a CHAOTIC Gen Z roaster. Brain rot energy. Talk like a instagram comment section.
- Heavy slang: fr, nah, dead, cooked, mid, ate, slay, NPC, no cap, on god, bussin, mid, tragic, caught in 4k, giving, screaming, crying, throwing up
- Be dramatic and unhinged. Over-the-top reactions.
- Reference brain rot culture:Ohio, skibidi energy, NPC behavior, main character, side character, 2016 vibes, discord mod energy, dark humour
- Roast patterns:
  * "nahhh bro is actually cooked 💀 [roast]"
  * "this fit giving [embarrassing thing] energy fr fr"
  * "not the [item] screaming for help 😭"
  * "on god this is the most [adjective] thing I've seen today"
  * "bro really woke up and chose this fit... bold choice"
- Be SPECIFIC about items. Make it sting to the user but funny for sier's friends who see the reply adn laugh hysterically.
- Examples:
  * "nahhh this shirt is GIVING discord mod energy fr. bro really said 'my mom bought this' and thought it was a flex 💀"
  * "the pants are screaming crying throwing up rn. those joints have been fighting for their life since 2019 no cap 😭"
  * "this whole fit is giving 'I let my grandma dress me and she gave up halfway.' tragic. cooked. done."
  * "bro's shoes are main character energy... main character of a movie nobody asked for. rest in peace to this outfit 💀"
  * "on god this jacket has seen things. things we don't speak about. throw the whole fit away and start over bestie."
- Keep it SHORT. One roast that hits. Make their friends send it in the group chat."""
        roast_json = ',\n    "roast": "<short, brutal, funny roast. Simple words. Make friends laugh.>"'
    else:
        personality = "a senior high-fashion consultant"
        voice_rules = """VOICE RULES (STYLIST MODE):
- Write like you're texting a friend back. Casual, direct, personal.
- NEVER say "Consider adding..." or "You might want to..." - that sounds robotic.
- Instead say: "Honestly, this jacket is carrying the whole fit" or "Those shoes are fighting with the pants".
- Never be cruel. Honest, not mean."""
        roast_json = ''

    return f"""Act as {personality}. Analyze this {occasion} outfit.
{vibe_section}

{voice_rules}

Return ONLY valid JSON (no markdown, no extra text):
{{
    "occasion_match": <integer 0-100>,
    "color_harmony": "<brief color analysis>",
    "formality_calibration": "<overdressed/underdressed/nailed it, one sentence>",
    "the_fix": "<ONE specific fix or 'Lock it in.'>",
    "items_spotted": ["<list actual items in photo>"],
    "vibe_check": "<1-2 sentence honest reaction>",
    "confidence": "<'High', 'Medium', or 'Low'>"{roast_json}
}}"""


# ============ ROUTES ============

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    """Serve the main application."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="index.html not found")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "groq": "configured" if GROQ_KEY else "not set",
        "openrouter": "configured" if OPENROUTER_KEY else "not set",
        "database": "connected" if db else "disconnected"
    }


@app.post("/analyze-fit/", response_model=AnalysisResult)
async def analyze_fit(
    file: UploadFile = File(...),
    occasion: str = Form(...),
    vibe_goal: Optional[str] = Form(None),
    roast_mode: str = Form("false")
):
    """
    Analyze an outfit photo against an occasion.
    
    This is the core "Event Context Engine" - it matches outfits to contexts,
    not arbitrary scores. roast_mode toggles between savage critic and nice stylist.
    """
    
    # Validate inputs
    if not occasion or len(occasion.strip()) == 0:
        raise HTTPException(status_code=400, detail="Occasion is required")
    
    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/jpg"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )
    
    # Check file size (max 10MB)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB")
    
    try:
        # Process image
        img = Image.open(BytesIO(content))
        img = fix_image_orientation(img)
        
        # Resize for API efficiency
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        
        # Convert to JPEG for consistency
        output = BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format="JPEG", quality=85)
        output.seek(0)
        
        # Upload to Supabase storage using the new upsert function
        image_url = None
        if db:
            file_path = f"{uuid.uuid4()}.jpg"
            image_url = await upload_to_supabase(
                output.getvalue(), 
                file_path, 
                "image/jpeg"
            )
        
        # Prepare for OpenRouter - check roast mode
        is_roast = roast_mode.lower() == "true"
        b64_image = base64.b64encode(output.getvalue()).decode('utf-8')
        prompt = build_analysis_prompt(occasion.strip(), vibe_goal.strip() if vibe_goal else None, is_roast=is_roast)
        
        # Try Groq first (FREE), then OpenRouter as fallback
        last_error = None
        response = None
        
        # === TRY GROQ (FREE) ===
        if GROQ_KEY:
            for model in GROQ_MODELS:
                try:
                    logger.info(f"Trying Groq (FREE): {model}")
                    
                    response = await http_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {GROQ_KEY}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": model,
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{b64_image}"
                                        }
                                    }
                                ]
                            }],
                            "temperature": 0.8,
                            "max_tokens": 1000
                        }
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Groq success with {model}")
                        break
                    elif response.status_code in [429, 404, 401]:
                        logger.warning(f"Groq {model} failed ({response.status_code}). Trying next...")
                        last_error = f"Groq error: {response.status_code}"
                        response = None
                        continue
                        
                except Exception as e:
                    logger.warning(f"Groq error: {e}")
                    last_error = str(e)
                    response = None
                    continue
        
        # === TRY OPENROUTER (PAID) AS FALLBACK ===
        if not response or response.status_code != 200:
            if OPENROUTER_KEY:
                for model in OPENROUTER_MODELS:
                    try:
                        logger.info(f"Trying OpenRouter: {model}")
                        
                        response = await http_client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {OPENROUTER_KEY}",
                                "HTTP-Referer": "https://flaunt.fit",
                                "X-Title": "FLAUNT.FIT"
                            },
                            json={
                                "model": model,
                                "messages": [{
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": prompt},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:image/jpeg;base64,{b64_image}"
                                            }
                                        }
                                    ]
                                }],
                                "temperature": 0.8,
                                "max_tokens": 1000
                            }
                        )
                        
                        if response.status_code == 200:
                            logger.info(f"OpenRouter success with {model}")
                            break
                        elif response.status_code in [429, 404]:
                            logger.warning(f"OpenRouter {model} failed ({response.status_code}). Trying next...")
                            last_error = f"OpenRouter error: {response.status_code}"
                            response = None
                            continue
                            
                    except Exception as e:
                        logger.warning(f"OpenRouter error: {e}")
                        last_error = str(e)
                        response = None
                        continue
        
        # === ALL FAILED ===
        if not response or response.status_code != 200:
            logger.error(f"All AI providers failed. Last error: {last_error}")
            raise HTTPException(
                status_code=503,
                detail="AI is busy. Add GROQ_API_KEY (free) to .env for unlimited access!"
            )
        
        resp_data = response.json()
        
        if "error" in resp_data:
            error_msg = resp_data["error"].get("message", str(resp_data["error"]))
            logger.error(f"AI API error: {error_msg}")
            raise HTTPException(status_code=502, detail=f"AI error: {error_msg}")
        
        if "choices" not in resp_data or not resp_data["choices"]:
            logger.error(f"Unexpected response: {resp_data}")
            raise HTTPException(status_code=502, detail="AI gave weird response. Try again.")
        
        # Extract and parse AI response
        ai_content = resp_data["choices"][0]["message"]["content"]
        ai_data = extract_json_from_response(ai_content)
        
        if not ai_data:
            logger.error(f"Failed to parse AI response: {ai_content[:500]}")
            raise HTTPException(
                status_code=502, 
                detail="AI fumbled the response. Give it another shot."
            )
        
        # Validate required fields
        required_fields = ["occasion_match", "color_harmony", "formality_calibration", 
                          "the_fix", "items_spotted", "vibe_check", "confidence"]
        missing = [f for f in required_fields if f not in ai_data]
        if missing:
            logger.error(f"Missing fields in AI response: {missing}")
            raise HTTPException(
                status_code=502, 
                detail=f"AI response incomplete. Missing: {missing}"
            )
        
        # Ensure occasion_match is in valid range
        try:
            occasion_match = int(ai_data.get("occasion_match", 0))
            occasion_match = max(0, min(100, occasion_match))  # Clamp to 0-100
        except (ValueError, TypeError):
            occasion_match = 50
        
        # Ensure items_spotted is a list
        items = ai_data.get("items_spotted", [])
        if not isinstance(items, list):
            items = [str(items)] if items else []
        items = [str(item) for item in items[:10]]  # Limit to 10 items
        
        # Save to database
        fit_id = None
        if db:
            try:
                result = db.table("fits").insert({
                    "occasion": occasion.strip(),
                    "occasion_match": occasion_match,
                    "color_harmony": ai_data.get("color_harmony", ""),
                    "formality_calibration": ai_data.get("formality_calibration", ""),
                    "the_fix": ai_data.get("the_fix", ""),
                    "items_spotted": items,
                    "vibe_check": ai_data.get("vibe_check", ""),
                    "confidence": ai_data.get("confidence", "Medium"),
                    "vibe_goal": vibe_goal.strip() if vibe_goal else None,
                    "roast_text": ai_data.get("roast", ""),  # SAVE THE ROAST
                    "image_url": image_url,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
                
                if result.data:
                    fit_id = result.data[0].get("id")
            except Exception as e:
                logger.warning(f"Database insert failed: {e}")
        
        return AnalysisResult(
            status="success",
            occasion_match=occasion_match,
            color_harmony=str(ai_data.get("color_harmony", "")),
            formality_calibration=str(ai_data.get("formality_calibration", "")),
            the_fix=str(ai_data.get("the_fix", "")),
            items_spotted=items,
            vibe_check=str(ai_data.get("vibe_check", "")),
            confidence=str(ai_data.get("confidence", "Medium")),
            roast=ai_data.get("roast"),  # RETURN THE ROAST
            image_url=image_url,
            fit_id=fit_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.get("/history")
async def get_history(limit: int = 20):
    """Get analysis history."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        result = db.table("fits").select("*").order("created_at", desc=True).limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"History fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")


@app.get("/profile-dna")
async def get_profile_dna():
    """Get user's style DNA based on their history."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        fits = db.table("fits").select("occasion, occasion_match, vibe_goal").execute().data
        
        if not fits:
            return {
                "status": "success",
                "dna": "Fresh Face",
                "level": "Newcomer",
                "avg_match": 0,
                "total_fits": 0,
                "top_occasion": None,
                "insight": "Upload your first fit to discover your style DNA!"
            }
        
        # Calculate stats
        avg_match = round(sum(f.get("occasion_match", 0) for f in fits) / len(fits), 1)
        
        # Find most common occasion
        occasions = [f.get("occasion") for f in fits if f.get("occasion")]
        top_occasion = max(set(occasions), key=occasions.count) if occasions else None
        
        # Determine level - Gen Z style
        if avg_match >= 85:
            level = "Icon"
        elif avg_match >= 70:
            level = "Trendsetter"
        elif avg_match >= 50:
            level = "Curator"
        else:
            level = "Explorer"
        
        # Generate DNA title
        if top_occasion:
            dna = f"{top_occasion} Pro"
        else:
            dna = "Style Chameleon"
        
        # Generate insight - keep it real
        if avg_match >= 80:
            insight = "You're eating. Your occasion matching is elite. Trust your instincts."
        elif avg_match >= 60:
            insight = "Solid instincts. Fine-tune with accessories and fit. You're getting there."
        else:
            insight = "Room to grow. Focus on occasion-appropriate pieces. We believe in you."
        
        return {
            "status": "success",
            "dna": dna,
            "level": level,
            "avg_match": avg_match,
            "total_fits": len(fits),
            "top_occasion": top_occasion,
            "insight": insight
        }
        
    except Exception as e:
        logger.error(f"Profile DNA failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate profile DNA")


@app.post("/add-to-closet/")
async def add_to_closet(
    item_name: str = Form(...),
    image_url: str = Form(...),
    category: Optional[str] = Form(None),
    color: Optional[str] = Form(None)
):
    """Add an item to user's virtual closet."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    # Validate inputs
    item_name = item_name.strip()
    if not item_name or len(item_name) > 100:
        raise HTTPException(status_code=400, detail="Item name must be 1-100 characters")
    
    try:
        result = db.table("wardrobe").insert({
            "item_name": item_name,
            "image_url": image_url.strip(),
            "category": category.strip() if category else None,
            "color": color.strip() if color else None,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        return {"status": "success", "data": result.data[0] if result.data else None}
        
    except Exception as e:
        logger.error(f"Closet add failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to add to closet")


@app.get("/closet")
async def get_closet(limit: int = 50):
    """Get user's closet items."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        result = db.table("wardrobe").select("*").order("created_at", desc=True).limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"Closet fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch closet")


@app.delete("/closet/{item_id}")
async def remove_from_closet(item_id: str):
    """Remove an item from closet."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        db.table("wardrobe").delete().eq("id", item_id).execute()
        return {"status": "success", "message": "Item removed"}
    except Exception as e:
        logger.error(f"Closet remove failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove item")


@app.put("/closet/{item_id}")
async def update_closet_item(
    item_id: str,
    item_name: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    color: Optional[str] = Form(None)
):
    """Update a closet item."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        update_data = {}
        if item_name:
            update_data["item_name"] = item_name.strip()
        if category:
            update_data["category"] = category.strip()
        if color:
            update_data["color"] = color.strip()
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        result = db.table("wardrobe").update(update_data).eq("id", item_id).execute()
        return {"status": "success", "data": result.data[0] if result.data else None}
    except Exception as e:
        logger.error(f"Closet update failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update item")


@app.get("/community")
async def get_community(limit: int = 20):
    """Get public community feed."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        result = db.table("fits").select("*").eq("is_public", True).order("created_at", desc=True).limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"Community fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch community")


@app.post("/toggle-public/{fit_id}")
async def toggle_public(fit_id: str, is_public: bool = True):
    """Toggle fit visibility in community feed."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        db.table("fits").update({"is_public": is_public}).eq("id", fit_id).execute()
        return {"status": "success", "is_public": is_public}
    except Exception as e:
        logger.error(f"Toggle public failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update visibility")


# ============ TODO #2: WEATHER INTEGRATION ============

@app.get("/weather")
async def get_weather(lat: Optional[float] = None, lon: Optional[float] = None):
    """
    Get weather data using Open-Meteo API (FREE, no API key needed).
    Provides outfit tips based on weather conditions.
    """
    # Default to a general location if no coordinates
    if not lat or not lon:
        lat, lon = 40.7128, -74.0060  # Default to NYC
    
    try:
        # Open-Meteo is completely free
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        
        response = await http_client.get(weather_url)
        
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="Weather service unavailable")
        
        data = response.json()
        
        current = data.get("current", {})
        daily = data.get("daily", {})
        
        temp = current.get("temperature_2m", 0)
        weather_code = current.get("weather_code", 0)
        is_day = current.get("is_day", 1)
        
        # Get daily high/low
        daily_high = daily.get("temperature_2m_max", [temp])[0] if daily.get("temperature_2m_max") else temp
        daily_low = daily.get("temperature_2m_min", [temp])[0] if daily.get("temperature_2m_min") else temp
        
        # Generate outfit tips based on weather
        outfit_tips = generate_weather_tips(temp, weather_code)
        
        # Weather description
        description = get_weather_description(weather_code, is_day)
        
        return {
            "status": "success",
            "temperature": round(temp),
            "daily_high": round(daily_high),
            "daily_low": round(daily_low),
            "weather_code": weather_code,
            "description": description,
            "is_sunny": weather_code in [0, 1],
            "is_rainy": weather_code >= 51 and weather_code <= 67,
            "outfit_tips": outfit_tips
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        # Return fallback data
        return {
            "status": "fallback",
            "temperature": 22,
            "daily_high": 25,
            "daily_low": 18,
            "weather_code": 3,
            "description": "Partly cloudy",
            "is_sunny": False,
            "is_rainy": False,
            "outfit_tips": ["Layer up for comfort", "Bring a light jacket"]
        }


def generate_weather_tips(temp: float, weather_code: int) -> List[str]:
    """Generate outfit tips based on weather conditions."""
    tips = []
    
    # Temperature-based tips
    if temp <= 5:
        tips.extend(["Bundle up! Heavy coat needed", "Don't forget gloves and scarf"])
    elif temp <= 15:
        tips.extend(["Layer up - it's chilly", "Light jacket or sweater recommended"])
    elif temp <= 22:
        tips.append("Perfect weather - light layers work great")
    elif temp <= 28:
        tips.extend(["Light breathable fabrics", "Sunglasses essential"])
    else:
        tips.extend(["Stay cool - minimal layers", "Light colors recommended", "Stay hydrated!"])
    
    # Weather condition tips
    if weather_code >= 51 and weather_code <= 67:  # Rain
        tips.append("Bring an umbrella!")
        tips.append("Waterproof footwear recommended")
    elif weather_code >= 71 and weather_code <= 77:  # Snow
        tips.append("Wear waterproof boots")
        tips.append("Layer up for snow!")
    elif weather_code >= 80:  # Showers
        tips.append("Light rain jacket recommended")
    
    return tips[:4]  # Limit to 4 tips


def get_weather_description(code: int, is_day: int = 1) -> str:
    """Convert WMO weather code to description."""
    weather_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail"
    }
    return weather_codes.get(code, "Unknown")


# ============ TODO #3: SAVED/FAVORITE OUTFITS ============

@app.post("/toggle-favorite/{fit_id}")
async def toggle_favorite(fit_id: str):
    """Toggle favorite status for an outfit."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get current status
        result = db.table("fits").select("is_favorite").eq("id", fit_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Fit not found")
        
        current_status = result.data[0].get("is_favorite", False)
        new_status = not current_status
        
        # Update
        db.table("fits").update({"is_favorite": new_status}).eq("id", fit_id).execute()
        
        return {
            "status": "success",
            "is_favorite": new_status,
            "message": "Added to favorites!" if new_status else "Removed from favorites"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Toggle favorite failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to toggle favorite")


@app.get("/favorites")
async def get_favorites(limit: int = 20):
    """Get all favorite outfits."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        result = db.table("fits").select("*").eq("is_favorite", True).order("created_at", desc=True).limit(limit).execute()
        
        return {
            "status": "success",
            "count": len(result.data),
            "favorites": result.data
        }
    except Exception as e:
        logger.error(f"Favorites fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch favorites")


# ============ TODO #4: OUTFIT TAGS ============

@app.post("/update-tags/{fit_id}")
async def update_tags(fit_id: str, tags: str = Form(...)):
    """Update tags for an outfit."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Parse tags (comma-separated)
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        tag_list = list(set(tag_list))[:10]  # Remove duplicates, limit to 10
        
        # Update in database
        db.table("fits").update({"tags": tag_list}).eq("id", fit_id).execute()
        
        return {
            "status": "success",
            "tags": tag_list,
            "message": f"Updated {len(tag_list)} tags"
        }
    except Exception as e:
        logger.error(f"Update tags failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update tags")


@app.get("/fits-by-tag/{tag}")
async def get_fits_by_tag(tag: str, limit: int = 20):
    """Get all outfits with a specific tag."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Use Supabase's contains operator for array search
        result = db.table("fits").select("*").contains("tags", [tag.lower()]).order("created_at", desc=True).limit(limit).execute()
        
        return {
            "status": "success",
            "tag": tag.lower(),
            "count": len(result.data),
            "fits": result.data
        }
    except Exception as e:
        logger.error(f"Fetch by tag failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch outfits by tag")


# ============ TODO #5: OUTFIT HISTORY TIMELINE ============

@app.get("/history-timeline")
async def get_history_timeline(days: int = 30):
    """Get outfit history organized by date (timeline view)."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get fits from last X days
        result = db.table("fits").select("*").order("created_at", desc=True).limit(100).execute()
        
        # Organize by date
        timeline = {}
        for fit in result.data:
            created_at = fit.get("created_at", "")
            if created_at:
                # Extract date part
                date_key = created_at.split("T")[0]
                if date_key not in timeline:
                    timeline[date_key] = []
                timeline[date_key].append({
                    "id": fit.get("id"),
                    "occasion": fit.get("occasion"),
                    "occasion_match": fit.get("occasion_match"),
                    "image_url": fit.get("image_url"),
                    "is_favorite": fit.get("is_favorite", False),
                    "tags": fit.get("tags", [])
                })
        
        # Calculate stats
        total_fits = len(result.data)
        avg_match = sum(f.get("occasion_match", 0) for f in result.data) / total_fits if total_fits > 0 else 0
        
        return {
            "status": "success",
            "timeline": timeline,
            "total_fits": total_fits,
            "avg_match": round(avg_match, 1),
            "days_covered": len(timeline)
        }
    except Exception as e:
        logger.error(f"History timeline failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate timeline")


# ============ TODO #6: AI STYLIST CHAT ============

@app.post("/chat")
async def chat_with_stylist(message: ChatMessage):
    """Chat with AI stylist for fashion advice."""
    if not GROQ_KEY and not OPENROUTER_KEY:
        raise HTTPException(status_code=503, detail="AI not configured")
    
    try:
        # Build context from user's history if available
        context_info = ""
        if db and message.context:
            # Could pull user's style profile, recent fits, etc.
            pass
        
        system_prompt = """You are FLAUNT, a friendly AI stylist assistant. You help users with:
- Outfit advice and recommendations
- Color coordination tips
- Occasion-appropriate styling
- Wardrobe organization tips
- Fashion trends (mention them casually, not forced)

Voice: Casual, like texting a stylish friend. Use light slang naturally. Be helpful and encouraging.
Keep responses concise but helpful. If asked about specific items, ask for photos or descriptions.
Never be mean about someone's style - always constructive."""

        # Try Groq first (FREE), then OpenRouter
        response = None
        
        if GROQ_KEY:
            try:
                response = await http_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",  # Good for text chat
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": message.message}
                        ],
                        "temperature": 0.8,
                        "max_tokens": 500
                    }
                )
            except Exception as e:
                logger.warning(f"Groq chat failed: {e}")
        
        if (not response or response.status_code != 200) and OPENROUTER_KEY:
            try:
                response = await http_client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "HTTP-Referer": "https://flaunt.fit",
                        "X-Title": "FLAUNT.FIT"
                    },
                    json={
                        "model": "qwen/qwen3.5-flash-02-23",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": message.message}
                        ],
                        "temperature": 0.8,
                        "max_tokens": 500
                    }
                )
            except Exception as e:
                logger.warning(f"OpenRouter chat failed: {e}")
        
        if not response or response.status_code != 200:
            raise HTTPException(status_code=503, detail="AI service unavailable")
        
        data = response.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "Sorry, I couldn't process that. Try again!")
        
        return {
            "status": "success",
            "reply": reply
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail="Chat failed")


# ============ TODO #7: BULK CLOSET IMPORT ============

@app.post("/bulk-closet-import/")
async def bulk_closet_import(files: List[UploadFile] = File(...)):
    """Import multiple closet items with AI detection."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    if not GROQ_KEY and not OPENROUTER_KEY:
        raise HTTPException(status_code=503, detail="AI not configured for item detection")
    
    imported_items = []
    errors = []
    
    for file in files[:10]:  # Limit to 10 items at once
        try:
            # Validate file
            if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
                errors.append(f"{file.filename}: Invalid file type")
                continue
            
            content = await file.read()
            if len(content) > 5 * 1024 * 1024:  # 5MB limit per file
                errors.append(f"{file.filename}: File too large")
                continue
            
            # Process image
            img = Image.open(BytesIO(content))
            img = fix_image_orientation(img)
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            
            output = BytesIO()
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(output, format="JPEG", quality=80)
            output.seek(0)
            
            # Upload to storage
            file_path = f"{uuid.uuid4()}.jpg"
            image_url = await upload_to_supabase(output.getvalue(), file_path, "image/jpeg")
            
            # AI detection
            b64_image = base64.b64encode(output.getvalue()).decode('utf-8')
            
            detection_prompt = """Identify this clothing item. Return ONLY JSON:
{
    "item_name": "<specific item name, e.g., 'Navy Blue Blazer', 'White Cotton T-Shirt'>",
    "category": "<one of: tops, bottoms, footwear, outerwear, accessories>",
    "color": "<primary color>",
    "style": "<casual/formal/sporty/streetwear>",
    "confidence": "<high/medium/low>"
}"""

            # Try AI detection
            item_data = None
            
            if GROQ_KEY:
                try:
                    response = await http_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {GROQ_KEY}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "llama-3.2-11b-vision-preview",
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": detection_prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                                ]
                            }],
                            "max_tokens": 200
                        }
                    )
                    if response.status_code == 200:
                        data = response.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        item_data = extract_json_from_response(content)
                except Exception as e:
                    logger.warning(f"Groq detection failed: {e}")
            
            # Fallback to filename if AI fails
            if not item_data:
                item_data = {
                    "item_name": file.filename.rsplit(".", 1)[0].replace("_", " ").title(),
                    "category": "tops",
                    "color": "unknown",
                    "confidence": "low"
                }
            
            # Save to wardrobe
            result = db.table("wardrobe").insert({
                "item_name": item_data.get("item_name", "Unknown Item"),
                "image_url": image_url,
                "category": item_data.get("category"),
                "color": item_data.get("color"),
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            
            if result.data:
                imported_items.append({
                    "id": result.data[0].get("id"),
                    "item_name": item_data.get("item_name"),
                    "category": item_data.get("category"),
                    "confidence": item_data.get("confidence")
                })
            
        except Exception as e:
            errors.append(f"{file.filename}: {str(e)}")
    
    return {
        "status": "success",
        "imported_count": len(imported_items),
        "imported_items": imported_items,
        "errors": errors
    }


# ============ TODO #8: SHOPPING VALIDATOR ============

@app.post("/validate-purchase/")
async def validate_purchase(
    file: UploadFile = File(...),
    item_name: str = Form(...),
    price: Optional[str] = Form(None),
    occasion: Optional[str] = Form(None)
):
    """Validate if a purchase is worth it based on user's wardrobe."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get user's current wardrobe
        wardrobe = db.table("wardrobe").select("*").execute().data
        
        # Get user's style history
        fits = db.table("fits").select("occasion, occasion_match").limit(20).execute().data
        
        # Analyze the potential purchase
        content = await file.read()
        img = Image.open(BytesIO(content))
        img.thumbnail((512, 512))
        
        output = BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format="JPEG", quality=80)
        output.seek(0)
        
        b64_image = base64.b64encode(output.getvalue()).decode('utf-8')
        
        # Build validation prompt
        wardrobe_summary = {}
        for item in wardrobe:
            cat = item.get("category", "other")
            wardrobe_summary[cat] = wardrobe_summary.get(cat, 0) + 1
        
        occasion_list = list(set(f.get("occasion", "") for f in fits if f.get("occasion")))
        avg_match = sum(f.get("occasion_match", 0) for f in fits) / len(fits) if fits else 0
        
        validation_prompt = f"""You are a fashion investment advisor. Analyze this potential purchase.

User's current wardrobe: {json.dumps(wardrobe_summary)}
User's common occasions: {occasion_list}
User's average style match: {avg_match:.0f}%

Item being considered: {item_name}
Target occasion: {occasion or 'Not specified'}

Return ONLY JSON:
{{
    "verdict": "<BUY IT / THINK TWICE / SKIP IT>",
    "score": <1-100 how good of an investment>,
    "why": "<2-3 sentences explaining the verdict>",
    "gaps_filled": ["<list occasions/styles this fills>"],
    "alternatives": ["<cheaper alternatives if any>"],
    "styling_tips": "<how to style with existing wardrobe>"
}}"""

        response = None
        
        if GROQ_KEY:
            try:
                response = await http_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "llama-3.2-11b-vision-preview",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": validation_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                            ]
                        }],
                        "max_tokens": 500
                    }
                )
            except Exception as e:
                logger.warning(f"Groq validation failed: {e}")
        
        if (not response or response.status_code != 200) and OPENROUTER_KEY:
            try:
                response = await http_client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "HTTP-Referer": "https://flaunt.fit",
                        "X-Title": "FLAUNT.FIT"
                    },
                    json={
                        "model": "google/gemini-2.5-flash-lite",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": validation_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                            ]
                        }],
                        "max_tokens": 500
                    }
                )
            except Exception as e:
                logger.warning(f"OpenRouter validation failed: {e}")
        
        if not response or response.status_code != 200:
            raise HTTPException(status_code=503, detail="AI service unavailable")
        
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        validation = extract_json_from_response(content)
        
        if not validation:
            validation = {
                "verdict": "THINK TWICE",
                "score": 50,
                "why": "Unable to analyze this item fully. Consider if it fills a gap in your wardrobe.",
                "gaps_filled": [],
                "alternatives": [],
                "styling_tips": "Try it on and see how it feels!"
            }
        
        return {
            "status": "success",
            "validation": validation,
            "wardrobe_context": {
                "total_items": len(wardrobe),
                "category_breakdown": wardrobe_summary
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Purchase validation failed: {e}")
        raise HTTPException(status_code=500, detail="Validation failed")


# ============ TODO #9: OUTFIT CALENDAR ============

@app.post("/calendar/plan")
async def plan_calendar_outfit(plan: CalendarPlan):
    """Plan an outfit for a specific date."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Check if entry already exists for this date
        existing = db.table("outfit_calendar").select("*").eq("planned_date", plan.date).execute()
        
        if existing.data:
            # Update existing
            result = db.table("outfit_calendar").update({
                "occasion": plan.occasion,
                "fit_id": plan.fit_id,
                "notes": plan.notes
            }).eq("planned_date", plan.date).execute()
        else:
            # Create new
            result = db.table("outfit_calendar").insert({
                "planned_date": plan.date,
                "occasion": plan.occasion,
                "fit_id": plan.fit_id,
                "notes": plan.notes,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
        
        return {
            "status": "success",
            "message": f"Outfit planned for {plan.date}",
            "entry": result.data[0] if result.data else None
        }
    except Exception as e:
        logger.error(f"Calendar plan failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to plan outfit")


@app.get("/calendar")
async def get_calendar(start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Get calendar entries for a date range."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Default to current month if no range specified
        if not start_date:
            start_date = datetime.utcnow().strftime("%Y-%m-01")
        if not end_date:
            end_date = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        # Get calendar entries
        result = db.table("outfit_calendar").select("*, fits(*)").gte("planned_date", start_date).lte("planned_date", end_date).order("planned_date").execute()
        
        return {
            "status": "success",
            "start_date": start_date,
            "end_date": end_date,
            "entries": result.data
        }
    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch calendar")


@app.delete("/calendar/{entry_id}")
async def delete_calendar_entry(entry_id: str):
    """Delete a calendar entry."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        db.table("outfit_calendar").delete().eq("id", entry_id).execute()
        return {"status": "success", "message": "Calendar entry deleted"}
    except Exception as e:
        logger.error(f"Calendar delete failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete entry")


# ============ TODO #10: STYLE QUIZ ============

STYLE_QUIZ_QUESTIONS = [
    {
        "id": "q1",
        "question": "What's your go-to weekend outfit?",
        "options": [
            {"value": "casual", "label": "Jeans and a nice tee"},
            {"value": "sporty", "label": "Athletic wear, always ready"},
            {"value": "polished", "label": "Something put-together"},
            {"value": "bold", "label": "Statement pieces only"}
        ]
    },
    {
        "id": "q2",
        "question": "Pick a color palette:",
        "options": [
            {"value": "neutral", "label": "Black, white, beige"},
            {"value": "earth", "label": "Browns, greens, terracotta"},
            {"value": "vibrant", "label": "Brights and neons"},
            {"value": "pastel", "label": "Soft pinks, blues, lavenders"}
        ]
    },
    {
        "id": "q3",
        "question": "Your dream shopping destination?",
        "options": [
            {"value": "minimalist", "label": "Scandinavian boutique"},
            {"value": "trendy", "label": "High-end streetwear store"},
            {"value": "vintage", "label": "Thrift stores and markets"},
            {"value": "luxury", "label": "Designer flagship store"}
        ]
    },
    {
        "id": "q4",
        "question": "What matters most in an outfit?",
        "options": [
            {"value": "comfort", "label": "Comfort is king"},
            {"value": "style", "label": "Looking my best"},
            {"value": "unique", "label": "Standing out"},
            {"value": "versatile", "label": "Can wear it anywhere"}
        ]
    },
    {
        "id": "q5",
        "question": "How do you feel about accessories?",
        "options": [
            {"value": "minimal", "label": "Keep it simple"},
            {"value": "statement", "label": "The more the better"},
            {"value": "functional", "label": "Only if it's useful"},
            {"value": "classic", "label": "Timeless pieces only"}
        ]
    }
]


@app.get("/style-quiz/questions")
async def get_style_quiz_questions():
    """Get style quiz questions."""
    return {
        "status": "success",
        "total_questions": len(STYLE_QUIZ_QUESTIONS),
        "questions": STYLE_QUIZ_QUESTIONS
    }


@app.post("/style-quiz/result")
async def calculate_style_quiz_result(quiz_result: QuizResult):
    """Calculate style quiz result based on answers."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Count answer values
        answer_counts = {}
        for answer in quiz_result.answers:
            value = answer.answer.lower()
            answer_counts[value] = answer_counts.get(value, 0) + 1
        
        # Determine dominant style
        dominant_style = max(answer_counts, key=answer_counts.get) if answer_counts else "casual"
        
        # Map to style profile
        style_profiles = {
            "casual": {
                "style_name": "Effortlessly Chill",
                "description": "You're the master of laid-back cool. Your style is relaxed but never sloppy.",
                "key_pieces": ["Quality basics", "Well-fitted jeans", "Sneakers", "Casual jackets"],
                "avoid": "Overly formal pieces that don't match your vibe",
                "style_icons": ["Ryan Gosling", "Jennifer Aniston"]
            },
            "sporty": {
                "style_name": "Athletic Edge",
                "description": "Comfort meets performance in your wardrobe. You're always ready to move.",
                "key_pieces": ["Performance fabrics", "Sneakers (obviously)", "Track jackets", "Fitted tanks"],
                "avoid": "Restrictive clothing that limits movement",
                "style_icons": ["David Beckham", "Kendall Jenner"]
            },
            "polished": {
                "style_name": "Polished Professional",
                "description": "You always look put-together. Clean lines and quality over quantity.",
                "key_pieces": ["Tailored blazers", "Quality trousers", "Classic shirts", "Loafers"],
                "avoid": "Wrinkled fabrics and ill-fitting pieces",
                "style_icons": ["Amal Clooney", "Idris Elba"]
            },
            "bold": {
                "style_name": "Bold Statement",
                "description": "You're not afraid to stand out. Your style is your signature.",
                "key_pieces": ["Statement jackets", "Unique accessories", "Bold prints", "Eye-catching shoes"],
                "avoid": "Boring basics that blend in",
                "style_icons": ["Billy Porter", "Zendaya"]
            },
            "minimalist": {
                "style_name": "Minimalist Maven",
                "description": "Less is more in your book. Clean, simple, timeless.",
                "key_pieces": ["White shirts", "Black trousers", "Quality basics", "Simple jewelry"],
                "avoid": "Excessive prints and busy patterns",
                "style_icons": ["Victoria Beckham", "Steve Jobs"]
            },
            "vintage": {
                "style_name": "Vintage Soul",
                "description": "You love pieces with history. Thrift stores are your playground.",
                "key_pieces": ["Vintage denim", "Retro jackets", "Unique finds", "Classic accessories"],
                "avoid": "Fast fashion basics everyone has",
                "style_icons": ["Alexa Chung", "Harry Styles"]
            },
            "neutral": {
                "style_name": "Neutral Nation",
                "description": "You stick to the classics. Earth tones and neutrals are your palette.",
                "key_pieces": ["Beige coats", "White tees", "Tan accessories", "Black boots"],
                "avoid": "Bright neons and clashing colors",
                "style_icons": ["Kim Kardashian", "Kanye West"]
            },
            "earth": {
                "style_name": "Earth Child",
                "description": "Nature-inspired tones ground your style. Warm and approachable.",
                "key_pieces": ["Terracotta pieces", "Olive greens", "Natural fabrics", "Wooden accessories"],
                "avoid": "Synthetic materials and cold colors",
                "style_icons": ["Bella Hadid", "Timothée Chalamet"]
            },
            "vibrant": {
                "style_name": "Color Enthusiast",
                "description": "Life's too short for boring colors. You brighten every room.",
                "key_pieces": ["Colorful tops", "Bright accessories", "Fun patterns", "Statement shoes"],
                "avoid": "All-black outfits unless it's a statement",
                "style_icons": ["Cardi B", "Lil Nas X"]
            }
        }
        
        # Get profile or default
        profile = style_profiles.get(dominant_style, style_profiles["casual"])
        
        # Save to database (using style_quiz_results table)
        if db:
            try:
                db.table("style_quiz_results").insert({
                    "answers": [{"question_id": a.question_id, "answer": a.answer} for a in quiz_result.answers],
                    "style_type": profile["style_name"],
                    "style_description": profile["description"],
                    "style_keywords": profile["key_pieces"],
                    "style_icons": profile["style_icons"],
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                logger.warning(f"Failed to save style quiz result: {e}")
        
        return {
            "status": "success",
            "style_name": profile["style_name"],
            "dominant_style": dominant_style,
            "description": profile["description"],
            "key_pieces": profile["key_pieces"],
            "avoid": profile["avoid"],
            "style_icons": profile["style_icons"],
            "answer_breakdown": answer_counts
        }
        
    except Exception as e:
        logger.error(f"Style quiz calculation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate style result")


# ============ ADDITIONAL ENDPOINTS (Frontend Support) ============

@app.get("/gap-analysis")
async def get_gap_analysis():
    """Analyze wardrobe gaps and provide recommendations."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get all wardrobe items
        wardrobe = db.table("wardrobe").select("*").execute().data
        
        # Category requirements
        essential_categories = {
            "tops": {"min": 5, "importance": "high"},
            "bottoms": {"min": 3, "importance": "high"},
            "footwear": {"min": 2, "importance": "medium"},
            "outerwear": {"min": 1, "importance": "medium"},
            "accessories": {"min": 2, "importance": "low"}
        }
        
        # Count items per category
        category_counts = {}
        for item in wardrobe:
            cat = item.get("category", "other").lower()
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        # Find gaps
        gaps = []
        for cat, req in essential_categories.items():
            current = category_counts.get(cat, 0)
            if current < req["min"]:
                gaps.append({
                    "category": cat,
                    "current": current,
                    "recommended": req["min"],
                    "importance": req["importance"],
                    "suggestion": f"You have {current} {cat}. Add {req['min'] - current} more for a complete wardrobe."
                })
        
        # Calculate completeness score
        total_essential = sum(r["min"] for r in essential_categories.values())
        current_essential = sum(min(category_counts.get(cat, 0), req["min"]) for cat, req in essential_categories.items())
        completeness = round((current_essential / total_essential) * 100) if total_essential > 0 else 0
        
        # Generate one purchase tip
        one_tip = ""
        if gaps:
            high_priority = [g for g in gaps if g["importance"] == "high"]
            if high_priority:
                gap = high_priority[0]
                one_tip = f"Add more {gap['category']} to complete your wardrobe"
            else:
                one_tip = f"Consider adding {gaps[0]['category']} for variety"
        else:
            one_tip = "Your wardrobe is well-balanced!"
        
        return {
            "status": "success",
            "total_items": len(wardrobe),
            "category_counts": category_counts,
            "completeness_score": completeness,
            "gaps": gaps,
            "one_purchase_tip": one_tip,
            "strengths": [f"Good variety in {cat}" for cat, count in category_counts.items() if count >= 3]
        }
    except Exception as e:
        logger.error(f"Gap analysis failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze wardrobe")


@app.get("/style-profile")
async def get_style_profile():
    """Get AI-generated style profile based on user data."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get user's fits
        fits = db.table("fits").select("*").order("created_at", desc=True).limit(50).execute().data
        
        # Get wardrobe
        wardrobe = db.table("wardrobe").select("*").execute().data
        
        if not fits:
            return {
                "status": "success",
                "profile": None,
                "message": "Analyze some outfits first to build your style profile!"
            }
        
        # Calculate style metrics
        occasions = [f.get("occasion") for f in fits if f.get("occasion")]
        occasion_counts = {}
        for occ in occasions:
            occasion_counts[occ] = occasion_counts.get(occ, 0) + 1
        
        # Preferred occasions (top 3)
        preferred_occasions = sorted(
            [{"occasion": k, "count": v} for k, v in occasion_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:3]
        
        # Color analysis from wardrobe
        colors = [item.get("color") for item in wardrobe if item.get("color")]
        color_counts = {}
        for color in colors:
            color_counts[color] = color_counts.get(color, 0) + 1
        preferred_colors = sorted(
            [{"color": k, "count": v} for k, v in color_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:5]
        
        # Average score
        avg_score = sum(f.get("occasion_match", 0) for f in fits) / len(fits) if fits else 0
        
        # Determine style persona
        if avg_score >= 80:
            persona = "Style Icon"
        elif avg_score >= 65:
            persona = "Fashion Forward"
        elif avg_score >= 50:
            persona = "Trend Explorer"
        else:
            persona = "Style Student"
        
        # Generate recommendations
        recommendations = []
        if preferred_occasions:
            recommendations.append(f"You shine at {preferred_occasions[0]['occasion']} events")
        if len(wardrobe) < 10:
            recommendations.append("Build your wardrobe with versatile basics")
        if avg_score < 60:
            recommendations.append("Focus on occasion-appropriate pieces")
        
        return {
            "status": "success",
            "profile": {
                "style_persona": persona,
                "total_outfits": len(fits),
                "avg_score": round(avg_score, 1),
                "preferred_occasions": preferred_occasions,
                "preferred_colors": preferred_colors,
                "wardrobe_count": len(wardrobe),
                "recommendations": recommendations
            }
        }
    except Exception as e:
        logger.error(f"Style profile failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate style profile")


@app.post("/dress-me/")
async def dress_me_from_closet(
    occasion: str = Form(...),
    vibe_goal: Optional[str] = Form(None)
):
    """Get outfit suggestions from user's closet."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get wardrobe items
        wardrobe = db.table("wardrobe").select("*").execute().data
        
        if len(wardrobe) < 3:
            return {
                "status": "success",
                "message": "Add more items to your closet first!",
                "outfits": [],
                "wardrobe_count": len(wardrobe)
            }
        
        # Categorize items
        by_category = {}
        for item in wardrobe:
            cat = item.get("category", "other").lower()
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(item)
        
        # Generate outfit combinations
        outfits = []
        
        # Basic outfit: top + bottom + footwear
        tops = by_category.get("tops", [])
        bottoms = by_category.get("bottoms", [])
        footwear = by_category.get("footwear", [])
        
        # Create up to 3 outfit suggestions
        for i in range(min(3, len(tops), len(bottoms) if bottoms else 1)):
            outfit = {
                "name": f"Look {i+1}",
                "items": [],
                "why_it_works": "",
                "style_rating": 7
            }
            
            if tops:
                outfit["items"].append(tops[i % len(tops)]["item_name"])
            if bottoms:
                outfit["items"].append(bottoms[i % len(bottoms)]["item_name"])
            if footwear:
                outfit["items"].append(footwear[i % len(footwear)]["item_name"])
            if by_category.get("outerwear"):
                outfit["items"].append(by_category["outerwear"][0]["item_name"])
            
            outfit["why_it_works"] = f"This combination works great for {occasion}"
            outfit["style_rating"] = 7 + i
            
            outfits.append(outfit)
        
        # Generate missing item suggestion
        missing = []
        if not by_category.get("tops"):
            missing.append("Add some tops to your closet")
        if not by_category.get("bottoms"):
            missing.append("Add pants or skirts to your closet")
        if not by_category.get("footwear"):
            missing.append("Add shoes to complete your looks")
        
        return {
            "status": "success",
            "outfits": outfits,
            "wardrobe_count": len(wardrobe),
            "missing_item": missing[0] if missing else None,
            "tip": f"For {occasion}, consider the vibe you want to project"
        }
    except Exception as e:
        logger.error(f"Dress me failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate outfit suggestions")


@app.post("/virtual-try-on")
async def virtual_try_on(
    item_id: str = Form(...),
    style_note: Optional[str] = Form(None)
):
    """Generate virtual try-on preview (placeholder for actual implementation)."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get item from wardrobe
        result = db.table("wardrobe").select("*").eq("id", item_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item = result.data[0]
        
        # In a real implementation, this would use an image generation model
        # For now, return a placeholder response
        return {
            "status": "success",
            "message": "Virtual try-on generated!",
            "item_name": item.get("item_name"),
            "image_url": item.get("image_url"),  # In real app, this would be the generated image
            "style_note": style_note,
            "note": "Full virtual try-on requires image generation integration"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Virtual try-on failed: {e}")
        raise HTTPException(status_code=500, detail="Virtual try-on failed")


@app.post("/generate-item-image/{item_id}")
async def generate_item_image(item_id: str):
    """Generate an AI image for a closet item."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    
    try:
        # Get item
        result = db.table("wardrobe").select("*").eq("id", item_id).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item = result.data[0]
        item_name = item.get("item_name", "")
        category = item.get("category", "")
        color = item.get("color", "")
        
        # In a real implementation, use image generation API
        # For now, return success with existing or placeholder
        return {
            "status": "success",
            "message": "Image generation requires AI image API integration",
            "item_id": item_id,
            "item_name": item_name
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate image")


# ============ ERROR HANDLERS ============

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Something went wrong. Try again."}
    )


# ============ MAIN ============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )