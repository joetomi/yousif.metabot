from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from routes.auth import developer_required
from models import db, Admin, Setting
from datetime import datetime

developer_bp = Blueprint('developer', __name__)

def safe_parse_expiry(expiry_val):
    """Safely parse SQLite or PostgreSQL expiry values into Python datetime objects."""
    if not expiry_val:
        return None
    if isinstance(expiry_val, datetime):
        return expiry_val
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(str(expiry_val).split("+")[0].strip(), fmt)
        except ValueError:
            continue
    return None

@developer_bp.route('/developer/users', methods=['GET'])
@developer_required
def list_users():
    # Query only users with role != 'developer' (clients only)
    users = Admin.query.filter(Admin.role != 'developer').order_by(Admin.created_at.desc()).all()
    
    user_data = []
    for u in users:
        page_name = Setting.get("page_name", "Not Connected", user_id=u.id)
        
        # Safely parse date and format it for the UI
        expiry_dt = safe_parse_expiry(u.subscription_expires_at)
        is_expired = False
        if expiry_dt and expiry_dt < datetime.utcnow():
            is_expired = True
            
        expiry_str = expiry_dt.strftime('%Y-%m-%d') if expiry_dt else ''
            
        user_data.append({
            'user': u,
            'page_name': page_name,
            'is_expired': is_expired,
            'expiry_str': expiry_str
        })
        
    return render_template('developer_users.html', users=user_data)

@developer_bp.route('/developer/users/add', methods=['POST'])
@developer_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    # Role is always 'user' (clients only, cannot add developers)
    role = 'user'
    expiry_str = request.form.get('subscription_expires_at', '').strip()
    
    if not username or not password:
        flash("Username and Password are required.", "danger")
        return redirect(url_for('developer.list_users'))
        
    # Check if username exists
    existing = Admin.query.filter_by(username=username).first()
    if existing:
        flash(f"Username '{username}' already exists.", "warning")
        return redirect(url_for('developer.list_users'))
        
    expiry_dt = None
    if expiry_str:
        try:
            expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
        except ValueError:
            try:
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Invalid expiry date format. Use YYYY-MM-DD.", "warning")
                return redirect(url_for('developer.list_users'))
                
    new_user = Admin(username=username, role=role)
    new_user.set_password(password)
    new_user.subscription_expires_at = expiry_dt
    db.session.add(new_user)
    db.session.commit()
    
    # Seed default user settings
    from config import Config
    Setting.set("messenger_bot_enabled", Config.DEFAULT_MESSENGER_BOT_ENABLED, user_id=new_user.id)
    Setting.set("messenger_bot_tone", Config.DEFAULT_MESSENGER_BOT_TONE, user_id=new_user.id)
    Setting.set("messenger_bot_kb", Config.DEFAULT_MESSENGER_BOT_KB, user_id=new_user.id)
    Setting.set("messenger_bot_fallback", Config.DEFAULT_MESSENGER_BOT_FALLBACK, user_id=new_user.id)
    Setting.set("anti_spam_mode", "every_comment", user_id=new_user.id)
    
    flash("User account created successfully!", "success")
    return redirect(url_for('developer.list_users'))

@developer_bp.route('/developer/users/toggle-status/<int:user_id>', methods=['POST'])
@developer_required
def toggle_status(user_id):
    u = Admin.query.get_or_404(user_id)
    if u.role == 'developer':
        flash("You cannot toggle developer account active status.", "danger")
        return redirect(url_for('developer.list_users'))
        
    u.is_active = not u.is_active
    db.session.commit()
    
    status_str = "activated" if u.is_active else "deactivated"
    flash(f"User '{u.username}' has been {status_str}.", "success")
    return redirect(url_for('developer.list_users'))

@developer_bp.route('/developer/users/extend-subscription/<int:user_id>', methods=['POST'])
@developer_required
def extend_subscription(user_id):
    u = Admin.query.get_or_404(user_id)
    if u.role == 'developer':
        flash("Cannot modify developer account.", "danger")
        return redirect(url_for('developer.list_users'))
        
    expiry_str = request.form.get('subscription_expires_at', '').strip()
    
    expiry_dt = None
    if expiry_str:
        try:
            expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d")
        except ValueError:
            try:
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Invalid date format. Use YYYY-MM-DD.", "danger")
                return redirect(url_for('developer.list_users'))
                
    u.subscription_expires_at = expiry_dt
    db.session.commit()
    
    flash(f"Subscription for '{u.username}' updated successfully.", "success")
    return redirect(url_for('developer.list_users'))

@developer_bp.route('/developer/users/change-password/<int:user_id>', methods=['POST'])
@developer_required
def change_password(user_id):
    u = Admin.query.get_or_404(user_id)
    if u.role == 'developer':
        flash("Cannot modify developer password from this route.", "danger")
        return redirect(url_for('developer.list_users'))
        
    new_password = request.form.get('new_password', '').strip()
    
    if not new_password:
        flash("Password cannot be empty.", "danger")
        return redirect(url_for('developer.list_users'))
        
    u.set_password(new_password)
    db.session.commit()
    
    flash(f"Password for '{u.username}' updated successfully.", "success")
    return redirect(url_for('developer.list_users'))

@developer_bp.route('/developer/users/delete/<int:user_id>', methods=['POST'])
@developer_required
def delete_user(user_id):
    u = Admin.query.get_or_404(user_id)
    if u.role == 'developer':
        flash("You cannot delete a developer account.", "danger")
        return redirect(url_for('developer.list_users'))
        
    db.session.delete(u)
    db.session.commit()
    
    flash(f"User account '{u.username}' deleted successfully.", "success")
    return redirect(url_for('developer.list_users'))

@developer_bp.route('/developer/change-own-password', methods=['POST'])
@developer_required
def change_own_password():
    dev_id = session.get('admin_id')
    dev = Admin.query.get_or_404(dev_id)
    
    if dev.role != 'developer':
        flash("Access denied.", "danger")
        return redirect(url_for('dashboard.index'))
        
    new_password = request.form.get('new_password', '').strip()
    if not new_password:
        flash("Password cannot be empty.", "danger")
        return redirect(url_for('developer.list_users'))
        
    dev.set_password(new_password)
    db.session.commit()
    
    flash("Developer password updated successfully!", "success")
    return redirect(url_for('developer.list_users'))
