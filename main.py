import os, json, re, uuid, requests, base64
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
import uvicorn

load_dotenv()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

# The verified free Gemma 4 Vision model
ACTIVE_MODEL = "google/gemma-4-26b-a4b-it:free"

def extract_json(text):
    """Regex Guard: Extracts only valid JSON from AI conversational responses"""
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except: return None

@app.get("/", response_class=HTMLResponse)
async def serve_app():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.post("/analyze-fit/")
async def analyze_fit(file: UploadFile = File(...), occasion: str = Form(...), roast_mode: str = Form("false")):
    try:
        # 1. Image Processing & Cloud Storage
        img_bytes = await file.read()
        img = Image.open(BytesIO(img_bytes))
        img.thumbnail((1024, 1024))
        
        file_path = f"{uuid.uuid4()}.jpg"
        out_img = BytesIO()
        img.save(out_img, format="JPEG", quality=85)
        out_img.seek(0)
        
        db.storage.from_("outfits").upload(file_path, out_img.getvalue(), {"content-type": "image/jpeg"})
        image_url = db.storage.from_("outfits").get_public_url(file_path)

        # 2. AI handshake via OpenRouter
        is_roast = roast_mode.lower() == "true"
        personality = "a savage fashion critic" if is_roast else "a senior high-fashion consultant"
        b64_img = base64.b64encode(img_bytes).decode('utf-8')
        
        resp = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "HTTP-Referer": "https://flaunt.fit", "X-Title": "flaunt.fit"},
            data=json.dumps({
                "model": ACTIVE_MODEL,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": f"Act as {personality}. Analyze this {occasion} outfit. Return ONLY JSON: {{'color_harmony': {{'score': 0, 'reason': ''}}, 'occasion_fit': {{'score': 0, 'reason': ''}}, 'style_coherence': {{'score': 0, 'reason': ''}}, 'fit_proportion': {{'score': 0, 'reason': ''}}, 'trend_score': {{'score': 0, 'reason': ''}}, 'roast': '', 'the_fix': '', 'detected_piece': ''}}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]}]
            })
        )
        
        # --- SURGICAL FIX: OPENROUTER ERROR CATCHER ---
        resp_json = resp.json()
        
        if "error" in resp_json:
            error_msg = resp_json['error'].get('message', str(resp_json['error']))
            return {"status": "error", "message": f"OpenRouter API Error: {error_msg}"}
            
        if "choices" not in resp_json:
            return {"status": "error", "message": f"Unexpected Response: {resp_json}"}
        # ----------------------------------------------
        
        ai_data = extract_json(resp_json['choices'][0]['message']['content'])
        
        if not ai_data:
            return {"status": "error", "message": "AI did not return valid JSON data."}

        def gv(k): return float(ai_data.get(k, {}).get("score", 0))
        total = round((gv("color_harmony")*0.2 + gv("occasion_fit")*0.25 + gv("style_coherence")*0.2 + gv("fit_proportion")*0.2 + gv("trend_score")*0.15), 2)
        
        res = db.table("fits").insert({
            "occasion": occasion, "ai_scorecard": ai_data, "total_score": total,
            "image_url": str(image_url), "roast_text": ai_data.get("roast", "")
        }).execute()
        
        return {"status": "success", "final_score": total, "breakdown": ai_data, "image_url": str(image_url), "id": res.data[0]['id']}
    except Exception as e: return {"status": "error", "message": str(e)}

@app.get("/profile-dna")
async def get_profile_dna():
    fits = db.table("fits").select("occasion, total_score").execute().data
    if not fits: return {"dna": "Style Recruit", "level": "Novice", "avg_score": 0.0, "total_fits": 0}
    avg = sum([float(f['total_score']) for f in fits]) / len(fits)
    top_occ = max(set([f['occasion'] for f in fits]), key=[f['occasion'] for f in fits].count)
    return {"dna": f"{top_occ} Pro", "level": "Icon" if avg > 8.5 else "Stylist", "avg_score": round(avg, 1), "total_fits": len(fits)}

@app.post("/add-to-closet/")
async def add_to_closet(item_name: str = Form(...), image_url: str = Form(...)):
    return db.table("wardrobe").insert({"item_name": item_name, "image_url": image_url}).execute()

@app.get("/closet")
async def get_closet(): return db.table("wardrobe").select("*").order("created_at", desc=True).execute().data

@app.get("/community")
async def get_community(): return db.table("fits").select("*").eq("is_public", True).order("likes_count", desc=True).execute().data

@app.get("/history")
async def get_history(): return db.table("fits").select("*").order("created_at", desc=True).execute().data

@app.post("/toggle-public/{fit_id}")
async def toggle_public(fit_id: str, status: bool): return db.table("fits").update({"is_public": status}).eq("id", fit_id).execute()

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)