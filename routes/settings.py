from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response, session
import json
import secrets
import requests
from urllib.parse import urlencode
from routes.auth import admin_required
from models import db, Setting, Post
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings')
@admin_required
def index():
    # Load all relevant settings from DB
    app_id = Setting.get("app_id", "")
    page_access_token = Setting.get("page_access_token", "")
    app_secret = Setting.get("app_secret", "")
    verify_token = Setting.get("verify_token", "my_verify_token_123")
    page_id = Setting.get("page_id", "")
    tunnel_url = Setting.get("tunnel_url", "https://ready-otters-happen.loca.lt")
    anti_spam_mode = Setting.get("anti_spam_mode", "every_comment")
    gemini_api_key = Setting.get("gemini_api_key", "")
    gemini_enabled = Setting.get("gemini_enabled", "false")
    gemini_system_instruction = Setting.get("gemini_system_instruction", "أنت مساعد ذكي ولطيف، أجب على استفسار العميل باحترافية واختصار.")
    
    # Calculate webhook URL automatically
    webhook_url = f"{tunnel_url.rstrip('/')}/webhook"
    
    return render_template(
        'settings.html',
        app_id=app_id,
        page_access_token=page_access_token,
        app_secret=app_secret,
        verify_token=verify_token,
        page_id=page_id,
        tunnel_url=tunnel_url,
        webhook_url=webhook_url,
        anti_spam_mode=anti_spam_mode,
        gemini_api_key=gemini_api_key,
        gemini_enabled=gemini_enabled,
        gemini_system_instruction=gemini_system_instruction
    )

@settings_bp.route('/settings/save', methods=['POST'])
@admin_required
def save():
    try:
        app_id = request.form.get("app_id", "").strip()
        page_access_token = request.form.get("page_access_token", "").strip()
        app_secret = request.form.get("app_secret", "").strip()
        verify_token = request.form.get("verify_token", "").strip()
        page_id = request.form.get("page_id", "").strip()
        tunnel_url = request.form.get("tunnel_url", "").strip()
        anti_spam_mode = request.form.get("anti_spam_mode", "every_comment")
        
        # Checkbox logic
        gemini_enabled = "true" if request.form.get("gemini_enabled") else "false"
        gemini_api_key = request.form.get("gemini_api_key", "").strip()
        gemini_system_instruction = request.form.get("gemini_system_instruction", "").strip()
        
        Setting.set("app_id", app_id)
        Setting.set("page_access_token", page_access_token)
        Setting.set("app_secret", app_secret)
        Setting.set("verify_token", verify_token)
        Setting.set("page_id", page_id)
        Setting.set("tunnel_url", tunnel_url)
        Setting.set("anti_spam_mode", anti_spam_mode)
        Setting.set("gemini_enabled", gemini_enabled)
        Setting.set("gemini_api_key", gemini_api_key)
        Setting.set("gemini_system_instruction", gemini_system_instruction)
        
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

@settings_bp.route('/settings/facebook/login')
@admin_required
def facebook_login():
    app_id = Setting.get("app_id")
    if not app_id:
        flash("Please configure Facebook App ID first.", "danger")
        return redirect(url_for('settings.index'))
        
    tunnel_url = Setting.get("tunnel_url")
    redirect_uri = f"{tunnel_url.rstrip('/')}/settings/facebook/callback"
    
    # Anti-forgery state token
    state = secrets.token_hex(16)
    session['oauth_state'] = state
    
    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': 'pages_show_list,pages_read_engagement,pages_manage_metadata,pages_messaging'
    }
    fb_oauth_url = f"https://www.facebook.com/v19.0/dialog/oauth?{urlencode(params)}"
    return redirect(fb_oauth_url)

@settings_bp.route('/settings/facebook/callback')
@admin_required
def facebook_callback():
    error = request.args.get('error')
    if error:
        error_desc = request.args.get('error_description', 'Authorization rejected')
        flash(f"Facebook Login error: {error_desc}", "danger")
        return redirect(url_for('settings.index'))
        
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state to prevent CSRF
    if not state or state != session.get('oauth_state'):
        flash("Invalid state token. Possible CSRF attempt.", "danger")
        return redirect(url_for('settings.index'))
        
    session.pop('oauth_state', None)
    
    app_id = Setting.get("app_id")
    app_secret = Setting.get("app_secret")
    tunnel_url = Setting.get("tunnel_url")
    redirect_uri = f"{tunnel_url.rstrip('/')}/settings/facebook/callback"
    
    if not app_id or not app_secret:
        flash("App ID or App Secret configuration missing.", "danger")
        return redirect(url_for('settings.index'))
        
    # 1. Exchange code for Short-lived User Access Token
    token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
    token_params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'client_secret': app_secret,
        'code': code
    }
    
    try:
        token_res = requests.get(token_url, params=token_params)
        token_data = token_res.json()
        
        if 'error' in token_data:
            err_msg = token_data['error'].get('message', 'Failed to retrieve access token')
            flash(f"OAuth exchange failed: {err_msg}", "danger")
            return redirect(url_for('settings.index'))
            
        short_user_token = token_data.get('access_token')
        
        # 2. Exchange short-lived User Token for a Long-lived User Token
        extend_params = {
            'grant_type': 'fb_exchange_token',
            'client_id': app_id,
            'client_secret': app_secret,
            'fb_exchange_token': short_user_token
        }
        extend_res = requests.get(token_url, params=extend_params)
        extend_data = extend_res.json()
        
        if 'error' in extend_data:
            err_msg = extend_data['error'].get('message', 'Failed to extend token')
            flash(f"Token extension failed: {err_msg}", "danger")
            return redirect(url_for('settings.index'))
            
        long_user_token = extend_data.get('access_token')
        
        # 3. Retrieve user accounts (Pages)
        pages_url = "https://graph.facebook.com/v19.0/me/accounts"
        pages_res = requests.get(pages_url, headers={"Authorization": f"Bearer {long_user_token}"})
        pages_data = pages_res.json()
        
        if 'error' in pages_data:
            err_msg = pages_data['error'].get('message', 'Failed to retrieve pages')
            flash(f"Failed to fetch user pages: {err_msg}", "danger")
            return redirect(url_for('settings.index'))
            
        pages_list = pages_data.get('data', [])
        if not pages_list:
            flash("No Facebook pages found for this user account.", "warning")
            return redirect(url_for('settings.index'))
            
        # Store pages in session temporarily so the user can choose
        session['oauth_pages'] = [
            {
                'id': page.get('id'),
                'name': page.get('name'),
                'access_token': page.get('access_token'),
                'category': page.get('category')
            }
            for page in pages_list
        ]
        
        return render_template('select_page.html', pages=session['oauth_pages'])
        
    except Exception as e:
        flash(f"Error during Facebook Login: {str(e)}", "danger")
        return redirect(url_for('settings.index'))

@settings_bp.route('/settings/facebook/select', methods=['POST'])
@admin_required
def facebook_select_page():
    page_id = request.form.get('page_id')
    oauth_pages = session.get('oauth_pages')
    
    if not page_id or not oauth_pages:
        flash("Invalid page selection or session expired.", "danger")
        return redirect(url_for('settings.index'))
        
    # Find the page in our stored list
    selected_page = next((p for p in oauth_pages if p['id'] == page_id), None)
    if not selected_page:
        flash("Selected page not found in session.", "danger")
        return redirect(url_for('settings.index'))
        
    # Save settings to database
    Setting.set("page_id", selected_page['id'])
    Setting.set("page_access_token", selected_page['access_token'])
    Setting.set("page_name", selected_page['name'])
    
    # Clear pages from session
    session.pop('oauth_pages', None)
    
    # Automatically try to subscribe page to webhooks
    sub_status_msg = ""
    try:
        api = FacebookApiService(page_access_token=selected_page['access_token'], page_id=selected_page['id'])
        sub_ok, sub_msg = api.subscribe_page()
        if sub_ok:
            sub_status_msg = " & Page subscribed to App webhooks."
            log_activity(
                event_type="SYSTEM",
                status="SUCCESS",
                message=f"Connected page '{selected_page['name']}' and subscribed to webhooks via OAuth."
            )
        else:
            sub_status_msg = f" (Warning: Webhook subscription failed: {sub_msg})"
            log_activity(
                event_type="SYSTEM",
                status="WARNING",
                message=f"Connected page '{selected_page['name']}' but webhook subscription failed: {sub_msg}"
            )
    except Exception as sub_ex:
        sub_status_msg = f" (Warning: Webhook subscription error: {str(sub_ex)})"
        
    flash(f"Page '{selected_page['name']}' successfully connected!{sub_status_msg}", "success")
    return redirect(url_for('settings.index'))
