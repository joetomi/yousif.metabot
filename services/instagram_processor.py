import os
import json
import requests
from datetime import datetime, timedelta
import google.generativeai as genai
from models import db, Setting, MessengerFAQ, ProcessedMessage, InstagramChatHistory, Post, Comment, Message
from services.comment_processor import log_activity, check_anti_spam, record_processed_user, trigger_dashboard_update
from services.facebook_api import FacebookApiService

def send_instagram_message(page_access_token, recipient_id, text):
    """Sends a direct message to a user on Instagram."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": page_access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    try:
        res = requests.post(url, params=params, json=payload)
        res_data = res.json()
        if 'error' in res_data:
            return False, res_data['error'].get('message', 'Unknown error')
        return True, "SUCCESS"
    except Exception as e:
        return False, str(e)

def send_instagram_comment_reply(page_access_token, comment_id, text):
    """Replies publicly to an Instagram comment."""
    url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
    params = {"access_token": page_access_token}
    payload = {"message": text}
    try:
        res = requests.post(url, params=params, json=payload)
        res_data = res.json()
        if 'error' in res_data:
            return False, res_data['error'].get('message', 'Unknown error')
        return True, "SUCCESS"
    except Exception as e:
        return False, str(e)

def send_instagram_private_reply(page_access_token, comment_id, text):
    """Sends a private message (private reply) to an Instagram commenter."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": page_access_token}
    payload = {
        "recipient": {"comment_id": comment_id},
        "message": {"text": text}
    }
    try:
        res = requests.post(url, params=params, json=payload)
        res_data = res.json()
        if 'error' in res_data:
            return False, res_data['error'].get('message', 'Unknown error')
        return True, "SUCCESS"
    except Exception as e:
        return False, str(e)

def process_instagram_message_job(app, msg_details):
    """Processes incoming direct messages on Instagram."""
    with app.app_context():
        user_id = msg_details.get("app_user_id")
        
        bot_enabled = Setting.get("instagram_bot_enabled", "false", user_id=user_id)
        if str(bot_enabled).lower() != "true":
            print("Instagram Bot is disabled. Skipping processing.")
            return

        sender_id = msg_details.get("sender_id")
        message_text = msg_details.get("message_text", "").strip()
        message_id = msg_details.get("message_id")
        
        if not sender_id or not message_id:
            print("Instagram message details are incomplete.")
            return

        # Duplicate checking
        processed = ProcessedMessage.query.filter_by(message_id=message_id, admin_id=user_id).first()
        if processed:
            print(f"Instagram message {message_id} already processed. Skipping.")
            return

        try:
            processed_msg = ProcessedMessage(message_id=message_id, admin_id=user_id)
            db.session.add(processed_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error saving processed Instagram message ID: {e}")
            return

        # Spam/Rate Limit check (5 messages in 30s)
        is_spam = False
        limit_time = datetime.utcnow() - timedelta(seconds=30)
        customer_msgs_count = InstagramChatHistory.query.filter(
            InstagramChatHistory.sender_id == sender_id,
            InstagramChatHistory.admin_id == user_id,
            InstagramChatHistory.is_from_customer == True,
            InstagramChatHistory.created_at >= limit_time
        ).count()
        
        if customer_msgs_count >= 5:
            is_spam = True
            print(f"Spam detected from Instagram customer {sender_id}.")

        # Log customer message
        try:
            cust_msg = InstagramChatHistory(
                sender_id=sender_id,
                message_content=message_text,
                is_from_customer=True,
                admin_id=user_id
            )
            db.session.add(cust_msg)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error logging Instagram customer message: {e}")

        token = Setting.get("instagram_page_access_token", user_id=user_id)
        fallback_text = Setting.get("instagram_bot_fallback", "شكراً لتواصلك معنا على انستجرام. سنقوم بالرد عليك قريباً.", user_id=user_id)

        if is_spam:
            success, msg = send_instagram_message(token, sender_id, fallback_text)
            try:
                bot_msg = InstagramChatHistory(
                    sender_id=sender_id,
                    message_content=fallback_text,
                    is_from_customer=False,
                    admin_id=user_id
                )
                db.session.add(bot_msg)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error logging Instagram bot spam fallback: {e}")
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
            success, msg = send_instagram_message(token, sender_id, matched_response)
            try:
                bot_msg = InstagramChatHistory(
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
        history_records = InstagramChatHistory.query.filter_by(
            sender_id=sender_id, admin_id=user_id
        ).order_by(InstagramChatHistory.created_at.desc()).offset(1).limit(10).all()
        history_records.reverse()
        
        history_text = ""
        if history_records:
            for record in history_records:
                role = "Customer" if record.is_from_customer else "Bot"
                history_text += f"\n[{role}]: {record.message_content}"

        gemini_api_key = Setting.get("instagram_gemini_api_key", "", user_id=user_id).strip()
        gemini_enabled = Setting.get("instagram_gemini_enabled", "false", user_id=user_id)
        
        if str(gemini_enabled).lower() != "true" or not gemini_api_key:
            success, msg = send_instagram_message(token, sender_id, fallback_text)
            try:
                bot_msg = InstagramChatHistory(
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

        tone = Setting.get("instagram_bot_tone", "professional", user_id=user_id).lower()
        tone_rules = {
            "casual": "تحدث بأسلوب ودي، عامي، بسيط وغير رسمي (كصديق يساعد صديقاً له).",
            "professional": "تحدث بأسلوب مهني، احترافي، محترم، واضح، ودقيق جداً.",
            "formal": "تحدث بأسلوب رسمي جداً، باللغة العربية الفصحى المنضبطة والمتحفظة.",
            "friendly": "تحدث بأسلوب ودود ولطيف للغاية، ترحيبي، ومتعاطف وبشوش."
        }
        tone_instruction = tone_rules.get(tone, tone_rules["professional"])
        knowledge_base = Setting.get("instagram_bot_kb", "", user_id=user_id).strip()
        page_name = Setting.get("page_name", "يوسف بوت", user_id=user_id)

        system_instruction = f"""
أنت مساعد خدمة عملاء ذكي ومحترف لحساب الإنستغرام لـ "{page_name}".
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
Conversation History with this Customer (ID: {sender_id}):
{history_text or "No previous messages."}

Current Customer Query:
{message_text}

Reply instructions:
{system_instruction}
"""
            response = model.generate_content(prompt)
            reply_text = response.text.strip() if response and response.text else fallback_text
        except Exception as e:
            print(f"Gemini call failed for Instagram: {e}")
            reply_text = fallback_text

        # Send reply
        success, msg = send_instagram_message(token, sender_id, reply_text)
        
        # Log bot reply
        try:
            bot_msg = InstagramChatHistory(
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
            message=f"Instagram response via Gemini: {reply_text}",
            admin_id=user_id
        )

def process_instagram_comment_job(app, comment_data):
    """Processes incoming comments on Instagram posts/reels."""
    with app.app_context():
        user_id = comment_data.get("app_user_id")
        comment_id = comment_data.get("comment_id")
        post_id = comment_data.get("post_id")  # This is prefixed with "ig_"
        sender_id = comment_data.get("user_id")
        username = comment_data.get("username", "Instagram User")
        message_text = comment_data.get("message", "")
        
        # 1. Verify post is monitored
        post = Post.query.filter_by(id=post_id, user_id=user_id).first()
        if not post or not post.is_monitored:
            print(f"Skipping Instagram comment {comment_id} on post {post_id} because post is not monitored.")
            return

        # 2. Check duplicate comment
        existing_comment = Comment.query.get(comment_id)
        if existing_comment and existing_comment.processed:
            print(f"Instagram comment {comment_id} already processed. Skipping.")
            return
            
        if not existing_comment:
            existing_comment = Comment(
                id=comment_id,
                post_id=post_id,
                user_id=sender_id,
                username=username,
                message=message_text,
                created_time=datetime.utcnow(),
                processed=True
            )
            db.session.add(existing_comment)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                print(f"Instagram comment {comment_id} processed in parallel thread. Skipping.")
                return
        else:
            existing_comment.processed = True
            db.session.commit()

        # 3. Check anti-spam
        if not check_anti_spam(sender_id, post_id, admin_id=user_id):
            existing_comment.processed = True
            db.session.commit()
            log_activity(
                event_type="SYSTEM",
                status="FAILED",
                message=f"Ignored Instagram commenter {username} ({sender_id}) due to Anti-Spam limits.",
                post_id=post_id,
                comment_id=comment_id,
                user_id=sender_id,
                admin_id=user_id
            )
            trigger_dashboard_update(admin_id=user_id)
            return

        token = Setting.get("instagram_page_access_token", user_id=user_id)
        
        # 4. Formulate templates (use post template parsed variables)
        reply_template = post.default_reply or "شكراً لتعليقك. تم الرد على الخاص."
        private_template = post.private_message or "مرحباً {name}، شكراً لاهتمامك. لقد أرسلنا لك التفاصيل."
        
        parsed_reply = FacebookApiService.parse_template(
            reply_template, username, message_text, post_id, datetime.utcnow()
        )
        parsed_private = FacebookApiService.parse_template(
            private_template, username, message_text, post_id, datetime.utcnow()
        )

        # 5. Send public reply
        reply_success, reply_msg = send_instagram_comment_reply(token, comment_id, parsed_reply)
        existing_comment.reply_sent = reply_success
        existing_comment.processed_at = datetime.utcnow()
        if reply_success:
            log_activity(
                event_type="REPLY",
                status="SUCCESS",
                message=f"Instagram comment public reply sent successfully to {username}.",
                post_id=post_id,
                comment_id=comment_id,
                user_id=sender_id,
                admin_id=user_id
            )
        else:
            existing_comment.reply_error = reply_msg
            log_activity(
                event_type="REPLY",
                status="FAILED",
                message=f"Instagram public reply failed: {reply_msg}",
                post_id=post_id,
                comment_id=comment_id,
                user_id=sender_id,
                admin_id=user_id
            )
        db.session.commit()

        # Record this user as processed
        record_processed_user(sender_id, post_id, admin_id=user_id)

        # 6. Send private reply
        message_record = Message(
            post_id=post_id,
            comment_id=comment_id,
            user_id=sender_id,
            message_content=parsed_private,
            status='PENDING'
        )
        db.session.add(message_record)
        db.session.commit()

        try:
            msg_success, msg_err = send_instagram_private_reply(token, comment_id, parsed_private)
            if msg_success:
                message_record.status = 'SUCCESS'
                
                # Save to InstagramChatHistory
                try:
                    bot_msg = InstagramChatHistory(
                        sender_id=sender_id,
                        message_content=parsed_private,
                        is_from_customer=False,
                        admin_id=user_id
                    )
                    db.session.add(bot_msg)
                    db.session.commit()
                except Exception as history_ex:
                    db.session.rollback()
                    print(f"Error saving IG private reply to chat history: {history_ex}")

                log_activity(
                    event_type="MESSAGE",
                    status="SUCCESS",
                    message=f"Instagram private reply sent successfully to {username}.",
                    post_id=post_id,
                    comment_id=comment_id,
                    user_id=sender_id,
                    admin_id=user_id
                )
            else:
                message_record.status = 'FAILED'
                message_record.error_message = msg_err
                log_activity(
                    event_type="MESSAGE",
                    status="FAILED",
                    message=f"Instagram private reply failed: {msg_err}",
                    post_id=post_id,
                    comment_id=comment_id,
                    user_id=sender_id,
                    admin_id=user_id
                )
            db.session.commit()
        except Exception as msg_ex:
            db.session.rollback()
            message_record.status = 'FAILED'
            message_record.error_message = str(msg_ex)
            db.session.commit()
            log_activity(
                event_type="MESSAGE",
                status="FAILED",
                message=f"Instagram private reply failed with exception: {str(msg_ex)}",
                post_id=post_id,
                comment_id=comment_id,
                user_id=sender_id,
                admin_id=user_id
            )

        trigger_dashboard_update(admin_id=user_id)
