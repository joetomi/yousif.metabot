import os
import json
import google.generativeai as genai
from models import db, Setting, MessengerFAQ, ProcessedMessage
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity

def process_messenger_job(app, msg_details):
    """
    Background worker executed by APScheduler to process incoming Messenger messages.
    """
    with app.app_context():
        user_id = msg_details.get("app_user_id")
        
        # 1. Verify Messenger Bot is enabled
        enabled = Setting.get("messenger_bot_enabled", "false", user_id=user_id)
        if str(enabled).lower() != "true":
            print("Messenger Bot is disabled. Skipping processing.")
            return

        sender_id = msg_details.get("sender_id")
        message_text = msg_details.get("message_text", "").strip()
        message_id = msg_details.get("message_id")
        
        if not sender_id or not message_id:
            print("Messenger message details are incomplete.")
            return

        # 2. Check for duplicate processing
        processed = ProcessedMessage.query.filter_by(message_id=message_id, admin_id=user_id).first()
        if processed:
            print(f"Messenger message {message_id} was already processed. Skipping.")
            return

        # Record message as processed immediately to prevent concurrency issues
        try:
            processed_msg = ProcessedMessage(message_id=message_id, admin_id=user_id)
            db.session.add(processed_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error saving processed message ID: {e}")
            return

        # Initialize Facebook API Service for this user
        token = Setting.get("page_access_token", user_id=user_id)
        page_id = Setting.get("page_id", user_id=user_id)
        api_service = FacebookApiService(page_access_token=token, page_id=page_id)

        # 3. Check for Keyword/FAQ matches
        faqs = MessengerFAQ.query.filter_by(is_active=True, admin_id=user_id).all()
        matched_response = None
        matched_keyword = None
        
        for faq in faqs:
            keywords = [k.strip().lower() for k in faq.keyword.split(",") if k.strip()]
            for kw in keywords:
                if kw in message_text.lower():
                    matched_response = faq.response
                    matched_keyword = kw
                    break
            if matched_response:
                break

        if matched_response:
            # Send FAQ response
            print(f"Matched FAQ keyword '{matched_keyword}'. Sending custom response.")
            success, msg = api_service.send_messenger_message(sender_id, matched_response)
            
            log_activity(
                event_type="MESSAGE",
                status="SUCCESS" if success else "FAILED",
                user_id=sender_id,
                comment_id=None,
                post_id=None,
                message=f"Messenger response via FAQ keyword '{matched_keyword}' trigger: {msg}",
                admin_id=user_id
            )
            return

        # 4. Fallback to Gemini AI with strict constraints and tone instructions
        gemini_api_key = Setting.get("gemini_api_key", "", user_id=user_id).strip()
        fallback_text = Setting.get("messenger_bot_fallback", "شكراً لتواصلك معنا. تم استلام رسالتك وسيقوم أحد ممثلي خدمة العملاء بالرد عليك قريباً.", user_id=user_id)
        
        if not gemini_api_key:
            # If Gemini key is missing, send the custom fallback response
            success, msg = api_service.send_messenger_message(sender_id, fallback_text)
            log_activity(
                event_type="MESSAGE",
                status="SUCCESS" if success else "FAILED",
                user_id=sender_id,
                message=f"Gemini API key missing. Dispatched fallback response: {msg}",
                admin_id=user_id
            )
            return

        # Define tone instructions
        tone_rules = {
            "casual": "تحدث بأسلوب ودي، عامي، بسيط وغير رسمي (كصديق يساعد صديقاً له).",
            "professional": "تحدث بأسلوب مهني، احترافي، محترم، واضح، ودقيق جداً.",
            "formal": "تحدث بأسلوب رسمي جداً، باللغة العربية الفصحى المنضبطة والمتحفظة.",
            "friendly": "تحدث بأسلوب ودود ولطيف للغاية، ترحيبي، ومتعاطف وبشوش."
        }
        
        tone = Setting.get("messenger_bot_tone", "professional", user_id=user_id).lower()
        tone_instruction = tone_rules.get(tone, tone_rules["professional"])
        
        knowledge_base = Setting.get("messenger_bot_kb", "", user_id=user_id).strip()
        if not knowledge_base:
            knowledge_base = "لا تتوفر معلومات تفصيلية عن الشركة حالياً."

        page_name = Setting.get("page_name", "يوسف بوت", user_id=user_id)

        # Build strict system prompt
        system_instruction = f"""
أنت مساعد خدمة عملاء ذكي ومحترف لـ "{page_name}".
مهمتك هي الإجابة على استفسار العميل بلباقة بناءً على معلومات الشركة والاشتراكات المتاحة المحددة هنا فقط:

--- معلومات الشركة والاشتراكات والخطط المتاحة ---
{knowledge_base}
----------------------------------------

أسلوب وطريقة الرد المطلوبة:
{tone_instruction}

قواعد صارمة جداً:
1. يجب عليك الإجابة حصراً واعتماداً على معلومات الشركة والاشتراكات المتاحة المذكورة أعلاه فقط.
2. لا تخترع أي اشتراكات، أسعار، عروض، أو معلومات غير موجودة في النص أعلاه كلياً.
3. إذا سألك العميل عن أي شيء غير موجود في النص المساعد (مثل مواضيع خارج نطاق العمل، أو أسعار غير مذكورة، أو أسئلة عامة)، أجب بلطف واعتذر بلباقة مخبراً إياه بأنك بوت الرد التلقائي، واطلب منه التفضل بترك استفساره وسيقوم موظف الدعم البشري بالتواصل معه والإجابة عليه في أقرب وقت.
4. أجب دائماً بنفس لغة العميل (العربية أو الإنجليزية).
5. حافظ على الردود قصيرة ومباشرة ومريحة للقراءة (بحد أقصى 2-3 جمل).
"""

        try:
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            prompt = f"""
Customer Query:
{message_text}

Reply instructions:
{system_instruction}
"""
            # Call Gemini
            response = model.generate_content(prompt)
            
            if response and response.text:
                reply_text = response.text.strip()
            else:
                reply_text = fallback_text
                
        except Exception as ex:
            print(f"Error calling Gemini in Messenger process: {ex}")
            reply_text = fallback_text

        # Send response via Messenger API
        success, msg = api_service.send_messenger_message(sender_id, reply_text)
        
        log_activity(
            event_type="MESSAGE",
            status="SUCCESS" if success else "FAILED",
            user_id=sender_id,
            comment_id=None,
            post_id=None,
            message=f"Messenger Response via Gemini (Tone: {tone}): {reply_text} | API Status: {msg}",
            admin_id=user_id
        )
