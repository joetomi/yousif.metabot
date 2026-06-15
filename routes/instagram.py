from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from routes.auth import admin_required
from models import db, Setting, MessengerFAQ, Post
import google.generativeai as genai

instagram_bp = Blueprint('instagram', __name__)

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
    
    # Retrieve Instagram posts (we scope them using instagram prefix or let them share)
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
    from flask import jsonify
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
