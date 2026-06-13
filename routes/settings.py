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
    app_id = Setting.get("app_id")
    if not app_id:
        flash("Please configure Facebook App ID first.", "danger")
        return redirect(url_for('dashboard.index'))
        
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
        return redirect(url_for('dashboard.index'))
        
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state to prevent CSRF
    if not state or state != session.get('oauth_state'):
        flash("Invalid state token. Possible CSRF attempt.", "danger")
        return redirect(url_for('dashboard.index'))
        
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
        return redirect(url_for('dashboard.index'))
        
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
            return redirect(url_for('dashboard.index'))
            
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
            return redirect(url_for('dashboard.index'))
            
        long_user_token = extend_data.get('access_token')
        
        # 3. Retrieve user accounts (Pages)
        pages_url = "https://graph.facebook.com/v19.0/me/accounts"
        pages_res = requests.get(pages_url, headers={"Authorization": f"Bearer {long_user_token}"})
        pages_data = pages_res.json()
        
        if 'error' in pages_data:
            err_msg = pages_data['error'].get('message', 'Failed to retrieve pages')
            flash(f"Failed to fetch user pages: {err_msg}", "danger")
            return redirect(url_for('dashboard.index'))
            
        pages_list = pages_data.get('data', [])
        if not pages_list:
            flash("No Facebook pages found for this user account.", "warning")
            return redirect(url_for('dashboard.index'))
            
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
        return redirect(url_for('dashboard.index'))

@settings_bp.route('/settings/facebook/select', methods=['POST'])
@admin_required
def facebook_select_page():
    page_id = request.form.get('page_id')
    oauth_pages = session.get('oauth_pages')
    
    if not page_id or not oauth_pages:
        flash("Invalid page selection or session expired.", "danger")
        return redirect(url_for('dashboard.index'))
        
    # Find the page in our stored list
    selected_page = next((p for p in oauth_pages if p['id'] == page_id), None)
    if not selected_page:
        flash("Selected page not found in session.", "danger")
        return redirect(url_for('dashboard.index'))
        
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
    return redirect(url_for('dashboard.index'))
