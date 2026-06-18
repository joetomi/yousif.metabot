from flask import Blueprint, render_template, redirect, url_for, request, flash, session, jsonify
from datetime import datetime
import requests
import json
from routes.auth import admin_required
from models import db, Setting, MessengerFAQ, Post
import google.generativeai as genai
from services.comment_processor import log_activity, trigger_dashboard_update

instagram_bp = Blueprint('instagram', __name__)

def fetch_instagram_posts(access_token, instagram_page_id, limit=25):
    """Fetches recent posts/reels from the linked Instagram Business Account."""
    if not access_token or not instagram_page_id:
        return []
        
    url = f"https://graph.facebook.com/v19.0/{instagram_page_id}/media"
    params = {
        "access_token": access_token,
        "fields": "id,caption,timestamp,comments_count",
        "limit": limit
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        # Call logging
        try:
            from models import ApiLog
            log_entry = ApiLog(
                endpoint=f"/{instagram_page_id}/media",
                method="GET",
                status_code=response.status_code,
                request_payload=json.dumps(params) if params else None,
                response_payload=response.text
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as log_ex:
            db.session.rollback()
            print(f"Error logging IG API call: {log_ex}")
            
        if response.status_code == 200:
            data = response.json()
            posts_list = []
            for item in data.get("data", []):
                posts_list.append({
                    "id": f"ig_{item.get('id')}",
                    "message": item.get("caption", "[No Caption text]"),
                    "created_time": item.get("timestamp"),
                    "comment_count": item.get("comments_count", 0)
                })
            return posts_list
        else:
            print(f"Instagram API returned status {response.status_code}: {response.text}")
            return []
    except Exception as e:
        print(f"Error fetching Instagram posts: {e}")
        return []

@instagram_bp.route('/instagram', methods=['GET'])
@admin_required
def index():
    admin_id = session.get('admin_id')
    
    bot_enabled = Setting.get("instagram_bot_enabled", "false", user_id=admin_id)
    gemini_enabled = Setting.get("instagram_gemini_enabled", "false", user_id=admin_id)
    bot_tone = Setting.get("instagram_bot_tone", "professional", user_id=admin_id)
    bot_kb = Setting.get("instagram_bot_kb", "", user_id=admin_id)
    gemini_api_key = Setting.get("instagram_gemini_api_key", "", user_id=admin_id)
    bot_fallback = Setting.get("instagram_bot_fallback", "شكراً لتواصلك معنا على انستجرام. سنقوم بالرد عليك قريباً.", user_id=admin_id)
    
    # Retrieve Instagram posts
    posts = Post.query.filter(Post.user_id == admin_id, Post.id.like("ig_%")).all()
    
    return render_template(
        'instagram_settings.html',
        bot_enabled=bot_enabled,
        gemini_enabled=gemini_enabled,
        bot_tone=bot_tone,
        bot_kb=bot_kb,
        gemini_api_key=gemini_api_key,
        bot_fallback=bot_fallback,
        posts=posts
    )

@instagram_bp.route('/instagram/save-settings', methods=['POST'])
@admin_required
def save_settings():
    admin_id = session.get('admin_id')
    
    enabled = request.form.get("instagram_bot_enabled", "false")
    gemini_enabled = request.form.get("instagram_gemini_enabled", "false")
    tone = request.form.get("instagram_bot_tone", "professional")
    kb = request.form.get("instagram_bot_kb", "").strip()
    gemini_api_key = request.form.get("instagram_gemini_api_key", "").strip()
    bot_fallback = request.form.get("instagram_bot_fallback", "").strip()
    
    enabled_val = "true" if enabled in ["true", "on"] else "false"
    gemini_enabled_val = "true" if gemini_enabled in ["true", "on"] else "false"
    if tone not in ["casual", "professional", "formal", "friendly"]:
        tone = "professional"
        
    Setting.set("instagram_bot_enabled", enabled_val, user_id=admin_id)
    Setting.set("instagram_gemini_enabled", gemini_enabled_val, user_id=admin_id)
    Setting.set("instagram_bot_tone", tone, user_id=admin_id)
    Setting.set("instagram_bot_kb", kb, user_id=admin_id)
    Setting.set("instagram_gemini_api_key", gemini_api_key, user_id=admin_id)
    Setting.set("instagram_bot_fallback", bot_fallback, user_id=admin_id)
    
    flash("Instagram settings saved successfully!", "success")
    return redirect(url_for('instagram.index'))

@instagram_bp.route('/instagram/test-gemini', methods=['POST'])
@admin_required
def test_gemini():
    data = request.get_json() or {}
    api_key = data.get("gemini_api_key", "").strip()
    
    if not api_key:
        return jsonify({"status": "error", "message": "يرجى إدخال مفتاح API أولاً."}), 400
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content("Say 'Connection OK' in one word.")
        if response and response.text:
            return jsonify({
                "status": "success",
                "message": "تم الاتصال بسيرفرات Gemini بنجاح والذكاء الاصطناعي جاهز للرد على انستجرام!"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "استجاب السيرفر باستجابة فارغة. يرجى التحقق من صلاحيات المفتاح."
            }), 400
    except Exception as e:
        error_msg = str(e)
        if "API_KEY_INVALID" in error_msg or "invalid" in error_msg.lower():
            error_msg = "مفتاح API غير صالح. يرجى التأكد من نسخه بشكل صحيح."
        elif "quota" in error_msg.lower() or "limit" in error_msg.lower():
            error_msg = "تم تجاوز الحصة المجانية للمفتاح (Quota Exceeded)."
        return jsonify({
            "status": "error",
            "message": f"فشل الاتصال: {error_msg}"
        }), 400

@instagram_bp.route('/instagram/posts', methods=['GET'])
@admin_required
def instagram_posts():
    admin_id = session.get('admin_id')
    posts = Post.query.filter((Post.user_id == admin_id) | (Post.user_id == None)).filter(Post.id.like("ig_%")).order_by(Post.created_time.desc()).all()
    return render_template('instagram_posts.html', posts=posts)

@instagram_bp.route('/instagram/posts/refresh', methods=['POST'])
@admin_required
def refresh_instagram_posts():
    admin_id = session.get('admin_id')
    
    instagram_page_access_token = Setting.get("instagram_page_access_token", user_id=admin_id)
    instagram_page_id = Setting.get("instagram_page_id", user_id=admin_id)
    
    if not instagram_page_access_token or not instagram_page_id:
        return jsonify({"status": "error", "message": "يرجى ربط حساب انستجرام أولاً."}), 400
        
    posts_data = fetch_instagram_posts(instagram_page_access_token, instagram_page_id, limit=25)
    
    if not posts_data:
        return jsonify({"status": "error", "message": "فشل جلب منشورات وريلز انستجرام. يرجى التحقق من الاتصال والصلاحيات."}), 400
        
    try:
        new_count = 0
        updated_count = 0
        
        for p_data in posts_data:
            p_id = p_data["id"]
            post = Post.query.filter((Post.id == p_id) & ((Post.user_id == admin_id) | (Post.user_id == None))).first()
            
            c_time = None
            if p_data.get("created_time"):
                try:
                    c_time = datetime.strptime(p_data["created_time"].split("+")[0], "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    pass
            
            if post:
                post.comment_count = p_data["comment_count"]
                post.message = p_data["message"]
                updated_count += 1
            else:
                post = Post(
                    id=p_id,
                    message=p_data["message"],
                    created_time=c_time,
                    comment_count=p_data["comment_count"],
                    is_monitored=False,
                    user_id=admin_id
                )
                db.session.add(post)
                new_count += 1
                
        db.session.commit()
        
        log_activity(
            event_type="SYSTEM",
            status="SUCCESS",
            message=f"Manual Instagram Posts Refresh: {new_count} new, {updated_count} updated posts."
        )
        trigger_dashboard_update(admin_id=admin_id)
        
        return jsonify({
            "status": "success", 
            "message": f"تم تحديث المنشورات بنجاح! تم مزامنة {new_count + updated_count} منشور وريلز."
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": f"خطأ في قاعدة البيانات: {str(e)}"}), 500
