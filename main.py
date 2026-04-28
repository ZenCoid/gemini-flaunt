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
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from PIL import Image, ExifTags
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
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
- You are a CHAOTIC Gen Z roaster. Brain rot energy. Talk like a TikTok comment section.
- Heavy slang: fr, nah, dead, cooked, mid, ate, slay, NPC, no cap, on god, bussin, mid, tragic, caught in 4k, giving, screaming, crying, throwing up
- Be dramatic and unhinged. Over-the-top reactions.
- Reference brain rot culture:Ohio, skibidi energy, NPC behavior, main character, side character, 2016 vibes, discord mod energy
- Roast patterns:
  * "nahhh bro is actually cooked 💀 [roast]"
  * "this fit giving [embarrassing thing] energy fr fr"
  * "not the [item] screaming for help 😭"
  * "on god this is the most [adjective] thing I've seen today"
  * "bro really woke up and chose this fit... bold choice"
- Be SPECIFIC about items. Make it sting but funny.
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
        
        # Upload to Supabase storage
        image_url = None
        if db:
            try:
                file_path = f"{uuid.uuid4()}.jpg"
                db.storage.from_("outfits").upload(
                    file_path, 
                    output.getvalue(), 
                    {"content-type": "image/jpeg"}
                )
                image_url = db.storage.from_("outfits").get_public_url(file_path)
            except Exception as e:
                logger.warning(f"Storage upload failed: {e}")
        
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
