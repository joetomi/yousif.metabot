from flask import Blueprint, render_template, redirect, url_for, request, flash
from routes.auth import admin_required
from models import db, Setting, MessengerFAQ

messenger_bp = Blueprint('messenger', __name__)

@messenger_bp.route('/messenger', methods=['GET'])
@admin_required
def index():
    # Fetch configurations
    bot_enabled = Setting.get("messenger_bot_enabled", "false")
    bot_tone = Setting.get("messenger_bot_tone", "professional")
    bot_kb = Setting.get("messenger_bot_kb", "")
    
    # Fetch all FAQ rules
    faqs = MessengerFAQ.query.order_by(MessengerFAQ.created_at.desc()).all()
    
    return render_template(
        'messenger.html',
        bot_enabled=bot_enabled,
        bot_tone=bot_tone,
        bot_kb=bot_kb,
        faqs=faqs
    )

@messenger_bp.route('/messenger/save-settings', methods=['POST'])
@admin_required
def save_settings():
    enabled = request.form.get("messenger_bot_enabled", "false")
    tone = request.form.get("messenger_bot_tone", "professional")
    kb = request.form.get("messenger_bot_kb", "").strip()
    
    # Normalize inputs
    enabled_val = "true" if enabled == "true" or enabled == "on" else "false"
    if tone not in ["casual", "professional", "formal", "friendly"]:
        tone = "professional"
        
    Setting.set("messenger_bot_enabled", enabled_val)
    Setting.set("messenger_bot_tone", tone)
    Setting.set("messenger_bot_kb", kb)
    
    flash("Messenger Bot settings saved successfully!", "success")
    return redirect(url_for('messenger.index'))

@messenger_bp.route('/messenger/faq/add', methods=['POST'])
@admin_required
def add_faq():
    keyword = request.form.get("keyword", "").strip()
    response = request.form.get("response", "").strip()
    
    if not keyword or not response:
        flash("Both Keyword and Custom Response are required.", "danger")
        return redirect(url_for('messenger.index'))
        
    # Check if keyword already exists
    exists = MessengerFAQ.query.filter_by(keyword=keyword).first()
    if exists:
        flash(f"A rule with keyword '{keyword}' already exists.", "warning")
        return redirect(url_for('messenger.index'))
        
    new_faq = MessengerFAQ(keyword=keyword, response=response)
    db.session.add(new_faq)
    db.session.commit()
    
    flash("FAQ rule added successfully!", "success")
    return redirect(url_for('messenger.index'))

@messenger_bp.route('/messenger/faq/delete/<int:faq_id>', methods=['POST'])
@admin_required
def delete_faq(faq_id):
    faq = MessengerFAQ.query.get_or_404(faq_id)
    db.session.delete(faq)
    db.session.commit()
    
    flash("FAQ rule deleted successfully!", "success")
    return redirect(url_for('messenger.index'))
