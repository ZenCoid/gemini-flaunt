# FLAUNT.FIT

<p align="center">
  <img src="logo.png" alt="FLAUNT.FIT Logo" width="200">
</p>

> **"Don't Rate. Match."** - AI Stylist that matches outfits to occasions, not arbitrary scores.

## Philosophy

This isn't a "rate my outfit 1-10" app. That's a dead concept.

FLAUNT answers one question: **"Is this the right version of you for where you're going?"**

### Core Principles
1. **Occasion Match** - Percentage score (0-100%) based on context
2. **Vibe Goal** - User defines their desired energy
3. **The Fix** - ONE specific, actionable improvement
4. **Cultural Awareness** - South Asian fashion, modesty gradients, local context

## Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/yourusername/flaunt-app.git
cd flaunt-app
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Set Up Supabase

Create these tables in your Supabase dashboard:

**Table: `fits`**
```sql
CREATE TABLE fits (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    occasion TEXT NOT NULL,
    occasion_match INTEGER DEFAULT 0,
    color_harmony TEXT,
    formality_calibration TEXT,
    the_fix TEXT,
    items_spotted JSONB DEFAULT '[]',
    vibe_check TEXT,
    confidence TEXT DEFAULT 'Medium',
    vibe_goal TEXT,
    roast_text TEXT,
    image_url TEXT,
    is_public BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Table: `wardrobe`**
```sql
CREATE TABLE wardrobe (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    item_name TEXT NOT NULL,
    image_url TEXT,
    category TEXT,
    color TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**⚠️ If tables already exist, run these to add missing columns:**
```sql
-- Add roast_text column if missing
ALTER TABLE fits ADD COLUMN IF NOT EXISTS roast_text TEXT;
```

**Storage Bucket: `outfits`**
- Create a public bucket named `outfits` in Supabase Storage

### 4. Run
```bash
python main.py
```

Open http://localhost:8000 in your browser.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve the app |
| `/health` | GET | Health check |
| `/analyze-fit/` | POST | Analyze an outfit |
| `/history` | GET | Get analysis history |
| `/profile-dna` | GET | Get user's style DNA |
| `/closet` | GET | Get closet items |
| `/add-to-closet/` | POST | Add item to closet |
| `/community` | GET | Get public feed |

## Tech Stack

- **Backend**: FastAPI + Python
- **Frontend**: Vanilla JS + Tailwind CSS
- **AI**: OpenRouter (Gemma 3 12B Vision - FREE)
- **Database**: Supabase (PostgreSQL + Storage)

## Features

### Implemented
- ✅ Occasion-based outfit matching
- ✅ Vibe goal input
- ✅ Percentage scoring (not 1-10)
- ✅ Color harmony analysis
- ✅ Formality calibration
- ✅ One actionable fix
- ✅ Items spotted detection
- ✅ Virtual closet
- ✅ Style DNA profile
- ✅ Community feed
- ✅ Mobile-first responsive design

### Roadmap
- 🔜 "Dress me from closet" - AI generates outfits from your wardrobe
- 🔜 Bulk closet scan - Pan camera across closet, auto-tag items
- 🔜 Gap analysis - "You need X to complete Y outfits"
- 🔜 Shopping validator - "This jacket works with 3 things you own"
- 🔜 Morning notifications - Daily outfit suggestions

## Cultural Context

The AI has been trained with awareness of:
- **South Asian events**: Dholki, Mehndi, Nikah, Walima
- **Dress codes**: Shalwar kameez, sherwani, kurta formality levels
- **Modesty gradients**: Event-appropriate coverage
- **Color symbolism**: Wedding colors, funeral protocols, regional preferences

## Security Notes

- API keys are stored in `.env` (never commit this file)
- Images are processed and stored securely in Supabase
- No personal data is stored beyond outfit images
- `.env.example` is provided for setup reference

## License

MIT License - Build something cool.

---

Made with intention, not just AI prompts.