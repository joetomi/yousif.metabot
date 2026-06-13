import os
import json
import google.generativeai as genai
from models import Setting

def generate_ai_replies(comment_text, customer_name, post_text=None, user_id=None):
    """
    Calls Google Gemini API (gemini-1.5-flash) to generate:
      1. A public comment reply.
      2. A private inbox message.
    Returns (public_reply, private_reply) or None if disabled/failed.
    """
    # 1. Check if Gemini is enabled and key is present
    enabled = Setting.get("gemini_enabled", "false", user_id=user_id)
    if str(enabled).lower() != "true":
        return None
        
    api_key = Setting.get("gemini_api_key", "", user_id=user_id)
    if not api_key:
        print("Gemini API is enabled but GEMINI_API_KEY is empty.")
        return None

    system_instruction = Setting.get(
        "gemini_system_instruction", 
        "أنت مساعد ذكي ولطيف، أجب على استفسار العميل باحترافية واختصار.",
        user_id=user_id
    ).strip()

    try:
        # 2. Configure Google GenAI
        genai.configure(api_key=api_key)
        
        # Configure JSON response output schema
        generation_config = {
            "response_mime_type": "application/json",
            "temperature": 0.7
        }
        
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        # 3. Formulate Prompt
        prompt = f"""
        You are an automated assistant. Your task is to reply to a Facebook comment and prepare a private message based on the customer's comment.
        
        Post Content (Context):
        {post_text or "No specific post context"}
        
        Customer Name:
        {customer_name}
        
        Customer Comment:
        {comment_text}
        
        Instructions to follow:
        {system_instruction}
        
        Please reply in the user's language (matching their tone and intent).
        Output your response strictly as a JSON object with exactly two keys:
        - "public_reply": A short, friendly comment response (max 1-2 sentences).
        - "private_reply": A helpful and detailed private message response.
        """
        
        # 4. Request generation
        response = model.generate_content(prompt, generation_config=generation_config)
        
        if not response or not response.text:
            print("Gemini API returned an empty response.")
            return None
            
        data = json.loads(response.text.strip())
        public_reply = data.get("public_reply", "").strip()
        private_reply = data.get("private_reply", "").strip()
        
        if not public_reply or not private_reply:
            print(f"Gemini output is missing fields: {response.text}")
            return None
            
        return public_reply, private_reply
        
    except Exception as e:
        print(f"Error generating Gemini replies: {e}")
        return None
