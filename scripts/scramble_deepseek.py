import asyncio
import time
import os
import sqlite3
import json
import random
import httpx
from dotenv import load_dotenv

# Load env variables for the API key if present
load_dotenv()

# CONFIGURATION - OpenAI-style Chat Completions API
BASE_URL = "https://api.daiju.live/v1/chat/completions"
# Using the key from original script if it works for the new provider,
# or fall back to OPENAI_API_KEY from .env
API_KEY = os.getenv("scriptkey", "")
MODEL_NAME = "deepseek-v3.2"

# DATABASE PATH
DB_PATH = "economy.db"

async def generate_and_save_words(count=60):
    print("--- JenBot Scramble Pregenerator (Streaming DeepSeek Style) ---")
    print(f"🔗 Target Endpoint: {BASE_URL}")
    print(f"🤖 Target Model: {MODEL_NAME}")
    
    categories = [
        "Space Exploration", "Deep Sea Creatures", "Medieval Weapons", "Cyberpunk Cities", 
        "Ancient Mythology", "Cooking Ingredients", "Fictional Magic Systems", "Types of Clouds",
        "Board Games", "Retro Video Games", "Musical Instruments", "Rare Gemstones",
        "Arctic Animals", "Famous Landmarks", "Modern Architecture", "Types of Cheese"
    ]
    
    selected_cats = random.sample(categories, 8)
    print(f"📊 Selected {len(selected_cats)} categories.")

    prompt = (
        f"Provide 6 interesting words each for these categories: {', '.join(selected_cats)}.\n"
        "Words should be 6-12 letters long.\n"
        "Return ONLY a JSON list of objects: "
        "[{\"original\": \"WORD\", \"scrambled\": \"DWRO\", \"category\": \"Theme\"}]"
    )

    # OpenAI-compatible Chat Completions Payload
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a word puzzle master. Output raw JSON list only. No intro or outro. Ensure the response is a valid JSON array."},
            {"role": "user", "content": prompt}
        ],
        "stream": True,
        "temperature": 0.7
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    print("\n📡 Requesting data via HTTPX Stream...")
    start_time = time.time()
    full_response_text = ""
    
    async with httpx.AsyncClient(verify=False) as client:
        try:
            async with client.stream("POST", BASE_URL, json=payload, headers=headers, timeout=60.0) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    print(f"❌ Error {response.status_code}: {error_text.decode()}")
                    return

                print("📦 Receiving stream: ", end="", flush=True)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_content = line[6:].strip()
                        if data_content == "[DONE]":
                            break
                        
                        try:
                            event_data = json.loads(data_content)
                            delta = event_data.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                text = delta["content"]
                                full_response_text += text
                                print(".", end="", flush=True) # visual progress
                        except json.JSONDecodeError:
                            continue
                
            print("\n✅ Stream Completed.")
            duration = time.time() - start_time
            print(f"⏱️ Duration: {duration:.2f}s")
            
            # Clean JSON
            response_text = full_response_text.strip()
            # Often AI models wrap JSON in markdown blocks
            if "```json" in response_text: 
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text: 
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            # Attempt to parse
            try:
                words_data = json.loads(response_text)
            except json.JSONDecodeError as e:
                print(f"❌ JSON Decode Error: {e}")
                print(f"Debug Raw Text: {response_text[:200]}...")
                return

            print(f"📦 Parsed {len(words_data)} words.")
            
            # Database Update
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS scramble_words (id INTEGER PRIMARY KEY AUTOINCREMENT, original TEXT, scrambled TEXT, category TEXT, status INTEGER DEFAULT 0)")
            
            newly_added = 0
            for item in words_data:
                orig = item.get('original', "").strip().upper()
                scram = item.get('scrambled', "").strip().upper()
                cat = item.get('category', "General")
                if orig and scram:
                    cursor.execute("SELECT id FROM scramble_words WHERE original = ?", (orig,))
                    if cursor.fetchone() is None:
                        cursor.execute("INSERT INTO scramble_words (original, scrambled, category, status) VALUES (?, ?, ?, 0)", 
                                       (orig, scram, cat))
                        newly_added += 1
            
            conn.commit()
            conn.close()
            print(f"✅ Successfully added {newly_added} new words to {DB_PATH}.")

        except Exception as e:
            print(f"\n❌ Operation failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(generate_and_save_words())
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
