from flask import Blueprint, request, Response, current_app, jsonify
import hmac
import hashlib
import json
from datetime import datetime
from models import db, Setting, WebhookLog
from services.scheduler import scheduler
from services.comment_processor import process_comment_job, log_activity

webhook_bp = Blueprint('webhook', __name__)

def verify_signature(payload_bytes, signature_header, secret):
    """Verifies that the webhook payload is signed with the App Secret."""
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    if not secret:
        # If no secret is configured, deny verification for safety
        return False
        
    expected_sig = signature_header.split("sha256=")[1]
    computed_sig = hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed_sig, expected_sig)

@webhook_bp.route('/webhook', methods=['GET'])
def verify():
    """Handles GET verification requests from Meta Developer Platform."""
    verify_token = Setting.get("verify_token", "my_verify_token_123")
    
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    
    if mode and token:
        if mode == 'subscribe' and token == verify_token:
            print("Webhook verified successfully by Meta!")
            log_activity(
                event_type="WEBHOOK",
                status="SUCCESS",
                message="Webhook verification challenge completed successfully."
            )
            return Response(challenge, status=200, mimetype="text/plain")
        else:
            log_activity(
                event_type="WEBHOOK",
                status="FAILED",
                message="Webhook verification failed: Token mismatch."
            )
            return Response("Forbidden", status=403)
            
    return Response("Bad Request", status=400)

@webhook_bp.route('/webhook', methods=['POST'])
def handle_event():
    """Handles incoming POST events from Meta Graph API Webhooks."""
    app_secret = Setting.get("app_secret")
    signature = request.headers.get('X-Hub-Signature-256')
    raw_payload = request.data
    
    # 1. Validate X-Hub-Signature-256
    if app_secret:
        if not verify_signature(raw_payload, signature, app_secret):
            # Log rejected payload
            log_activity(
                event_type="WEBHOOK",
                status="FAILED",
                message="Incoming webhook request signature verification failed. App Secret mismatch."
            )
            return jsonify({"status": "error", "message": "Invalid signature"}), 403
            
    # Parse Webhook Log
    log_status = "SUCCESS"
    log_error = None
    
    try:
        data = request.get_json()
    except Exception as e:
        log_status = "FAILED"
        log_error = f"Invalid JSON payload: {str(e)}"
        
        # Save raw log
        log_db = WebhookLog(
            payload=raw_payload.decode('utf-8', errors='ignore'),
            status=log_status,
            error_message=log_error
        )
        db.session.add(log_db)
        db.session.commit()
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    # Save to WebhookLog
    log_db = WebhookLog(
        payload=json.dumps(data),
        status=log_status,
        error_message=log_error
    )
    db.session.add(log_db)
    db.session.commit()

    # 2. Check for feed changes (new comments) or direct messages (messaging)
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            page_id = entry.get("id")
            
            # Find the user associated with this Page ID (prioritize non-null user_id)
            user_setting = Setting.query.filter(
                Setting.key == "page_id",
                Setting.value == str(page_id),
                Setting.user_id.isnot(None)
            ).first()
            
            if not user_setting:
                user_setting = Setting.query.filter_by(key="page_id", value=str(page_id)).first()
                
            user_id = None
            if user_setting:
                user_id = user_setting.user_id
                
            if not user_id:
                # Fallback to the default admin user for unittests / global settings
                from models import Admin
                default_admin = Admin.query.filter_by(role='developer').first() or Admin.query.first()
                if default_admin:
                    user_id = default_admin.id
                    
            if not user_id:
                print(f"Skipping event for Page ID {page_id} because it's not connected to any registered user.")
                continue
            
            # Verify admin user is active and subscription is valid
            from models import Admin
            admin = Admin.query.get(user_id)
            if not admin or not admin.is_active:
                print(f"Skipping event for user {user_id}: Inactive account.")
                continue
                
            if admin.subscription_expires_at and admin.subscription_expires_at < datetime.utcnow():
                print(f"Skipping event for user {user_id}: Subscription expired at {admin.subscription_expires_at}.")
                continue
            
            # A. Handle comments (feed changes)
            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    val = change.get("value", {})
                    item = val.get("item")
                    verb = val.get("verb")
                    
                    if item == "comment" and verb == "add":
                        comment_id = val.get("comment_id")
                        post_id = val.get("post_id")
                        sender_id = val.get("from", {}).get("id")
                        sender_name = val.get("from", {}).get("name")
                        
                        if sender_id and str(sender_id) == str(page_id):
                            print(f"Skipping comment {comment_id} written by the page itself to prevent loop.")
                            continue
                            
                        message = val.get("message", "")
                        created_time_int = val.get("created_time")
                        
                        created_time_str = None
                        if created_time_int:
                            created_time_str = datetime.utcfromtimestamp(created_time_int).strftime("%Y-%m-%dT%H:%M:%S+0000")
                            
                        comment_data = {
                            "comment_id": comment_id,
                            "post_id": post_id,
                            "user_id": sender_id,
                            "username": sender_name,
                            "message": message,
                            "created_time": created_time_str,
                            "app_user_id": user_id
                        }
                        
                        app_ref = current_app._get_current_object()
                        scheduler.add_job(
                            func=process_comment_job,
                            trigger='date',
                            args=[app_ref, comment_data],
                            id=f"process_comment_{comment_id}",
                            name=f"Process comment {comment_id} from {sender_name}",
                            replace_existing=True
                        )
                        print(f"Enqueued comment {comment_id} for processing.")
            
            # B. Handle direct Messenger messages
            if "messaging" in entry:
                for messaging_event in entry.get("messaging", []):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    recipient_id = messaging_event.get("recipient", {}).get("id")
                    page_id_stored = Setting.get("page_id", user_id=user_id)
                    
                    if sender_id and sender_id != page_id_stored:
                        message_data = messaging_event.get("message", {})
                        if "text" in message_data and not message_data.get("is_echo"):
                            message_text = message_data.get("text", "")
                            message_id = message_data.get("mid")
                            
                            msg_details = {
                                "sender_id": sender_id,
                                "message_text": message_text,
                                "message_id": message_id,
                                "timestamp": messaging_event.get("timestamp"),
                                "app_user_id": user_id
                            }
                            
                            from services.messenger_processor import process_messenger_job
                            app_ref = current_app._get_current_object()
                            scheduler.add_job(
                                func=process_messenger_job,
                                trigger='date',
                                args=[app_ref, msg_details],
                                id=f"process_msg_{message_id}",
                                name=f"Process Messenger message {message_id} from {sender_id}",
                                replace_existing=True
                            )
                            print(f"Enqueued Messenger message {message_id} for processing.")

    elif data.get("object") == "instagram":
        for entry in data.get("entry", []):
            page_id = entry.get("id")
            
            user_setting = Setting.query.filter(
                Setting.key == "instagram_page_id",
                Setting.value == str(page_id),
                Setting.user_id.isnot(None)
            ).first()
            
            if not user_setting:
                user_setting = Setting.query.filter_by(key="instagram_page_id", value=str(page_id)).first()
                
            user_id = None
            if user_setting:
                user_id = user_setting.user_id
                
            if not user_id:
                from models import Admin
                default_admin = Admin.query.filter_by(role='developer').first() or Admin.query.first()
                if default_admin:
                    user_id = default_admin.id
                    
            if not user_id:
                continue
                
            # A. Handle Instagram Direct Messages
            if "messaging" in entry:
                for messaging_event in entry.get("messaging", []):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    recipient_id = messaging_event.get("recipient", {}).get("id")
                    page_id_stored = Setting.get("instagram_page_id", user_id=user_id)
                    
                    if sender_id and sender_id != page_id_stored:
                        message_data = messaging_event.get("message", {})
                        if "text" in message_data and not message_data.get("is_echo"):
                            message_text = message_data.get("text", "")
                            message_id = message_data.get("mid")
                            
                            msg_details = {
                                "sender_id": sender_id,
                                "message_text": message_text,
                                "message_id": message_id,
                                "timestamp": messaging_event.get("timestamp"),
                                "app_user_id": user_id
                            }
                            
                            from services.instagram_processor import process_instagram_message_job
                            app_ref = current_app._get_current_object()
                            scheduler.add_job(
                                func=process_instagram_message_job,
                                trigger='date',
                                args=[app_ref, msg_details],
                                id=f"process_ig_msg_{message_id}",
                                name=f"Process IG message {message_id} from {sender_id}",
                                replace_existing=True
                            )
                            print(f"Enqueued Instagram message {message_id} for processing.")

            # B. Handle Instagram Comments
            for change in entry.get("changes", []):
                if change.get("field") == "comments":
                    val = change.get("value", {})
                    comment_id = val.get("id")
                    media = val.get("media", {})
                    post_id = media.get("id")
                    sender_id = val.get("from", {}).get("id")
                    sender_username = val.get("from", {}).get("username")
                    message_text = val.get("text", "")
                    
                    if sender_id and str(sender_id) == str(page_id):
                        continue
                        
                    comment_data = {
                        "comment_id": comment_id,
                        "post_id": f"ig_{post_id}" if post_id else None,
                        "user_id": sender_id,
                        "username": sender_username,
                        "message": message_text,
                        "app_user_id": user_id
                    }
                    
                    from services.instagram_processor import process_instagram_comment_job
                    app_ref = current_app._get_current_object()
                    scheduler.add_job(
                        func=process_instagram_comment_job,
                        trigger='date',
                        args=[app_ref, comment_data],
                        id=f"process_ig_comment_{comment_id}",
                        name=f"Process IG comment {comment_id} from {sender_username}",
                        replace_existing=True
                    )
                    print(f"Enqueued Instagram comment {comment_id} for processing.")

    elif data.get("object") == "whatsapp_business_account":
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    val = change.get("value", {})
                    metadata = val.get("metadata", {})
                    phone_number_id = metadata.get("phone_number_id")
                    
                    user_setting = Setting.query.filter(
                        Setting.key == "whatsapp_phone_number_id",
                        Setting.value == str(phone_number_id),
                        Setting.user_id.isnot(None)
                    ).first()
                    
                    if not user_setting:
                        user_setting = Setting.query.filter_by(key="whatsapp_phone_number_id", value=str(phone_number_id)).first()
                        
                    user_id = None
                    if user_setting:
                        user_id = user_setting.user_id
                        
                    if not user_id:
                        from models import Admin
                        default_admin = Admin.query.filter_by(role='developer').first() or Admin.query.first()
                        if default_admin:
                            user_id = default_admin.id
                            
                    if not user_id:
                        continue
                        
                    for message in val.get("messages", []):
                        sender_id = message.get("from")
                        message_id = message.get("id")
                        msg_type = message.get("type")
                        
                        if msg_type == "text":
                            message_text = message.get("text", {}).get("body", "")
                            
                            msg_details = {
                                "sender_id": sender_id,
                                "message_text": message_text,
                                "message_id": message_id,
                                "app_user_id": user_id
                            }
                            
                            from services.whatsapp_processor import process_whatsapp_message_job
                            app_ref = current_app._get_current_object()
                            scheduler.add_job(
                                func=process_whatsapp_message_job,
                                trigger='date',
                                args=[app_ref, msg_details],
                                id=f"process_wa_msg_{message_id}",
                                name=f"Process WA message {message_id} from {sender_id}",
                                replace_existing=True
                            )
                            print(f"Enqueued WhatsApp message {message_id} for processing.")

    return jsonify({"status": "received"}), 200
