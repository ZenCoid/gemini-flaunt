import base64
import time
from openai import OpenAI

# 1. PASTE YOUR OPENROUTER API KEY HERE
YOUR_API_KEY = "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"

def event_context_engine(image_path, occasion, vibe):
    print(f"Loading Flaunt.Fit Context Engine...")
    print(f"Target Event: {occasion} | Desired Vibe: {vibe}\n")
    
    # Initialize OpenRouter client
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=YOUR_API_KEY,
    )
    
    # The new "Don't Rate, Match" Prompt
    prompt = f"""
    You are the lead stylist and Event Context Engine for Flaunt.Fit. 
    Your job is NOT to rate an outfit out of 10. That is a dead concept.
    Your job is to answer: "Is this outfit appropriate for where the user is going?"
    
    You have deep expertise in both Western streetwear/formal wear AND South Asian fashion (shalwar kameez, sherwanis, kurtas, dholki/nikah protocols, modesty gradients, and local color symbolism).

    User's Event: {occasion}
    User's Desired Vibe: {vibe}
    
    Analyze the uploaded photo against the event and vibe. Return your response EXACTLY in this format:
    1. Occasion Match: [Give a Percentage %] - [1 sentence explaining why it fits or misses]
    2. Color Harmony: [Brief analysis of the palette and if it suits the event]
    3. Formality Calibration: [Are they overdressed, underdressed, or perfectly calibrated?]
    4. The Fix: [One concrete, actionable item swap or styling tweak to nail the desired vibe. If perfect, say "Lock it in."]
    """
    
    try:
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        print(f"Analyzing image: {image_path}...\n")
        
        # Robust Retry Logic using Gemma 3 12B Vision
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model="google/gemma-3-12b-it:free",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{base64_image}"
                                    }
                                }
                            ]
                        }
                    ]
                )
                
                print("=== FLAUNT.FIT EVENT SCORECARD ===")
                print(response.choices[0].message.content)
                print("==================================")
                break 
                
            except Exception as api_error:
                if "429" in str(api_error) or "rate-limited" in str(api_error):
                    print(f"Server traffic. Retrying in 5 seconds... (Attempt {attempt + 1} of {max_retries})")
                    time.sleep(5)
                else:
                    print(f"API Error: {api_error}")
                    break
                    
    except FileNotFoundError:
        print(f"Error: Could not find '{image_path}'.")
    except Exception as e:
        print(f"System Error: {e}")

# 2. RUN THE TEST WITH OCCASION DATA
test_photo_name = "test_photo.jpg" 
target_occasion = "A friend's Dholki in Lahore"
target_vibe = "Traditional but relaxed, approachable"

event_context_engine(test_photo_name, target_occasion, target_vibe)