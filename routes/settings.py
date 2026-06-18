import json
import secrets
import requests
from urllib.parse import urlencode
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from routes.auth import admin_required
from models import db, Setting
from services.facebook_api import FacebookApiService
from services.comment_processor import log_activity

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings/facebook/login')
@admin_required
def facebook_login():
    # Mark session context as popup flow
    session['oauth_popup'] = True
    
    app_id = Setting.get("app_id")
    if not app_id:
        flash("Please configure Facebook App ID first.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    redirect_uri = f"{request.url_root.rstrip('/')}/settings/facebook/callback"
    # Force HTTPS for production/external URLs to prevent "Insecure Login Blocked" from Meta
    if not request.host.startswith('localhost') and not request.host.startswith('127.0.0.1'):
        if redirect_uri.startswith('http://'):
            redirect_uri = redirect_uri.replace('http://', 'https://', 1)
    
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
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state to prevent CSRF
    if not state or state != session.get('oauth_state'):
        flash("Invalid state token. Possible CSRF attempt.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    session.pop('oauth_state', None)
    
    app_id = Setting.get("app_id")
    app_secret = Setting.get("app_secret")
    redirect_uri = f"{request.url_root.rstrip('/')}/settings/facebook/callback"
    # Force HTTPS for production/external URLs to prevent "Insecure Login Blocked" from Meta
    if not request.host.startswith('localhost') and not request.host.startswith('127.0.0.1'):
        if redirect_uri.startswith('http://'):
            redirect_uri = redirect_uri.replace('http://', 'https://', 1)
    
    if not app_id or not app_secret:
        flash("App ID or App Secret configuration missing.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
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
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
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
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        long_user_token = extend_data.get('access_token')
        
        # 3. Retrieve user accounts (Pages)
        pages_url = "https://graph.facebook.com/v19.0/me/accounts"
        pages_res = requests.get(pages_url, headers={"Authorization": f"Bearer {long_user_token}"})
        pages_data = pages_res.json()
        
        if 'error' in pages_data:
            err_msg = pages_data['error'].get('message', 'Failed to retrieve pages')
            flash(f"Failed to fetch user pages: {err_msg}", "danger")
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        pages_list = pages_data.get('data', [])
        if not pages_list:
            flash("No Facebook pages found for this user account.", "warning")
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
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
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')

@settings_bp.route('/settings/facebook/select', methods=['POST'])
@admin_required
def facebook_select_page():
    page_id = request.form.get('page_id')
    oauth_pages = session.get('oauth_pages')
    
    if not page_id or not oauth_pages:
        flash("Invalid page selection or session expired.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    # Find the page in our stored list
    selected_page = next((p for p in oauth_pages if p['id'] == page_id), None)
    if not selected_page:
        flash("Selected page not found in session.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    # Check if this page is already connected by another client account
    current_user_id = session.get('admin_id')
    existing_conn = Setting.query.filter(
        Setting.key == "page_id",
        Setting.value == selected_page['id'],
        Setting.user_id != current_user_id
    ).first()
    
    if existing_conn:
        flash("صفحة الفيسبوك هذه مرتبطة بالفعل بحساب عميل آخر. يرجى اختيار صفحة أخرى.", "danger")
        session.pop('oauth_pages', None)
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    # Save settings to database
    Setting.set("page_id", selected_page['id'])
    Setting.set("page_access_token", selected_page['access_token'])
    Setting.set("page_name", selected_page['name'])
    
    # Clear pages from session
    session.pop('oauth_pages', None)
    session.pop('oauth_popup', None)
    
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
    return render_template('close_popup.html')


@settings_bp.route('/settings/instagram/login')
@admin_required
def instagram_login():
    session['oauth_popup'] = True
    
    app_id = Setting.get("app_id")
    if not app_id:
        flash("يرجى إعداد معرف تطبيق فيسبوك (App ID) أولاً في الإعدادات.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
    
    redirect_uri = f"{request.url_root.rstrip('/')}/settings/instagram/callback"
    if not request.host.startswith('localhost') and not request.host.startswith('127.0.0.1'):
        if redirect_uri.startswith('http://'):
            redirect_uri = redirect_uri.replace('http://', 'https://', 1)
            
    state = secrets.token_hex(16)
    session['oauth_state'] = state
    
    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': 'instagram_basic,instagram_manage_messages,instagram_manage_comments,pages_show_list,pages_read_engagement'
    }
    fb_oauth_url = f"https://www.facebook.com/v19.0/dialog/oauth?{urlencode(params)}"
    return redirect(fb_oauth_url)

@settings_bp.route('/settings/instagram/callback')
@admin_required
def instagram_callback():
    error = request.args.get('error')
    if error:
        error_desc = request.args.get('error_description', 'Authorization rejected')
        flash(f"Instagram Login error: {error_desc}", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    code = request.args.get('code')
    state = request.args.get('state')
    
    if not state or state != session.get('oauth_state'):
        flash("Invalid state token. Possible CSRF attempt.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    session.pop('oauth_state', None)
    
    app_id = Setting.get("app_id")
    app_secret = Setting.get("app_secret")
    
    if not app_id or not app_secret:
        flash("إعدادات معرف التطبيق (App ID) أو السر (App Secret) غير مكتملة.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
    
    redirect_uri = f"{request.url_root.rstrip('/')}/settings/instagram/callback"
    if not request.host.startswith('localhost') and not request.host.startswith('127.0.0.1'):
        if redirect_uri.startswith('http://'):
            redirect_uri = redirect_uri.replace('http://', 'https://', 1)
            
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
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        short_user_token = token_data.get('access_token')
        
        # Exchange for long-lived User Token
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
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        long_user_token = extend_data.get('access_token')
        
        # Query pages with linked instagram business accounts
        pages_url = "https://graph.facebook.com/v19.0/me/accounts"
        pages_params = {
            'fields': 'id,name,access_token,instagram_business_account{id,name,username}'
        }
        pages_res = requests.get(pages_url, params=pages_params, headers={"Authorization": f"Bearer {long_user_token}"})
        pages_data = pages_res.json()
        
        if 'error' in pages_data:
            err_msg = pages_data['error'].get('message', 'Failed to retrieve accounts')
            flash(f"Failed to fetch accounts: {err_msg}", "danger")
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        pages_list = pages_data.get('data', [])
        ig_accounts = []
        for page in pages_list:
            ig_biz = page.get('instagram_business_account')
            if ig_biz:
                ig_accounts.append({
                    'page_id': page.get('id'),
                    'page_name': page.get('name'),
                    'access_token': page.get('access_token'),
                    'instagram_page_id': ig_biz.get('id'),
                    'instagram_username': ig_biz.get('username') or ig_biz.get('name') or "Instagram Business Account"
                })
                
        if not ig_accounts:
            flash("لم يتم العثور على أي حسابات انستجرام للنشاط (Instagram Business Accounts) مرتبطة بصفحاتك.", "warning")
            session.pop('oauth_popup', None)
            return render_template('close_popup.html')
            
        session['oauth_instagram_accounts'] = ig_accounts
        return render_template('select_instagram.html', accounts=ig_accounts)
        
    except Exception as e:
        flash(f"Error during Instagram Login: {str(e)}", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')

@settings_bp.route('/settings/instagram/select', methods=['POST'])
@admin_required
def instagram_select_account():
    instagram_page_id = request.form.get('instagram_page_id')
    oauth_accounts = session.get('oauth_instagram_accounts')
    
    if not instagram_page_id or not oauth_accounts:
        flash("Invalid selection or session expired.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    selected = next((a for a in oauth_accounts if a['instagram_page_id'] == instagram_page_id), None)
    if not selected:
        flash("Selected Instagram account not found in session.", "danger")
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    current_user_id = session.get('admin_id')
    exists = Setting.query.filter(
        Setting.key == "instagram_page_id",
        Setting.value == selected['instagram_page_id'],
        Setting.user_id != current_user_id
    ).first()
    if exists:
        flash("حساب انستجرام هذا مرتبط بالفعل بحساب عميل آخر.", "danger")
        session.pop('oauth_instagram_accounts', None)
        session.pop('oauth_popup', None)
        return render_template('close_popup.html')
        
    Setting.set("instagram_page_id", selected['instagram_page_id'], user_id=current_user_id)
    Setting.set("instagram_page_access_token", selected['access_token'], user_id=current_user_id)
    Setting.set("instagram_fb_page_id", selected['page_id'], user_id=current_user_id)
    Setting.set("instagram_bot_enabled", "true", user_id=current_user_id)
    Setting.set("instagram_username", selected['instagram_username'], user_id=current_user_id)
    
    session.pop('oauth_instagram_accounts', None)
    session.pop('oauth_popup', None)
    
    flash(f"Instagram Account '@{selected['instagram_username']}' connected successfully!", "success")
    return render_template('close_popup.html')


@settings_bp.route('/settings/add-account', methods=['GET'])
@admin_required
def add_account_page():
    admin_id = session.get('admin_id')
    
    # Check current connections
    fb_connected = False
    page_name = Setting.get("page_name", user_id=admin_id)
    page_id = Setting.get("page_id", user_id=admin_id)
    if page_id and page_id.strip():
        fb_connected = True
        
    ig_connected = False
    instagram_page_id = Setting.get("instagram_page_id", user_id=admin_id)
    instagram_username = Setting.get("instagram_username", user_id=admin_id)
    if instagram_page_id and instagram_page_id.strip():
        ig_connected = True
        
    wa_connected = False
    whatsapp_phone_number_id = Setting.get("whatsapp_phone_number_id", user_id=admin_id)
    if whatsapp_phone_number_id and whatsapp_phone_number_id.strip():
        wa_connected = True
        
    # Get tunnel_url and verify_token to display
    tunnel_url = Setting.get("tunnel_url", "https://yousif-metabot-j49b.onrender.com")
    verify_token = Setting.get("verify_token", "my_verify_token_123")
    
    return render_template(
        'add_account.html',
        fb_connected=fb_connected,
        page_name=page_name,
        ig_connected=ig_connected,
        instagram_page_id=instagram_page_id,
        instagram_username=instagram_username,
        wa_connected=wa_connected,
        whatsapp_phone_number_id=whatsapp_phone_number_id,
        tunnel_url=tunnel_url,
        verify_token=verify_token
    )



@settings_bp.route('/settings/instagram/disconnect', methods=['POST'])
@admin_required
def disconnect_instagram():
    admin_id = session.get('admin_id')
    Setting.set("instagram_page_id", "", user_id=admin_id)
    Setting.set("instagram_page_access_token", "", user_id=admin_id)
    Setting.set("instagram_fb_page_id", "", user_id=admin_id)
    Setting.set("instagram_username", "", user_id=admin_id)
    Setting.set("instagram_bot_enabled", "false", user_id=admin_id)
    flash("Instagram account disconnected.", "info")
    return redirect(url_for('settings.add_account_page'))

@settings_bp.route('/settings/whatsapp/connect', methods=['POST'])
@admin_required
def connect_whatsapp():
    admin_id = session.get('admin_id')
    whatsapp_phone_number_id = request.form.get("whatsapp_phone_number_id", "").strip()
    whatsapp_access_token = request.form.get("whatsapp_access_token", "").strip()
    
    if not whatsapp_phone_number_id or not whatsapp_access_token:
        flash("Both Phone Number ID and Access Token are required.", "danger")
        return redirect(url_for('settings.add_account_page'))
        
    # Check if phone number is connected by another client
    exists = Setting.query.filter(
        Setting.key == "whatsapp_phone_number_id",
        Setting.value == whatsapp_phone_number_id,
        Setting.user_id != admin_id
    ).first()
    if exists:
        flash("حساب واتساب هذا مرتبط بالفعل بحساب عميل آخر.", "danger")
        return redirect(url_for('settings.add_account_page'))
        
    Setting.set("whatsapp_phone_number_id", whatsapp_phone_number_id, user_id=admin_id)
    Setting.set("whatsapp_access_token", whatsapp_access_token, user_id=admin_id)
    # Enable WhatsApp bot automatically
    Setting.set("whatsapp_bot_enabled", "true", user_id=admin_id)
    
    flash("WhatsApp account connected successfully!", "success")
    return redirect(url_for('settings.add_account_page'))

@settings_bp.route('/settings/whatsapp/disconnect', methods=['POST'])
@admin_required
def disconnect_whatsapp():
    admin_id = session.get('admin_id')
    Setting.set("whatsapp_phone_number_id", "", user_id=admin_id)
    Setting.set("whatsapp_access_token", "", user_id=admin_id)
    Setting.set("whatsapp_bot_enabled", "false", user_id=admin_id)
    flash("WhatsApp account disconnected.", "info")
    return redirect(url_for('settings.add_account_page'))

@settings_bp.route('/settings/facebook/disconnect', methods=['POST'])
@admin_required
def disconnect_facebook():
    admin_id = session.get('admin_id')
    Setting.set("page_id", "", user_id=admin_id)
    Setting.set("page_access_token", "", user_id=admin_id)
    Setting.set("page_name", "", user_id=admin_id)
    Setting.set("messenger_bot_enabled", "false", user_id=admin_id)
    flash("Facebook page disconnected.", "info")
    return redirect(url_for('settings.add_account_page'))

