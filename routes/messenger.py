from flask import Blueprint, render_template, redirect, url_for, request, flash
from routes.auth import admin_required
from models import db, Setting, MessengerFAQ

messenger_bp = Blueprint('messenger', __name__)

@messenger_bp.route('/messenger', methods=['GET'])
@admin_required
def index():
    from flask import session
    admin_id = session.get('admin_id')
    
    # Fetch configurations
    bot_enabled = Setting.get("messenger_bot_enabled", "false", user_id=admin_id)
    gemini_enabled = Setting.get("gemini_enabled", "false", user_id=admin_id)
    bot_tone = Setting.get("messenger_bot_tone", "professional", user_id=admin_id)
    bot_kb = Setting.get("messenger_bot_kb", "", user_id=admin_id)
    gemini_api_key = Setting.get("gemini_api_key", "", user_id=admin_id)
    bot_fallback = Setting.get("messenger_bot_fallback", "شكراً لتواصلك معنا. تم استلام رسالتك وسيقوم أحد ممثلي خدمة العملاء بالرد عليك قريباً.", user_id=admin_id)
    
    # Fetch all FAQ rules for this user
    faqs = MessengerFAQ.query.filter_by(admin_id=admin_id).order_by(MessengerFAQ.created_at.desc()).all()
    
    return render_template(
        'messenger.html',
        bot_enabled=bot_enabled,
        gemini_enabled=gemini_enabled,
        bot_tone=bot_tone,
        bot_kb=bot_kb,
        gemini_api_key=gemini_api_key,
        bot_fallback=bot_fallback,
        faqs=faqs
    )

@messenger_bp.route('/messenger/save-settings', methods=['POST'])
@admin_required
def save_settings():
    from flask import session
    admin_id = session.get('admin_id')
    
    enabled = request.form.get("messenger_bot_enabled", "false")
    gemini_enabled = request.form.get("gemini_enabled", "false")
    tone = request.form.get("messenger_bot_tone", "professional")
    kb = request.form.get("messenger_bot_kb", "").strip()
    gemini_api_key = request.form.get("gemini_api_key", "").strip()
    bot_fallback = request.form.get("messenger_bot_fallback", "").strip()
    
    # Normalize inputs
    enabled_val = "true" if enabled == "true" or enabled == "on" else "false"
    gemini_enabled_val = "true" if gemini_enabled == "true" or gemini_enabled == "on" else "false"
    if tone not in ["casual", "professional", "formal", "friendly"]:
        tone = "professional"
        
    Setting.set("messenger_bot_enabled", enabled_val, user_id=admin_id)
    Setting.set("gemini_enabled", gemini_enabled_val, user_id=admin_id)
    Setting.set("messenger_bot_tone", tone, user_id=admin_id)
    Setting.set("messenger_bot_kb", kb, user_id=admin_id)
    Setting.set("gemini_api_key", gemini_api_key, user_id=admin_id)
    Setting.set("messenger_bot_fallback", bot_fallback, user_id=admin_id)
    
    flash("Messenger Bot settings saved successfully!", "success")
    return redirect(url_for('messenger.index'))

@messenger_bp.route('/messenger/test-gemini', methods=['POST'])
@admin_required
def test_gemini():
    from flask import jsonify
    import google.generativeai as genai
    
    data = request.get_json() or {}
    api_key = data.get("gemini_api_key", "").strip()
    
    if not api_key:
        return jsonify({"status": "error", "message": "يرجى إدخال مفتاح API أولاً."}), 400
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        
        # Call a very quick generation to verify connection
        response = model.generate_content("Say 'Connection OK' in one word.")
        if response and response.text:
            return jsonify({
                "status": "success",
                "message": "تم الاتصال بسيرفرات Gemini بنجاح والذكاء الاصطناعي جاهز للرد على الزبائن!"
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

@messenger_bp.route('/messenger/faq/add', methods=['POST'])
@admin_required
def add_faq():
    from flask import session
    admin_id = session.get('admin_id')
    
    keyword = request.form.get("keyword", "").strip()
    response = request.form.get("response", "").strip()
    
    if not keyword or not response:
        flash("Both Keyword and Custom Response are required.", "danger")
        return redirect(url_for('messenger.index'))
        
    # Check if keyword already exists for this user
    exists = MessengerFAQ.query.filter_by(keyword=keyword, admin_id=admin_id).first()
    if exists:
        flash(f"A rule with keyword '{keyword}' already exists.", "warning")
        return redirect(url_for('messenger.index'))
        
    new_faq = MessengerFAQ(keyword=keyword, response=response, admin_id=admin_id)
    db.session.add(new_faq)
    db.session.commit()
    
    flash("FAQ rule added successfully!", "success")
    return redirect(url_for('messenger.index'))

@messenger_bp.route('/messenger/faq/delete/<int:faq_id>', methods=['POST'])
@admin_required
def delete_faq(faq_id):
    from flask import session
    admin_id = session.get('admin_id')
    
    faq = MessengerFAQ.query.filter_by(id=faq_id, admin_id=admin_id).first_or_404()
    db.session.delete(faq)
    db.session.commit()
    
    flash("FAQ rule deleted successfully!", "success")
    return redirect(url_for('messenger.index'))
