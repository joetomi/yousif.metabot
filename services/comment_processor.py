from datetime import datetime
from models import db, Post, Comment, Message, ProcessedUser, ActivityLog, Setting
from services.facebook_api import FacebookApiService

def check_anti_spam(user_id, post_id, admin_id=None):
    """
    Checks if a reply should be sent to the user based on the active anti-spam policy.
    Modes:
      - 'every_comment': Always reply.
      - 'once_per_user_post': Reply once per user per post.
      - 'once_per_user_global': Reply once per user globally.
    """
    mode = Setting.get("anti_spam_mode", "every_comment", user_id=admin_id)
    
    if mode == "every_comment":
        return True
        
    if mode == "once_per_user_post":
        exists = ProcessedUser.query.filter_by(user_id=user_id, post_id=post_id, admin_id=admin_id).first()
        return exists is None
        
    if mode == "once_per_user_global":
        exists = ProcessedUser.query.filter_by(user_id=user_id, admin_id=admin_id).first()
        return exists is None
        
    return True

def record_processed_user(user_id, post_id, admin_id=None):
    """Saves user to ProcessedUser to enforce anti-spam controls."""
    try:
        processed = ProcessedUser(user_id=user_id, post_id=post_id, admin_id=admin_id)
        db.session.add(processed)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error recording processed user: {e}")

def trigger_dashboard_update(admin_id=None):
    """Broadcasts real-time statistics update via SocketIO to the dashboard."""
    try:
        from app import socketio
        
        if admin_id:
            # Calculate stats for specific user
            total_monitored = Post.query.filter_by(is_monitored=True, user_id=admin_id).count()
            total_comments = Comment.query.join(Post).filter(Post.user_id == admin_id).count()
            total_replies = Comment.query.join(Post).filter(Post.user_id == admin_id, Comment.reply_sent == True).count()
            total_messages = Message.query.join(Post).filter(Post.user_id == admin_id, Message.status == 'SUCCESS').count()
            
            socketio.emit('stats_update', {
                'admin_id': admin_id,
                'total_monitored': total_monitored,
                'total_comments': total_comments,
                'total_replies': total_replies,
                'total_messages': total_messages
            })
        else:
            total_monitored = Post.query.filter_by(is_monitored=True).count()
            total_comments = Comment.query.count()
            total_replies = Comment.query.filter_by(reply_sent=True).count()
            total_messages = Message.query.filter_by(status='SUCCESS').count()
            
            from models import WebhookLog
            total_webhooks = WebhookLog.query.count() if db.inspect(db.engine).has_table("webhook_logs") else 0
            
            # Emit event
            socketio.emit('stats_update', {
                'total_monitored': total_monitored,
                'total_comments': total_comments,
                'total_replies': total_replies,
                'total_messages': total_messages,
                'total_webhooks': total_webhooks
            })
    except Exception as e:
        print(f"Error sending SocketIO update: {e}")

def process_comment_job(app, comment_data):
    """
    Background job function executed by APScheduler.
    Executes within the flask application context.
    """
    with app.app_context():
        comment_id = comment_data.get("comment_id")
        post_id = comment_data.get("post_id")
        user_id = comment_data.get("user_id")
        username = comment_data.get("username", "Facebook User")
        message_text = comment_data.get("message", "")
        created_time_str = comment_data.get("created_time")
        admin_id = comment_data.get("app_user_id")
        
        created_time = datetime.utcnow()
        if created_time_str:
            try:
                # Meta format: e.g. 2026-06-12T18:40:00+0000
                created_time = datetime.strptime(created_time_str.split("+")[0], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass

        # 1. Verify if the parent post is monitored
        post = Post.query.filter_by(id=post_id, user_id=admin_id).first()
        if not post or not post.is_monitored:
            log_activity(
                event_type="SYSTEM",
                status="FAILED",
                message=f"Ignored comment {comment_id} on post {post_id} because monitoring is disabled.",
                post_id=post_id,
                comment_id=comment_id,
                user_id=user_id,
                admin_id=admin_id
            )
            return

        # 2. Check if comment itself was already processed (de-duplication)
        existing_comment = Comment.query.get(comment_id)
        if existing_comment and existing_comment.processed:
            print(f"Comment {comment_id} was already processed. Skipping.")
            return

        # Create or update Comment record
        if not existing_comment:
            existing_comment = Comment(
                id=comment_id,
                post_id=post_id,
                user_id=user_id,
                username=username,
                message=message_text,
                created_time=created_time,
                processed=True  # Mark as processed immediately to prevent duplicate concurrent runs
            )
            db.session.add(existing_comment)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                print(f"Comment {comment_id} was already processed in another thread. Skipping.")
                return
        else:
            existing_comment.processed = True
            db.session.commit()

        # 3. Check anti-spam rules
        if not check_anti_spam(user_id, post_id, admin_id=admin_id):
            existing_comment.processed = True
            db.session.commit()
            
            log_activity(
                event_type="SYSTEM",
                status="FAILED",
                message=f"Ignored commenter {username} ({user_id}) due to Anti-Spam limits.",
                post_id=post_id,
                comment_id=comment_id,
                user_id=user_id,
                admin_id=admin_id
            )
            trigger_dashboard_update(admin_id=admin_id)
            return

        # Initialize Graph API Service for this user
        token = Setting.get("page_access_token", user_id=admin_id)
        page_id = Setting.get("page_id", user_id=admin_id)
        api = FacebookApiService(page_access_token=token, page_id=page_id)

        # 4. Formulate templates (try Gemini AI first, fallback to static templates)
        ai_replies = None
        try:
            from services.gemini_api import generate_ai_replies
            ai_replies = generate_ai_replies(message_text, username, post.message, user_id=admin_id)
        except Exception as ai_ex:
            print(f"Exception calling Gemini service: {ai_ex}")
            
        if ai_replies:
            parsed_reply, parsed_private = ai_replies
            print(f"Using Gemini generated replies for comment {comment_id}.")
        else:
            # Fallback
            gemini_enabled = Setting.get("gemini_enabled", "false", user_id=admin_id)
            if str(gemini_enabled).lower() == "true":
                fallback_text = Setting.get("messenger_bot_fallback", "شكراً لتواصلك معنا. تم استلام رسالتك وسيقوم أحد ممثلي خدمة العملاء بالرد عليك قريباً.", user_id=admin_id)
                parsed_reply = post.default_reply or "تم الرد على الخاص."
                parsed_private = FacebookApiService.parse_template(
                    fallback_text, username, message_text, post_id, created_time
                )
            else:
                reply_template = post.default_reply or "Thank you for your comment. We have sent details to your inbox."
                private_template = post.private_message or "Hello {name}, thank you for your message."
                
                parsed_reply = FacebookApiService.parse_template(
                    reply_template, username, message_text, post_id, created_time
                )
                parsed_private = FacebookApiService.parse_template(
                    private_template, username, message_text, post_id, created_time
                )

        # 5. Public Reply
        reply_success, reply_msg, reply_fb_id = api.reply_to_comment(comment_id, parsed_reply)
        
        existing_comment.reply_sent = reply_success
        existing_comment.processed = True
        existing_comment.processed_at = datetime.utcnow()
        if reply_success:
            existing_comment.reply_id = reply_fb_id
            log_activity(
                event_type="REPLY",
                status="SUCCESS",
                message=f"Public reply sent successfully to {username}.",
                post_id=post_id,
                comment_id=comment_id,
                user_id=user_id,
                admin_id=admin_id
            )
        else:
            existing_comment.reply_error = reply_msg
            log_activity(
                event_type="REPLY",
                status="FAILED",
                message=f"Public reply failed: {reply_msg}",
                post_id=post_id,
                comment_id=comment_id,
                user_id=user_id,
                admin_id=admin_id
            )
        db.session.commit()

        # Record this user as processed
        record_processed_user(user_id, post_id, admin_id=admin_id)

        # 6. Private Reply (gracefully isolated)
        message_record = Message(
            post_id=post_id,
            comment_id=comment_id,
            user_id=user_id,
            message_content=parsed_private,
            status='PENDING'
        )
        db.session.add(message_record)
        db.session.commit()

        try:
            msg_success, msg_err = api.send_private_reply(comment_id, parsed_private)
            if msg_success:
                message_record.status = 'SUCCESS'
                
                # Save to MessengerChatHistory
                try:
                    from models import MessengerChatHistory
                    bot_msg = MessengerChatHistory(
                        sender_id=user_id,
                        message_content=parsed_private,
                        is_from_customer=False,
                        admin_id=admin_id
                    )
                    db.session.add(bot_msg)
                    db.session.commit()
                except Exception as history_ex:
                    db.session.rollback()
                    print(f"Error saving comment private reply to chat history: {history_ex}")
                    
                log_activity(
                    event_type="MESSAGE",
                    status="SUCCESS",
                    message=f"Private messenger reply sent successfully to {username}.",
                    post_id=post_id,
                    comment_id=comment_id,
                    user_id=user_id,
                    admin_id=admin_id
                )
            else:
                message_record.status = 'FAILED'
                message_record.error_message = msg_err
                log_activity(
                    event_type="MESSAGE",
                    status="FAILED",
                    message=f"Private reply rejected by Meta: {msg_err}",
                    post_id=post_id,
                    comment_id=comment_id,
                    user_id=user_id,
                    admin_id=admin_id
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
                message=f"Private reply failed with exception: {str(msg_ex)}",
                post_id=post_id,
                comment_id=comment_id,
                user_id=user_id,
                admin_id=admin_id
            )

        # Refresh dashboard stats
        trigger_dashboard_update(admin_id=admin_id)

def log_activity(event_type, status, message, post_id=None, comment_id=None, user_id=None, admin_id=None):
    """Helper to record system and activity logs."""
    try:
        if admin_id is None:
            from flask import has_request_context, session
            if has_request_context():
                admin_id = session.get('admin_id')
                
        activity = ActivityLog(
            event_type=event_type,
            status=status,
            message=message,
            post_id=post_id,
            comment_id=comment_id,
            user_id=user_id,
            admin_id=admin_id
        )
        db.session.add(activity)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error logging activity: {e}")
