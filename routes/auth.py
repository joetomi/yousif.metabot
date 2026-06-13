from flask import Blueprint, render_template, redirect, url_for, request, session, flash
from functools import wraps
from models import db, Admin

auth_bp = Blueprint('auth', __name__)

def admin_required(f):
    """Decorator to protect routes from unauthenticated access."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session or not session['admin_logged_in']:
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def developer_required(f):
    """Decorator to protect routes from non-developer access."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session or not session['admin_logged_in']:
            return redirect(url_for('auth.login', next=request.url))
        admin = Admin.query.get(session.get('admin_id'))
        if not admin or admin.role != 'developer':
            flash('Access denied. Developer role required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('dashboard.index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and admin.check_password(password):
            session.clear()
            session['admin_logged_in'] = True
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            session.permanent = True  # session persistent based on config
            
            # Default to Arabic if no language set
            if 'lang' not in session:
                session['lang'] = 'ar'
                
            flash('Logged in successfully!', 'success')
            
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard.index'))
        else:
            flash('Invalid username or password.', 'danger')
            
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/toggle-lang')
def toggle_lang():
    """Toggles language setting in session."""
    current_lang = session.get('lang', 'ar')
    session['lang'] = 'ar' if current_lang == 'en' else 'en'
    return redirect(request.referrer or url_for('dashboard.index'))

@auth_bp.route('/privacy-policy')
def privacy_policy():
    """Public route to display the Privacy Policy and Data Deletion instructions."""
    if 'lang' not in session:
        session['lang'] = 'ar'
    return render_template('privacy.html')
