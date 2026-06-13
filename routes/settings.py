from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
import json
from routes.auth import admin_required
from models import db, Setting, Post
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings')
@admin_required
def index():
    # Load all relevant settings from DB
    page_access_token = Setting.get("page_access_token", "")
    app_secret = Setting.get("app_secret", "")
    verify_token = Setting.get("verify_token", "my_verify_token_123")
    page_id = Setting.get("page_id", "")
    tunnel_url = Setting.get("tunnel_url", "https://ready-otters-happen.loca.lt")
    anti_spam_mode = Setting.get("anti_spam_mode", "every_comment")
    
    # Calculate webhook URL automatically
    webhook_url = f"{tunnel_url.rstrip('/')}/webhook"
    
    return render_template(
        'settings.html',
        page_access_token=page_access_token,
        app_secret=app_secret,
        verify_token=verify_token,
        page_id=page_id,
        tunnel_url=tunnel_url,
        webhook_url=webhook_url,
        anti_spam_mode=anti_spam_mode
    )

@settings_bp.route('/settings/save', methods=['POST'])
@admin_required
def save():
    try:
        page_access_token = request.form.get("page_access_token", "").strip()
        app_secret = request.form.get("app_secret", "").strip()
        verify_token = request.form.get("verify_token", "").strip()
        page_id = request.form.get("page_id", "").strip()
        tunnel_url = request.form.get("tunnel_url", "").strip()
        anti_spam_mode = request.form.get("anti_spam_mode", "every_comment")
        
        Setting.set("page_access_token", page_access_token)
        Setting.set("app_secret", app_secret)
        Setting.set("verify_token", verify_token)
        Setting.set("page_id", page_id)
        Setting.set("tunnel_url", tunnel_url)
        Setting.set("anti_spam_mode", anti_spam_mode)
        
        # Try to automatically subscribe Page to App webhooks
        sub_status_msg = ""
        if page_access_token and page_id:
            api = FacebookApiService(page_access_token=page_access_token, page_id=page_id)
            sub_ok, sub_msg = api.subscribe_page()
            if sub_ok:
                sub_status_msg = " & Page subscribed to App webhooks."
                log_activity(
                    event_type="SYSTEM",
                    status="SUCCESS",
                    message="Successfully subscribed Page to App webhooks."
                )
            else:
                sub_status_msg = f" (Warning: Webhook subscription failed: {sub_msg})"
                log_activity(
                    event_type="SYSTEM",
                    status="WARNING",
                    message=f"Failed to subscribe Page to App webhooks: {sub_msg}"
                )

        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message="Application settings updated successfully."
        )
        flash(f"Settings saved successfully!{sub_status_msg}", "success")
    except Exception as e:
        flash(f"Error saving settings: {str(e)}", "danger")
        
    return redirect(url_for('settings.index'))

@settings_bp.route('/settings/test-connection', methods=['POST'])
@admin_required
def test_connection():
    # Temporary instantiate api using form values (if user changes inputs and wants to test before saving)
    page_access_token = request.form.get("page_access_token", "").strip()
    page_id = request.form.get("page_id", "").strip()
    
    # Fallback to DB if empty
    if not page_access_token:
        page_access_token = Setting.get("page_access_token")
    if not page_id:
        page_id = Setting.get("page_id")
        
    api = FacebookApiService(page_access_token=page_access_token, page_id=page_id)
    success, message = api.test_connection()
    
    if success:
        sub_ok, sub_msg = api.subscribe_page()
        if sub_ok:
            return jsonify({"status": "success", "message": f"{message} & Page subscribed to App webhooks."})
        else:
            return jsonify({"status": "success", "message": f"{message} (Warning: Failed to subscribe page: {sub_msg})"})
    else:
        return jsonify({"status": "error", "message": message}), 400

@settings_bp.route('/settings/refresh-page', methods=['POST'])
@admin_required
def refresh_page():
    api = FacebookApiService()
    page_info = api.get_page_info()
    
    if page_info:
        # Save any fetched page info
        page_name = page_info.get("name", "")
        page_username = page_info.get("username", "")
        Setting.set("page_name", page_name)
        Setting.set("page_username", page_username)
        
        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message=f"Refreshed page details for: {page_name}"
        )
        return jsonify({"status": "success", "message": f"Successfully refreshed details for page '{page_name}'."})
    else:
        return jsonify({"status": "error", "message": "Failed to fetch page info from Meta API. Verify token and ID."}), 400

@settings_bp.route('/settings/export', methods=['GET'])
@admin_required
def export_config():
    """Exports application settings, monitored posts, and template variables as JSON."""
    try:
        # Retrieve settings
        config_data = {
            "settings": {
                "page_access_token": Setting.get("page_access_token", ""),
                "app_secret": Setting.get("app_secret", ""),
                "verify_token": Setting.get("verify_token", ""),
                "page_id": Setting.get("page_id", ""),
                "tunnel_url": Setting.get("tunnel_url", ""),
                "anti_spam_mode": Setting.get("anti_spam_mode", "every_comment"),
                "page_name": Setting.get("page_name", ""),
                "page_username": Setting.get("page_username", "")
            },
            "posts": []
        }
        
        # Retrieve posts
        posts = Post.query.all()
        for post in posts:
            config_data["posts"].append({
                "id": post.id,
                "message": post.message,
                "is_monitored": post.is_monitored,
                "default_reply": post.default_reply,
                "private_message": post.private_message
            })
            
        json_output = json.dumps(config_data, indent=4)
        
        log_activity(
            event_type="CONFIG_EXPORT",
            status="SUCCESS",
            message="Exported system configuration backup file."
        )
        
        return Response(
            json_output,
            mimetype="application/json",
            headers={"Content-disposition": "attachment; filename=facebook_bot_backup.json"}
        )
    except Exception as e:
        flash(f"Failed to export configuration: {str(e)}", "danger")
        return redirect(url_for('settings.index'))

@settings_bp.route('/settings/import', methods=['POST'])
@admin_required
def import_config():
    """Restores settings and posts from an uploaded JSON configuration file."""
    if 'backup_file' not in request.files:
        flash("No file part selected.", "danger")
        return redirect(url_for('settings.index'))
        
    file = request.files['backup_file']
    if file.filename == '':
        flash("No backup file uploaded.", "danger")
        return redirect(url_for('settings.index'))
        
    try:
        data = json.load(file)
        
        # 1. Import Settings
        settings_data = data.get("settings", {})
        for key, val in settings_data.items():
            Setting.set(key, val)
            
        # 2. Import Posts
        posts_data = data.get("posts", [])
        posts_created = 0
        posts_updated = 0
        
        for p_data in posts_data:
            p_id = p_data.get("id")
            if not p_id:
                continue
                
            post = Post.query.get(p_id)
            if post:
                post.message = p_data.get("message", post.message)
                post.is_monitored = p_data.get("is_monitored", post.is_monitored)
                post.default_reply = p_data.get("default_reply", post.default_reply)
                post.private_message = p_data.get("private_message", post.private_message)
                posts_updated += 1
            else:
                post = Post(
                    id=p_id,
                    message=p_data.get("message", ""),
                    is_monitored=p_data.get("is_monitored", False),
                    default_reply=p_data.get("default_reply", ""),
                    private_message=p_data.get("private_message", "")
                )
                db.session.add(post)
                posts_created += 1
                
        db.session.commit()
        
        log_activity(
            event_type="CONFIG_IMPORT",
            status="SUCCESS",
            message=f"Restored backup: settings updated, {posts_created} new posts, {posts_updated} updated."
        )
        
        flash(f"Configuration imported successfully! ({posts_created} new, {posts_updated} updated posts)", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to restore backup: {str(e)}", "danger")
        
    return redirect(url_for('settings.index'))
