import os
import json
import requests
from datetime import datetime, timedelta
import google.generativeai as genai
from models import db, Setting, MessengerFAQ, ProcessedMessage, WhatsAppChatHistory
from services.comment_processor import log_activity

def send_whatsapp_message(access_token, phone_number_id, recipient_id, text):
    """Sends a text message using WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_id,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text
        }
    }
    try:
        res = requests.post(url, headers=headers, json=payload)
        res_data = res.json()
        if 'error' in res_data:
            return False, res_data['error'].get('message', 'Unknown error')
        return True, "SUCCESS"
    except Exception as e:
        return False, str(e)

def process_whatsapp_message_job(app, msg_details):
    """Processes incoming messages from WhatsApp."""
    with app.app_context():
        user_id = msg_details.get("app_user_id")
        
        bot_enabled = Setting.get("whatsapp_bot_enabled", "false", user_id=user_id)
        if str(bot_enabled).lower() != "true":
            print("WhatsApp Bot is disabled. Skipping processing.")
            return

        sender_id = msg_details.get("sender_id")  # User phone number or wa_id
        message_text = msg_details.get("message_text", "").strip()
        message_id = msg_details.get("message_id")
        
        if not sender_id or not message_id:
            print("WhatsApp message details are incomplete.")
            return

        # Duplicate checking
        processed = ProcessedMessage.query.filter_by(message_id=message_id, admin_id=user_id).first()
        if processed:
            print(f"WhatsApp message {message_id} already processed. Skipping.")
            return

        try:
            processed_msg = ProcessedMessage(message_id=message_id, admin_id=user_id)
            db.session.add(processed_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error saving processed WhatsApp message ID: {e}")
            return

        # Spam/Rate Limit check (5 messages in 30s)
        is_spam = False
        limit_time = datetime.utcnow() - timedelta(seconds=30)
        customer_msgs_count = WhatsAppChatHistory.query.filter(
            WhatsAppChatHistory.sender_id == sender_id,
            WhatsAppChatHistory.admin_id == user_id,
            WhatsAppChatHistory.is_from_customer == True,
            WhatsAppChatHistory.created_at >= limit_time
        ).count()
        
        if customer_msgs_count >= 5:
            is_spam = True
            print(f"Spam detected from WhatsApp customer {sender_id}.")

        # Log customer message to history
        try:
            cust_msg = WhatsAppChatHistory(
                sender_id=sender_id,
                message_content=message_text,
                is_from_customer=True,
                admin_id=user_id
            )
            db.session.add(cust_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error logging WhatsApp customer message: {e}")

        token = Setting.get("whatsapp_access_token", user_id=user_id)
        phone_number_id = Setting.get("whatsapp_phone_number_id", user_id=user_id)
        fallback_text = Setting.get("whatsapp_bot_fallback", "شكراً لتواصلك معنا. سنقوم بالرد عليك قريباً عبر واتساب.", user_id=user_id)

        if is_spam:
            success, msg = send_whatsapp_message(token, phone_number_id, sender_id, fallback_text)
            try:
                bot_msg = WhatsAppChatHistory(
                    sender_id=sender_id,
                    message_content=fallback_text,
                    is_from_customer=False,
                    admin_id=user_id
                )
                db.session.add(bot_msg)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
            return

        # Check for FAQs
        faqs = MessengerFAQ.query.filter_by(is_active=True, admin_id=user_id).all()
        matched_response = None
        for faq in faqs:
            keywords = [k.strip().lower() for k in faq.keyword.split(",") if k.strip()]
            for kw in keywords:
                if kw in message_text.lower():
                    matched_response = faq.response
                    break
            if matched_response:
                break

        if matched_response:
            success, msg = send_whatsapp_message(token, phone_number_id, sender_id, matched_response)
            try:
                bot_msg = WhatsAppChatHistory(
                    sender_id=sender_id,
                    message_content=matched_response,
                    is_from_customer=False,
                    admin_id=user_id
                )
                db.session.add(bot_msg)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
            return

        # Fetch conversation history context
        history_records = WhatsAppChatHistory.query.filter_by(
            sender_id=sender_id, admin_id=user_id
        ).order_by(WhatsAppChatHistory.created_at.desc()).offset(1).limit(10).all()
        history_records.reverse()
        
        history_text = ""
        if history_records:
            for record in history_records:
                role = "Customer" if record.is_from_customer else "Bot"
                history_text += f"\n[{role}]: {record.message_content}"

        gemini_api_key = Setting.get("whatsapp_gemini_api_key", "", user_id=user_id).strip()
        gemini_enabled = Setting.get("whatsapp_gemini_enabled", "false", user_id=user_id)
        
        if str(gemini_enabled).lower() != "true" or not gemini_api_key:
            success, msg = send_whatsapp_message(token, phone_number_id, sender_id, fallback_text)
            try:
                bot_msg = WhatsAppChatHistory(
                    sender_id=sender_id,
                    message_content=fallback_text,
                    is_from_customer=False,
                    admin_id=user_id
                )
                db.session.add(bot_msg)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
            return

        tone = Setting.get("whatsapp_bot_tone", "professional", user_id=user_id).lower()
        tone_rules = {
            "casual": "تحدث بأسلوب ودي، عامي، بسيط وغير رسمي (كصديق يساعد صديقاً له).",
            "professional": "تحدث بأسلوب مهني، احترافي، محترم، واضح، ودقيق جداً.",
            "formal": "تحدث بأسلوب رسمي جداً، باللغة العربية الفصحى المنضبطة والمتحفظة.",
            "friendly": "تحدث بأسلوب ودود ولطيف للغاية، ترحيبي، ومتعاطف وبشوش."
        }
        tone_instruction = tone_rules.get(tone, tone_rules["professional"])
        knowledge_base = Setting.get("whatsapp_bot_kb", "", user_id=user_id).strip()
        page_name = Setting.get("page_name", "يوسف بوت", user_id=user_id)

        system_instruction = f"""
أنت مساعد خدمة عملاء ذكي ومحترف لحساب الواتساب لـ "{page_name}".
مهمتك هي الإجابة على استفسار العميل بلباقة بناءً على معلومات الشركة والاشتراكات المتاحة المحددة هنا فقط:

--- معلومات الشركة والاشتراكات والخطط المتاحة ---
{knowledge_base}
----------------------------------------

أسلوب وطريقة الرد المطلوبة:
{tone_instruction}

قواعد صارمة جداً:
1. يجب عليك الإجابة حصراً واعتماداً على معلومات الشركة والاشتراكات المتاحة المذكورة أعلاه فقط.
2. لا تخترع أي اشتراكات، أسعار، عروض، أو معلومات غير موجودة في النص أعلاه كلياً.
3. إذا سألك العميل عن أي شيء غير موجود في النص المساعد، أعتذر بلطف واطلب منه ترك استفساره وسيرد عليه موظف بشري قريباً.
4. أجب دائماً بنفس لغة العميل.
5. حافظ على الردود قصيرة ومباشرة ومريحة للقراءة.
6. راجع محادثتك السابقة مع هذا العميل المذكورة أدناه لفهم السياق.
"""

        try:
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            prompt = f"""
Conversation History with this Customer (Phone: {sender_id}):
{history_text or "No previous messages."}

Current Customer Query:
{message_text}

Reply instructions:
{system_instruction}
"""
            response = model.generate_content(prompt)
            reply_text = response.text.strip() if response and response.text else fallback_text
        except Exception as e:
            print(f"Gemini call failed for WhatsApp: {e}")
            reply_text = fallback_text

        # Send message
        success, msg = send_whatsapp_message(token, phone_number_id, sender_id, reply_text)
        
        # Log bot message
        try:
            bot_msg = WhatsAppChatHistory(
                sender_id=sender_id,
                message_content=reply_text,
                is_from_customer=False,
                admin_id=user_id
            )
            db.session.add(bot_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()

        log_activity(
            event_type="MESSAGE",
            status="SUCCESS" if success else "FAILED",
            user_id=sender_id,
            message=f"WhatsApp response via Gemini: {reply_text}",
            admin_id=user_id
        )
