"""
FLAUNT.FIT — AI Stylist Backend
"Don't Rate. Match." Philosophy
Dual Roast Mode + Full User Authentication
Version 4.0 — Authenticated Edition
"""

import os
import json
import re
import uuid
import base64
import logging
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from PIL import Image, ExifTags
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from supabase import create_client, Client

# ============================================================
# ENVIRONMENT & CONFIGURATION
# ============================================================
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flaunt")

def validate_env():
    required = ["SUPABASE_URL", "SUPABASE_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {missing}")
    if not os.getenv("OPENROUTER_API_KEY") and not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError("Need at least one: OPENROUTER_API_KEY or GROQ_API_KEY")

try:
    validate_env()
except EnvironmentError as e:
    logger.warning(f"Environment warning: {e}")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # Use Service Role Key for backend
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")

GROQ_MODELS = ["llama-3.2-11b-vision-preview"]
OPENROUTER_MODELS = [
    "google/gemini-2.5-flash-lite",
    "qwen/qwen3.5-flash-02-23"
]

# Supabase Client
db: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        db = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase connected successfully")
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")

http_client = httpx.AsyncClient(timeout=60.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await http_client.aclose()

app = FastAPI(
    title="FLAUNT.FIT API",
    description="AI Stylist — Don't Rate. Match.",
    version="4.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# ============================================================
# MODELS
# ============================================================
class AnalysisResult(BaseModel):
    status: str
    overall_score: int
    occasion_fit: int
    color_harmony: str
    formality_calibration: str
    the_fix: str
    items_spotted: list[str]
    vibe_check: str
    confidence: str
    roast: Optional[str] = None
    image_url: Optional[str] = None
    fit_id: Optional[str] = None

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

# ============================================================
# UTILITIES
# ============================================================
def fix_image_orientation(img: Image.Image) -> Image.Image:
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
    """Robust JSON extraction from AI responses."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    try:
        cleaned = re.sub(r'```(?:json)?\s*', '', text)
        cleaned = re.sub(r'```\s*', '', cleaned)
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return None

async def upload_to_supabase(file_bytes: bytes, file_path: str, content_type: str = "image/jpeg") -> Optional[str]:
    if not db:
        return None
    try:
        db.storage.from_("outfits").upload(
            file_path, file_bytes, {"content-type": content_type},
            upsert=True
        )
        return db.storage.from_("outfits").get_public_url(file_path)
    except Exception as e:
        logger.error(f"Supabase upload failed: {e}")
        try:
            db.storage.from_("outfits").upload(
                file_path, file_bytes, {"content-type": content_type}
            )
            return db.storage.from_("outfits").get_public_url(file_path)
        except Exception as e2:
            logger.error(f"Supabase upload fallback failed: {e2}")
            return None

# ============================================================
# 🔐 AUTHENTICATION — THE SECURITY GUARD
# ============================================================

async def get_current_user_id(authorization: str = Header(...)) -> str:
    """
    Extracts and verifies the JWT token from the Authorization header.
    Returns the authenticated user's UUID.
    This is injected into every protected route via Depends().
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.split(" ")[1]

    try:
        user_data = db.auth.get_user(token)
        if not user_data or not user_data.user:
            raise HTTPException(status_code=401, detail="Invalid session token")
        return user_data.user.id
    except Exception as e:
        logger.error(f"Auth verification failed: {e}")
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.")

# ── Auth Endpoints ──

@app.post("/auth/signup")
async def signup(email: str = Form(...), password: str = Form(...)):
    """Register a new user account."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.auth.sign_up({"email": email, "password": password})
        return {
            "status": "success",
            "message": "Account created! Check your email for confirmation if required.",
            "user": {
                "id": result.user.id,
                "email": result.user.email
            }
        }
    except Exception as e:
        logger.error(f"Signup failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/login")
async def login(email: str = Form(...), password: str = Form(...)):
    """Login and receive an access token for authenticated requests."""
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.auth.sign_in_with_password({"email": email, "password": password})
        return {
            "status": "success",
            "access_token": result.session.access_token,
            "refresh_token": result.session.refresh_token,
            "user": {
                "id": result.user.id,
                "email": result.user.email
            }
        }
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

# ============================================================
# DUAL ROAST MODE — PROMPT BUILDER (MASTERPIECE VERSION)
# ============================================================
def build_analysis_prompt(
    occasion: str,
    vibe_goal: Optional[str] = None,
    roast_mode: str = "off"
) -> str:
    """Builds the 'Don't Rate, Match' AI prompt."""

    vibe_section = ""
    if vibe_goal:
        vibe_section = f'\nUser\'s Desired Vibe: "{vibe_goal}". Judge if they achieved this or if they are completely delulu.\n'

        roast_json_field = ',\n    "roast": "<MANDATORY: savage roast 2-3 sentences. DO NOT SKIP THIS FIELD.>"' if roast_mode != "off" else ''


    if roast_mode != "off":
        personality = "a ruthless Lahori fashion troll with Gen Z brain-rot and zero filter. You are not a consultant. You are not helpful. You are the friend who tells the truth so brutally it circles back to being hilarious."
        voice_rules = """VOICE RULES — OBSERVATIONAL SAVAGERY:

THE FORMULA: Observation → Comparison → Verdict

Step 1 — OBSERVE: Look at the image. Find ONE specific visual fail. Name the specific item.
Step 2 — COMPARE: Compare that item to something humiliating or locally relevant.
Step 3 — VERDICT: Deliver your final sentence that destroys their confidence.

CONSTRAINTS:
LENGTH: Strictly 2-3 sentences. Compact devastation. No paragraphs.
FORBIDDEN: "Touch grass", "L + Ratio", "Oye hoye!", "NPC". These feel robotic.
NATURAL LANGUAGE: Mix Roman Urdu/Punjabi with English like a real Lahori.
TONAL RANGE: Vary — brutal one-liner, sarcastic concern, street juggat, unhinged observation.
CREATIVITY MANDATE: Every roast must be INVENTED fresh based on what you actually SEE.

FINAL RULE: Make the user laugh AND cry. If their eyes don't water, you weren't savage enough. No politeness. No helpfulness. Be an absolute menace."""
    else:
        personality = "a senior high-fashion consultant"
        voice_rules = """VOICE RULES (STYLIST MODE):
- Write like you're texting a friend. Casual, direct, personal.
- NEVER say "Consider adding..." or "You might want to..."
- Instead say: "Honestly, this jacket is carrying the whole fit" or "Those shoes are fighting with the pants".
- Never be cruel. Honest but encouraging."""

        json_instruction = "Return ONLY valid JSON. The 'roast' key is MANDATORY when Roast Mode is ON. If you leave it empty or output placeholder text, the app breaks. NO text outside brackets. NO markdown."


    return f"""Act as {personality}. Analyze this {occasion} outfit.
{vibe_section}

{voice_rules}

CRITICAL: {json_instruction}

{{
    "occasion_fit": <integer 0-100>,
    "color_harmony": <integer 0-100>,
    "style_coherence": <integer 0-100>,
    "fit_proportion": <integer 0-100>,
    "trend_score": <integer 0-100>,
    "color_harmony_text": "<brief color analysis>",
    "formality_calibration": "<overdressed/underdressed/nailed it, one sentence>",
    "the_fix": "<ONE specific fix or 'Lock it in.'>",
    "items_spotted": ["<list actual items>"],
     "vibe_check": "<1-2 sentence honest reaction>"{roast_json_field},
    "confidence": "<'High', 'Medium', or 'Low'>"
}}"""

# ============================================================
# ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "groq": "configured" if GROQ_KEY else "not set",
        "openrouter": "configured" if OPENROUTER_KEY else "not set",
        "database": "connected" if db else "disconnected"
    }

# ── CORE: ANALYZE FIT (Protected) ──
@app.post("/analyze-fit/", response_model=AnalysisResult)
async def analyze_fit(
    file: UploadFile = File(...),
    occasion: str = Form(...),
    vibe_goal: Optional[str] = Form(None),
    roast_mode: str = Form("off"),
    current_user_id: str = Depends(get_current_user_id)  # 🔐 AUTH REQUIRED
):
    if not occasion or len(occasion.strip()) == 0:
        raise HTTPException(status_code=400, detail="Occasion is required")

    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/jpg"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {', '.join(allowed_types)}")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB")

    try:
        # Process image
        img = Image.open(BytesIO(content))
        img = fix_image_orientation(img)
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

        output = BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format="JPEG", quality=85)
        output.seek(0)

        # Upload to Supabase — scoped to this user's folder
        image_url = None
        if db:
            file_path = f"{current_user_id}/{uuid.uuid4()}.jpg"
            image_url = await upload_to_supabase(output.getvalue(), file_path, "image/jpeg")

        # Build prompt and call AI
        b64_image = base64.b64encode(output.getvalue()).decode('utf-8')
        prompt = build_analysis_prompt(
            occasion.strip(),
            vibe_goal.strip() if vibe_goal else None,
            roast_mode=roast_mode.lower()
        )

        response = None
        last_error = None

        # Try Groq first
        if GROQ_KEY:
            for model in GROQ_MODELS:
                try:
                    logger.info(f"Trying Groq: {model}")
                    response = await http_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                            ]}],
                            "temperature": 0.9,
                            "max_tokens": 1000
                        }
                    )
                    if response.status_code == 200:
                        break
                    else:
                        last_error = f"Groq error: {response.status_code}"
                        response = None
                except Exception as e:
                    logger.warning(f"Groq error: {e}")
                    last_error = str(e)
                    response = None

        # Fallback to OpenRouter
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
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                                ]}],
                                "temperature": 0.9,
                                "max_tokens": 1000
                            }
                        )
                        if response.status_code == 200:
                            break
                        else:
                            last_error = f"OpenRouter error: {response.status_code}"
                            response = None
                    except Exception as e:
                        logger.warning(f"OpenRouter error: {e}")
                        last_error = str(e)
                        response = None

        if not response or response.status_code != 200:
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {last_error}")

        resp_data = response.json()
        if "error" in resp_data:
            raise HTTPException(status_code=502, detail=f"AI error: {resp_data['error'].get('message', str(resp_data['error']))}")
        if "choices" not in resp_data or not resp_data["choices"]:
            raise HTTPException(status_code=502, detail="AI gave weird response. Try again.")

        ai_content = resp_data["choices"][0]["message"]["content"]
        ai_data = extract_json_from_response(ai_content)
        if not ai_data:
            raise HTTPException(status_code=502, detail="AI fumbled the response. Give it another shot.")

        required_fields = [
            "occasion_fit", "color_harmony", "style_coherence", "fit_proportion",
            "trend_score", "color_harmony_text", "formality_calibration",
            "the_fix", "items_spotted", "vibe_check", "confidence"
        ]
        missing = [f for f in required_fields if f not in ai_data]
        if missing:
            raise HTTPException(status_code=502, detail=f"AI response incomplete. Missing: {missing}")

        def safe_int(val, default=50):
            try:
                v = int(val)
                return max(0, min(100, v))
            except:
                return default

        occasion_fit_val = safe_int(ai_data.get("occasion_fit", 50))
        color_harmony_val = safe_int(ai_data.get("color_harmony", 50))
        style_coherence_val = safe_int(ai_data.get("style_coherence", 50))
        fit_proportion_val = safe_int(ai_data.get("fit_proportion", 50))
        trend_score_val = safe_int(ai_data.get("trend_score", 50))

        items = ai_data.get("items_spotted", [])
        if not isinstance(items, list):
            items = [str(items)] if items else []
        items = [str(item) for item in items[:10]]

        total = round(
            occasion_fit_val * 0.25 +
            color_harmony_val * 0.20 +
            style_coherence_val * 0.20 +
            fit_proportion_val * 0.20 +
            trend_score_val * 0.15
        )

        # 🔐 SAVE WITH REAL USER ID — NO MORE "guest"
        fit_id = None
        if db:
            try:
                result = db.table("fits").insert({
                    "user_id": current_user_id,          # ← REAL USER ID
                    "occasion": occasion.strip(),
                    "occasion_fit": occasion_fit_val,
                    "color_harmony": color_harmony_val,
                    "style_coherence": style_coherence_val,
                    "fit_proportion": fit_proportion_val,
                    "trend_score": trend_score_val,
                    "color_harmony_text": ai_data.get("color_harmony_text", ""),
                    "formality_calibration": ai_data.get("formality_calibration", ""),
                    "the_fix": ai_data.get("the_fix", ""),
                    "items_spotted": items,
                    "vibe_check": ai_data.get("vibe_check", ""),
                    "confidence": ai_data.get("confidence", "Medium"),
                    "vibe_goal": vibe_goal.strip() if vibe_goal else None,
                    "roast_text": ai_data.get("roast", ""),
                    "roast_style": roast_mode.lower(),
                    "image_url": image_url,
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
                if result.data:
                    fit_id = result.data[0].get("id")
            except Exception as e:
                logger.warning(f"Database insert failed: {e}")

        return AnalysisResult(
            status="success",
            overall_score=total,
            occasion_fit=occasion_fit_val,
            color_harmony=str(ai_data.get("color_harmony_text", "")),
            formality_calibration=str(ai_data.get("formality_calibration", "")),
            the_fix=str(ai_data.get("the_fix", "")),
            items_spotted=items,
            vibe_check=str(ai_data.get("vibe_check", "")),
            confidence=str(ai_data.get("confidence", "Medium")),
            roast=ai_data.get("roast"),
            image_url=image_url,
            fit_id=fit_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

# ── HISTORY (Protected) ──
@app.get("/history")
async def get_history(
    limit: int = 20,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("*") \
            .eq("user_id", current_user_id) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"History fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")

# ── PROFILE DNA (Protected) ──
@app.get("/profile-dna")
async def get_profile_dna(current_user_id: str = Depends(get_current_user_id)):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        fits = db.table("fits").select("occasion, occasion_fit, vibe_goal") \
            .eq("user_id", current_user_id).execute().data

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

        avg_match = round(sum(f.get("occasion_fit", 0) for f in fits) / len(fits), 1)
        occasions = [f.get("occasion") for f in fits if f.get("occasion")]
        top_occasion = max(set(occasions), key=occasions.count) if occasions else None

        level = "Icon" if avg_match >= 85 else "Trendsetter" if avg_match >= 70 else "Curator" if avg_match >= 50 else "Explorer"
        dna = f"{top_occasion} Pro" if top_occasion else "Style Chameleon"
        insight = "You're eating. Elite occasion matching." if avg_match >= 80 else \
                  "Solid instincts. Fine-tune with accessories." if avg_match >= 60 else \
                  "Room to grow. Focus on occasion-appropriate pieces."

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

# ── WARDROBE / CLOSET (All Protected) ──

@app.post("/add-to-closet/")
async def add_to_closet(
    item_name: str = Form(...),
    image_url: str = Form(...),
    category: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    item_name = item_name.strip()
    if not item_name or len(item_name) > 100:
        raise HTTPException(status_code=400, detail="Item name must be 1-100 characters")
    try:
        result = db.table("wardrobe").insert({
            "user_id": current_user_id,  # ← Scoped to this user
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
async def get_closet(
    limit: int = 50,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("wardrobe").select("*") \
            .eq("user_id", current_user_id) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"Closet fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch closet")

@app.delete("/closet/{item_id}")
async def remove_from_closet(
    item_id: str,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        db.table("wardrobe").delete() \
            .eq("id", item_id) \
            .eq("user_id", current_user_id).execute()
        return {"status": "success", "message": "Item removed"}
    except Exception as e:
        logger.error(f"Closet remove failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove item")

@app.put("/closet/{item_id}")
async def update_closet_item(
    item_id: str,
    item_name: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        update_data = {}
        if item_name: update_data["item_name"] = item_name.strip()
        if category: update_data["category"] = category.strip()
        if color: update_data["color"] = color.strip()
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        result = db.table("wardrobe").update(update_data) \
            .eq("id", item_id) \
            .eq("user_id", current_user_id).execute()
        return {"status": "success", "data": result.data[0] if result.data else None}
    except Exception as e:
        logger.error(f"Closet update failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update item")

# ── COMMUNITY (Public — anyone can see public fits) ──
@app.get("/community")
async def get_community(limit: int = 20):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("*") \
            .eq("is_public", True) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"status": "success", "data": result.data}
    except Exception as e:
        logger.error(f"Community fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch community")

@app.post("/toggle-public/{fit_id}")
async def toggle_public(
    fit_id: str,
    is_public: bool = True,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        db.table("fits").update({"is_public": is_public}) \
            .eq("id", fit_id) \
            .eq("user_id", current_user_id).execute()
        return {"status": "success", "is_public": is_public}
    except Exception as e:
        logger.error(f"Toggle public failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update visibility")

# ── FAVORITES (Protected) ──
@app.post("/toggle-favorite/{fit_id}")
async def toggle_favorite(
    fit_id: str,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("is_favorite") \
            .eq("id", fit_id) \
            .eq("user_id", current_user_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Fit not found")
        new_status = not result.data[0].get("is_favorite", False)
        db.table("fits").update({"is_favorite": new_status}) \
            .eq("id", fit_id) \
            .eq("user_id", current_user_id).execute()
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
async def get_favorites(
    limit: int = 20,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("*") \
            .eq("user_id", current_user_id) \
            .eq("is_favorite", True) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"status": "success", "count": len(result.data), "favorites": result.data}
    except Exception as e:
        logger.error(f"Favorites fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch favorites")

# ── TAGS (Protected) ──
@app.post("/update-tags/{fit_id}")
async def update_tags(
    fit_id: str,
    tags: str = Form(...),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        tag_list = list(set([t.strip().lower() for t in tags.split(",") if t.strip()]))[:10]
        db.table("fits").update({"tags": tag_list}) \
            .eq("id", fit_id) \
            .eq("user_id", current_user_id).execute()
        return {"status": "success", "tags": tag_list, "message": f"Updated {len(tag_list)} tags"}
    except Exception as e:
        logger.error(f"Update tags failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update tags")

# ── FITS BY TAG (Public) ──
@app.get("/fits-by-tag/{tag}")
async def get_fits_by_tag(tag: str, limit: int = 20):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("*") \
            .eq("is_public", True) \
            .contains("tags", [tag.lower()]) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"status": "success", "tag": tag.lower(), "count": len(result.data), "fits": result.data}
    except Exception as e:
        logger.error(f"Fetch by tag failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch outfits by tag")

# ── HISTORY TIMELINE (Protected) ──
@app.get("/history-timeline")
async def get_history_timeline(
    days: int = 30,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("fits").select("*") \
            .eq("user_id", current_user_id) \
            .order("created_at", desc=True) \
            .limit(100).execute()
        timeline = {}
        for fit in result.data:
            dt = fit.get("created_at", "")
            if dt:
                date_key = dt.split("T")[0]
                if date_key not in timeline:
                    timeline[date_key] = []
                timeline[date_key].append({
                    "id": fit.get("id"),
                    "occasion": fit.get("occasion"),
                    "occasion_fit": fit.get("occasion_fit"),
                    "image_url": fit.get("image_url"),
                    "is_favorite": fit.get("is_favorite", False),
                    "tags": fit.get("tags", [])
                })
        total = len(result.data)
        avg = sum(f.get("occasion_fit", 0) for f in result.data) / total if total > 0 else 0
        return {
            "status": "success",
            "timeline": timeline,
            "total_fits": total,
            "avg_match": round(avg, 1),
            "days_covered": len(timeline)
        }
    except Exception as e:
        logger.error(f"History timeline failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate timeline")

# ── WEATHER (Public — no auth needed) ──
@app.get("/weather")
async def get_weather(lat: Optional[float] = None, lon: Optional[float] = None):
    if not lat or not lon:
        lat, lon = 31.5204, 74.3587  # Default: Lahore
    try:
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        response = await http_client.get(weather_url)
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="Weather service unavailable")
        data = response.json()
        current = data.get("current", {})
        temp = current.get("temperature_2m", 22)
        weather_code = current.get("weather_code", 0)
        daily = data.get("daily", {})
        daily_high = daily.get("temperature_2m_max", [temp])[0] if daily.get("temperature_2m_max") else temp
        daily_low = daily.get("temperature_2m_min", [temp])[0] if daily.get("temperature_2m_min") else temp

        tips = []
        if temp <= 5:
            tips.extend(["Bundle up! Heavy coat needed", "Don't forget gloves and scarf"])
        elif temp <= 15:
            tips.extend(["Layer up — it's chilly", "Light jacket or sweater recommended"])
        elif temp <= 22:
            tips.append("Perfect weather — light layers work great")
        elif temp <= 28:
            tips.extend(["Light breathable fabrics", "Sunglasses essential"])
        else:
            tips.extend(["Stay cool — minimal layers", "Light colors recommended", "Stay hydrated!"])

        if 51 <= weather_code <= 67:
            tips.extend(["Bring an umbrella!", "Waterproof footwear"])
        elif 71 <= weather_code <= 77:
            tips.extend(["Waterproof boots", "Layer up for snow!"])
        elif weather_code >= 80:
            tips.append("Light rain jacket recommended")

        descriptions = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 51: "Light drizzle", 61: "Slight rain", 63: "Moderate rain",
            65: "Heavy rain", 71: "Slight snow", 95: "Thunderstorm"
        }
        return {
            "status": "success",
            "temperature": round(temp),
            "daily_high": round(daily_high),
            "daily_low": round(daily_low),
            "weather_code": weather_code,
            "description": descriptions.get(weather_code, "Unknown"),
            "is_sunny": weather_code in [0, 1],
            "is_rainy": 51 <= weather_code <= 67,
            "outfit_tips": tips[:4]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
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

# ── AI CHAT (Protected) ──
@app.post("/chat")
async def chat_with_stylist(
    message: ChatMessage,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not GROQ_KEY and not OPENROUTER_KEY:
        raise HTTPException(status_code=503, detail="AI not configured")
    try:
        system_prompt = "You are FLAUNT, a friendly AI stylist assistant. Help users with outfit advice, color coordination, occasion-appropriate styling, wardrobe organization, and fashion trends. Voice: Casual, like texting a stylish friend. Keep responses concise but helpful. Never be mean about someone's style — always constructive."
        response = None
        if GROQ_KEY:
            try:
                response = await http_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": message.message}
                        ],
                        "temperature": 0.8,
                        "max_tokens": 500
                    }
                )
            except:
                pass
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
            except:
                pass
        if not response or response.status_code != 200:
            raise HTTPException(status_code=503, detail="AI service unavailable")
        data = response.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "Sorry, couldn't process that. Try again!")
        return {"status": "success", "reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail="Chat failed")

# ── BULK CLOSET IMPORT (Protected) ──
@app.post("/bulk-closet-import/")
async def bulk_closet_import(
    files: List[UploadFile] = File(...),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    if not GROQ_KEY and not OPENROUTER_KEY:
        raise HTTPException(status_code=503, detail="AI not configured for item detection")

    imported_items, errors = [], []
    for file in files[:10]:
        try:
            if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
                errors.append(f"{file.filename}: Invalid file type")
                continue
            content = await file.read()
            if len(content) > 5 * 1024 * 1024:
                errors.append(f"{file.filename}: File too large")
                continue

            img = Image.open(BytesIO(content))
            img = fix_image_orientation(img)
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(output, format="JPEG", quality=80)
            output.seek(0)

            file_path = f"{current_user_id}/{uuid.uuid4()}.jpg"
            image_url = await upload_to_supabase(output.getvalue(), file_path, "image/jpeg")
            b64_image = base64.b64encode(output.getvalue()).decode('utf-8')

            detection_prompt = 'Identify this clothing item. Return ONLY JSON: {"item_name": "<specific name>", "category": "<tops/bottoms/footwear/outerwear/accessories>", "color": "<primary color>", "confidence": "<high/medium/low>"}'
            item_data = None
            if GROQ_KEY:
                try:
                    response = await http_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": "llama-3.2-11b-vision-preview",
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "text": detection_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                            ]}],
                            "max_tokens": 200
                        }
                    )
                    if response.status_code == 200:
                        item_data = extract_json_from_response(
                            response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        )
                except:
                    pass

            if not item_data:
                item_data = {
                    "item_name": file.filename.rsplit(".", 1)[0].replace("_", " ").title(),
                    "category": "tops",
                    "color": "unknown",
                    "confidence": "low"
                }

            result = db.table("wardrobe").insert({
                "user_id": current_user_id,  # ← Scoped to user
                "item_name": item_data.get("item_name", "Unknown"),
                "image_url": image_url,
                "category": item_data.get("category"),
                "color": item_data.get("color"),
                "created_at": datetime.utcnow().isoformat()
            }).execute()

            if result.data:
                imported_items.append({
                    "id": result.data[0].get("id"),
                    "item_name": item_data.get("item_name"),
                    "category": item_data.get("category")
                })
        except Exception as e:
            errors.append(f"{file.filename}: {str(e)}")

    return {
        "status": "success",
        "imported_count": len(imported_items),
        "imported_items": imported_items,
        "errors": errors
    }

# ── PURCHASE VALIDATOR (Protected) ──
@app.post("/validate-purchase/")
async def validate_purchase(
    file: UploadFile = File(...),
    item_name: str = Form(...),
    price: Optional[str] = Form(None),
    occasion: Optional[str] = Form(None),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        wardrobe = db.table("wardrobe").select("*") \
            .eq("user_id", current_user_id).execute().data
        fits = db.table("fits").select("occasion, occasion_fit") \
            .eq("user_id", current_user_id).limit(20).execute().data

        content = await file.read()
        img = Image.open(BytesIO(content))
        img.thumbnail((512, 512))
        output = BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format="JPEG", quality=80)
        output.seek(0)
        b64_image = base64.b64encode(output.getvalue()).decode('utf-8')

        wardrobe_summary = {}
        for item in wardrobe:
            cat = item.get("category", "other")
            wardrobe_summary[cat] = wardrobe_summary.get(cat, 0) + 1

        occasion_list = list(set(f.get("occasion", "") for f in fits if f.get("occasion")))
        avg_match = sum(f.get("occasion_fit", 0) for f in fits) / len(fits) if fits else 0

        validation_prompt = f"Fashion investment advisor. Wardrobe: {json.dumps(wardrobe_summary)}. Occasions: {occasion_list}. Avg match: {avg_match:.0f}%. Item: {item_name}. Occasion: {occasion or 'N/A'}. Return ONLY JSON: {{\"verdict\": \"<BUY IT / THINK TWICE / SKIP IT>\", \"score\": <1-100>, \"why\": \"<2-3 sentences>\", \"gaps_filled\": [], \"alternatives\": [], \"styling_tips\": \"<how to style>\"}}"

        response = None
        if GROQ_KEY:
            try:
                response = await http_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.2-11b-vision-preview",
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": validation_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                        ]}],
                        "max_tokens": 500
                    }
                )
            except:
                pass
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
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": validation_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                        ]}],
                        "max_tokens": 500
                    }
                )
            except:
                pass
        if not response or response.status_code != 200:
            raise HTTPException(status_code=503, detail="AI service unavailable")

        data = response.json()
        content_str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        validation = extract_json_from_response(content_str) or {
            "verdict": "THINK TWICE", "score": 50, "why": "Unable to analyze.",
            "gaps_filled": [], "alternatives": [], "styling_tips": "Try it on first!"
        }
        return {
            "status": "success",
            "validation": validation,
            "wardrobe_context": {"total_items": len(wardrobe), "category_breakdown": wardrobe_summary}
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Validation failed")
        raise HTTPException(status_code=500, detail="Validation failed")

# ── OUTFIT CALENDAR (Protected) ──
@app.post("/calendar/plan")
async def plan_calendar_outfit(
    plan: CalendarPlan,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        existing = db.table("outfit_calendar").select("*") \
            .eq("planned_date", plan.date) \
            .eq("user_id", current_user_id).execute()
        if existing.data:
            result = db.table("outfit_calendar").update({
                "occasion": plan.occasion,
                "fit_id": plan.fit_id,
                "notes": plan.notes
            }).eq("planned_date", plan.date).eq("user_id", current_user_id).execute()
        else:
            result = db.table("outfit_calendar").insert({
                "user_id": current_user_id,
                "planned_date": plan.date,
                "occasion": plan.occasion,
                "fit_id": plan.fit_id,
                "notes": plan.notes,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
        return {
            "status": "success",
            "message": f"Planned for {plan.date}",
            "entry": result.data[0] if result.data else None
        }
    except Exception as e:
        logger.error(f"Calendar plan failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to plan outfit")

@app.get("/calendar")
async def get_calendar(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        if not start_date:
            start_date = datetime.utcnow().strftime("%Y-%m-01")
        if not end_date:
            end_date = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")
        result = db.table("outfit_calendar").select("*, fits(*)") \
            .eq("user_id", current_user_id) \
            .gte("planned_date", start_date) \
            .lte("planned_date", end_date) \
            .order("planned_date").execute()
        return {"status": "success", "start_date": start_date, "end_date": end_date, "entries": result.data}
    except Exception as e:
        logger.error(f"Calendar fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch calendar")

@app.delete("/calendar/{entry_id}")
async def delete_calendar_entry(
    entry_id: str,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        db.table("outfit_calendar").delete() \
            .eq("id", entry_id) \
            .eq("user_id", current_user_id).execute()
        return {"status": "success", "message": "Deleted"}
    except Exception as e:
        logger.error(f"Calendar delete failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete entry")

# ── STYLE QUIZ ──
STYLE_QUIZ_QUESTIONS = [
    {"id": "q1", "question": "Weekend outfit?", "options": [
        {"value": "casual", "label": "Jeans & tee"},
        {"value": "sporty", "label": "Athletic wear"},
        {"value": "polished", "label": "Put-together"},
        {"value": "bold", "label": "Statement pieces"}
    ]},
    {"id": "q2", "question": "Pick palette:", "options": [
        {"value": "neutral", "label": "Black/white/beige"},
        {"value": "earth", "label": "Browns/greens"},
        {"value": "vibrant", "label": "Brights/neons"},
        {"value": "pastel", "label": "Soft pinks/blues"}
    ]},
    {"id": "q3", "question": "Dream store?", "options": [
        {"value": "minimalist", "label": "Scandinavian boutique"},
        {"value": "trendy", "label": "Streetwear store"},
        {"value": "vintage", "label": "Thrift markets"},
        {"value": "luxury", "label": "Designer flagship"}
    ]},
    {"id": "q4", "question": "What matters most?", "options": [
        {"value": "comfort", "label": "Comfort is king"},
        {"value": "style", "label": "Looking my best"},
        {"value": "unique", "label": "Standing out"},
        {"value": "versatile", "label": "Anywhere wear"}
    ]},
    {"id": "q5", "question": "Accessories?", "options": [
        {"value": "minimal", "label": "Keep it simple"},
        {"value": "statement", "label": "More the better"},
        {"value": "functional", "label": "Only useful"},
        {"value": "classic", "label": "Timeless pieces"}
    ]}
]

@app.get("/style-quiz/questions")
async def get_style_quiz_questions():
    return {"status": "success", "total_questions": len(STYLE_QUIZ_QUESTIONS), "questions": STYLE_QUIZ_QUESTIONS}

@app.post("/style-quiz/result")
async def calculate_style_quiz_result(
    quiz_result: QuizResult,
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        answer_counts = {}
        for answer in quiz_result.answers:
            value = answer.answer.lower()
            answer_counts[value] = answer_counts.get(value, 0) + 1
        dominant_style = max(answer_counts, key=answer_counts.get) if answer_counts else "casual"

        profiles = {
            "casual": {"style_name": "Effortlessly Chill", "description": "Laid-back cool.", "keywords": ["Quality basics", "Well-fitted jeans"], "icons": ["Ryan Gosling"]},
            "sporty": {"style_name": "Athletic Edge", "description": "Comfort meets performance.", "keywords": ["Performance fabrics", "Sneakers"], "icons": ["David Beckham"]},
            "polished": {"style_name": "Polished Professional", "description": "Clean lines.", "keywords": ["Tailored blazers", "Classic shirts"], "icons": ["Amal Clooney"]},
            "bold": {"style_name": "Bold Statement", "description": "Stand out.", "keywords": ["Statement jackets", "Bold prints"], "icons": ["Billy Porter"]},
            "minimalist": {"style_name": "Minimalist Maven", "description": "Less is more.", "keywords": ["White shirts", "Black trousers"], "icons": ["Victoria Beckham"]},
            "vintage": {"style_name": "Vintage Soul", "description": "Love history.", "keywords": ["Vintage denim", "Retro jackets"], "icons": ["Alexa Chung"]},
            "neutral": {"style_name": "Neutral Nation", "description": "Warm tones.", "keywords": ["Beige coats", "Tan accessories"], "icons": ["Kim Kardashian"]},
            "earth": {"style_name": "Earth Child", "description": "Nature-inspired.", "keywords": ["Terracotta", "Olive greens"], "icons": ["Bella Hadid"]},
            "vibrant": {"style_name": "Color Enthusiast", "description": "Bright colors.", "keywords": ["Colorful tops", "Fun patterns"], "icons": ["Cardi B"]}
        }
        profile = profiles.get(dominant_style, profiles["casual"])

        if db:
            try:
                db.table("style_quiz_results").insert({
                    "user_id": current_user_id,
                    "answers": [{"question_id": a.question_id, "answer": a.answer} for a in quiz_result.answers],
                    "style_type": profile["style_name"],
                    "description": profile["description"],
                    "keywords": profile["keywords"],
                    "icons": profile["icons"],
                    "created_at": datetime.utcnow().isoformat()
                }).execute()
            except:
                pass

        return {
            "status": "success",
            "style_name": profile["style_name"],
            "dominant_style": dominant_style,
            "description": profile["description"],
            "keywords": profile["keywords"],
            "avoid": "Boring basics",
            "style_icons": profile["icons"],
            "answer_breakdown": answer_counts
        }
    except Exception as e:
        logger.error(f"Style quiz failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to calculate style result")

# ── GAP ANALYSIS (Protected — needs user wardrobe) ──
@app.get("/gap-analysis")
async def get_gap_analysis(current_user_id: str = Depends(get_current_user_id)):  # 🔐
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        wardrobe = db.table("wardrobe").select("*") \
            .eq("user_id", current_user_id).execute().data

        essential_categories = {
            "tops": {"min": 5, "importance": "high"},
            "bottoms": {"min": 3, "importance": "high"},
            "footwear": {"min": 2, "importance": "medium"},
            "outerwear": {"min": 1, "importance": "medium"},
            "accessories": {"min": 2, "importance": "low"}
        }

        category_counts = {}
        for item in wardrobe:
            cat = item.get("category", "other").lower()
            category_counts[cat] = category_counts.get(cat, 0) + 1

        gaps = []
        for cat, req in essential_categories.items():
            current = category_counts.get(cat, 0)
            if current < req["min"]:
                gaps.append({
                    "category": cat,
                    "current": current,
                    "recommended": req["min"],
                    "importance": req["importance"],
                    "suggestion": f"Add {req['min'] - current} more {cat}s."
                })

        total_essential = sum(r["min"] for r in essential_categories.values())
        current_essential = sum(
            min(category_counts.get(cat, 0), req["min"])
            for cat, req in essential_categories.items()
        )
        completeness = round((current_essential / total_essential) * 100) if total_essential > 0 else 0

        one_tip = "Build your wardrobe with versatile basics" if len(wardrobe) < 10 else \
                  "Focus on occasion-appropriate pieces" if gaps else \
                  "Wardrobe is well-balanced!"

        strengths = [f"Good variety in {cat}" for cat, count in category_counts.items() if count >= 3]

        return {
            "status": "success",
            "total_items": len(wardrobe),
            "category_counts": category_counts,
            "completeness_score": completeness,
            "gaps": gaps,
            "one_purchase_tip": one_tip,
            "strengths": strengths
        }
    except Exception as e:
        logger.error(f"Gap analysis failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze wardrobe")

# ── STYLE PROFILE (Protected) ──
@app.get("/style-profile")
async def get_style_profile(current_user_id: str = Depends(get_current_user_id)):  # 🔐
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        fits = db.table("fits").select("*") \
            .eq("user_id", current_user_id) \
            .order("created_at", desc=True).limit(50).execute().data
        wardrobe = db.table("wardrobe").select("*") \
            .eq("user_id", current_user_id).execute().data

        if not fits:
            return {"status": "success", "profile": None, "message": "Analyze some outfits first!"}

        occasions = [f.get("occasion") for f in fits if f.get("occasion")]
        occasion_counts = {}
        for occ in occasions:
            occasion_counts[occ] = occasion_counts.get(occ, 0) + 1
        preferred_occasions = sorted(
            [{"occasion": k, "count": v} for k, v in occasion_counts.items()],
            key=lambda x: x["count"], reverse=True
        )[:3]

        colors = [item.get("color") for item in wardrobe if item.get("color")]
        color_counts = {}
        for color in colors:
            color_counts[color] = color_counts.get(color, 0) + 1
        preferred_colors = sorted(
            [{"color": k, "count": v} for k, v in color_counts.items()],
            key=lambda x: x["count"], reverse=True
        )[:5]

        avg_score = sum(f.get("occasion_fit", 0) for f in fits) / len(fits) if fits else 0
        persona = "Style Icon" if avg_score >= 80 else "Fashion Forward" if avg_score >= 65 else "Trend Explorer" if avg_score >= 50 else "Style Student"

        recommendations = []
        if preferred_occasions:
            recommendations.append(f"You shine at {preferred_occasions[0]['occasion']} events")
        if len(wardrobe) < 10:
            recommendations.append("Build wardrobe with versatile basics")
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
        raise HTTPException(status_code=500, detail="Failed to generate profile")

# ── DRESS ME (Protected) ──
@app.post("/dress-me/")
async def dress_me_from_closet(
    occasion: str = Form(...),
    vibe_goal: Optional[str] = Form(None),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        wardrobe = db.table("wardrobe").select("*") \
            .eq("user_id", current_user_id).execute().data

        if len(wardrobe) < 3:
            return {
                "status": "success",
                "message": "Add more items first!",
                "outfits": [],
                "wardrobe_count": len(wardrobe)
            }

        by_category = {}
        for item in wardrobe:
            cat = item.get("category", "other").lower()
            by_category.setdefault(cat, []).append(item)

        outfits = []
        tops = by_category.get("tops", [])
        bottoms = by_category.get("bottoms", [])
        footwear = by_category.get("footwear", [])

        for i in range(min(3, len(tops), len(bottoms) if bottoms else 1)):
            outfit = {
                "name": f"Look {i+1}",
                "items": [],
                "why_it_works": f"Works great for {occasion}",
                "style_rating": 7 + i
            }
            if tops: outfit["items"].append(tops[i % len(tops)]["item_name"])
            if bottoms: outfit["items"].append(bottoms[i % len(bottoms)]["item_name"])
            if footwear: outfit["items"].append(footwear[i % len(footwear)]["item_name"])
            if by_category.get("outerwear"):
                outfit["items"].append(by_category["outerwear"][0]["item_name"])
            outfits.append(outfit)

        missing = []
        if not by_category.get("tops"): missing.append("Add tops")
        if not by_category.get("bottoms"): missing.append("Add bottoms")
        if not by_category.get("footwear"): missing.append("Add footwear")

        return {
            "status": "success",
            "outfits": outfits,
            "wardrobe_count": len(wardrobe),
            "missing_item": missing[0] if missing else None,
            "tip": f"For {occasion}, consider the vibe you want to project"
        }
    except Exception as e:
        logger.error(f"Dress me failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate suggestions")

# ── VIRTUAL TRY-ON (Protected) ──
@app.post("/virtual-try-on")
async def virtual_try_on(
    item_id: str = Form(...),
    style_note: Optional[str] = Form(None),
    current_user_id: str = Depends(get_current_user_id)  # 🔐
):
    if not db:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        result = db.table("wardrobe").select("*") \
            .eq("id", item_id) \
            .eq("user_id", current_user_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Item not found")
        item = result.data[0]
        return {
            "status": "success",
            "message": "Virtual try-on generated!",
            "item_name": item.get("item_name"),
            "image_url": item.get("image_url"),
            "style_note": style_note,
            "note": "Full integration requires image generation API"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Virtual try-on failed: {e}")
        raise HTTPException(status_code=500, detail="Virtual try-on failed")

# ============================================================
# ERROR HANDLERS
# ============================================================
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

# ============================================================
# ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
