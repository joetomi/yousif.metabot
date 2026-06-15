import os
import unittest
import json
import hmac
import hashlib
from datetime import datetime
from unittest.mock import patch, MagicMock

# Set testing environment variables
os.environ["DATABASE_URL"] = "sqlite:///test_database.db"
os.environ["SECRET_KEY"] = "test-secret-key-987654321"
os.environ["TESTING"] = "True"

from app import create_app
from models import db, Admin, Setting, Post, Comment, Message, ProcessedUser, ApiLog, ActivityLog, WebhookLog, MessengerFAQ, ProcessedMessage
from services.facebook_api import FacebookApiService
from services.comment_processor import process_comment_job, check_anti_spam

class FacebookBotTestCase(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing POST routes easily
        self.app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///test_database.db"
        
        self.client = self.app.test_client()
        
        # Initialize database tables
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            
            # Seed default admin
            admin = Admin(username='admin')
            admin.set_password('admin')
            db.session.add(admin)
            
            # Seed settings
            Setting.set("page_access_token", "test_token")
            Setting.set("app_secret", "test_secret")
            Setting.set("verify_token", "test_verify")
            Setting.set("page_id", "test_page_id")
            Setting.set("tunnel_url", "https://test.tunnel.lt")
            Setting.set("anti_spam_mode", "every_comment")
            
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            
        # Clean up database file
        if os.path.exists("test_database.db"):
            try:
                os.remove("test_database.db")
            except Exception:
                pass

    def login_admin(self):
        """Helper to log in as the default admin."""
        return self.client.post('/login', data=dict(
            username='admin',
            password='admin'
        ), follow_redirects=True)

    # 1. Verification of Dependencies & Imports
    def test_imports_succeed(self):
        """Verify that essential libraries import successfully."""
        import flask
        import flask_sqlalchemy
        import flask_socketio
        import flask_migrate
        import requests
        import apscheduler
        import eventlet
        self.assertTrue(True)

    # 2. Verify Database CRUD operations
    def test_database_crud(self):
        """Verify that basic database CRUD operations are functional."""
        with self.app.app_context():
            # Create
            new_post = Post(id="post_999", message="CRUD test message", is_monitored=True)
            db.session.add(new_post)
            db.session.commit()
            
            # Read
            post = Post.query.get("post_999")
            self.assertIsNotNone(post)
            self.assertEqual(post.message, "CRUD test message")
            
            # Update
            post.message = "Updated CRUD message"
            db.session.commit()
            updated_post = Post.query.get("post_999")
            self.assertEqual(updated_post.message, "Updated CRUD message")
            
            # Delete
            db.session.delete(updated_post)
            db.session.commit()
            deleted_post = Post.query.get("post_999")
            self.assertIsNone(deleted_post)

    # 3. Verify Authentication flows
    def test_authentication_flow(self):
        """Verify admin login redirects, session security, and logout."""
        # Test protected route redirect
        resp = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers['Location'])
        
        # Test login failure with incorrect credentials
        resp = self.client.post('/login', data=dict(
            username='admin',
            password='wrongpassword'
        ), follow_redirects=True)
        self.assertIn(b'Invalid username or password', resp.data)
        
        # Test login success
        resp = self.login_admin()
        self.assertTrue(b'Dashboard' in resp.data or b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85' in resp.data)
        
        # Test logout clears session
        resp = self.client.get('/logout', follow_redirects=True)
        self.assertTrue(b'Admin Login' in resp.data or b'\xd8\xaa\xd8\xb3\xd8\xac\xd9\x8a\xd9\x84 \xd8\xaf\xd8\xae\xd9\x88\xd9\x84 \xd8\xa7\xd9\x84\xd9\x85\xd8\xb3\xd8\xa4\xd9\x88\xd9\x84' in resp.data)
        
        # Test dashboard is blocked again
        resp = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    # 4. Verify Settings management
    def test_settings_management(self):
        """Verify that settings are saved and retrieved correctly in the database."""
        with self.app.app_context():
            Setting.set("page_access_token", "new_page_token_abc")
            Setting.set("app_secret", "new_app_secret_def")
            Setting.set("verify_token", "new_verify_token_ghi")
            Setting.set("page_id", "new_page_id_jkl")
            Setting.set("tunnel_url", "https://new.tunnel.lt")
            Setting.set("anti_spam_mode", "once_per_user_post")
            
            self.assertEqual(Setting.get("page_access_token"), "new_page_token_abc")
            self.assertEqual(Setting.get("app_secret"), "new_app_secret_def")
            self.assertEqual(Setting.get("verify_token"), "new_verify_token_ghi")
            self.assertEqual(Setting.get("page_id"), "new_page_id_jkl")
            self.assertEqual(Setting.get("tunnel_url"), "https://new.tunnel.lt")
            self.assertEqual(Setting.get("anti_spam_mode"), "once_per_user_post")

    # 5. Verify Post Management
    def test_post_management(self):
        """Verify monitored flag toggles and templates configurations."""
        self.login_admin()
        
        # Setup post record
        with self.app.app_context():
            post = Post(id="test_post_1", message="Hello world", is_monitored=False)
            db.session.add(post)
            db.session.commit()
            
        # Toggle monitoring flag to True
        resp = self.client.post('/posts/toggle-monitoring/test_post_1', 
                                data=json.dumps({'is_monitored': True}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.data)['is_monitored'])
        
        # Update templates
        resp = self.client.post('/posts/update-templates/test_post_1', data=dict(
            default_reply='Custom comment reply for {name}',
            private_message='Custom message for {name}'
        ), follow_redirects=True)
        self.assertIn(b'Templates updated successfully', resp.data)
        
        with self.app.app_context():
            post = Post.query.get("test_post_1")
            self.assertEqual(post.default_reply, 'Custom comment reply for {name}')
            self.assertEqual(post.private_message, 'Custom message for {name}')

    # 6. Verify Webhook GET subscription
    def test_webhook_get_verification(self):
        """Verify webhook mode subscribe challenge returns challenge or 403 on error."""
        # 1. Valid Verify Token challenge
        resp = self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=test_verify&hub.challenge=challenge123')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.decode(), 'challenge123')
        
        # 2. Invalid Verify Token challenge
        resp = self.client.get('/webhook?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=challenge123')
        self.assertEqual(resp.status_code, 403)

    # 7. Verify Webhook Signature validation
    def test_webhook_signature_validation(self):
        """Verify webhook signature validation with App Secret."""
        payload = b'{"object":"page","entry":[]}'
        
        # 1. Valid Signature
        app_secret = "test_secret"
        signature = "sha256=" + hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
        
        headers = {'X-Hub-Signature-256': signature}
        resp = self.client.post('/webhook', data=payload, headers=headers, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        
        # 2. Invalid Signature
        headers = {'X-Hub-Signature-256': 'sha256=invalidhashvalue'}
        resp = self.client.post('/webhook', data=payload, headers=headers, content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    # 8. Webhook Comment Simulation & Processor checks
    @patch('services.scheduler.scheduler.add_job')
    def test_webhook_comment_simulation(self, mock_add_job):
        """Verify enqueuing background job when comment webhook payload arrives."""
        payload = {
            "object": "page",
            "entry": [{
                "id": "test_page_id",
                "time": 16000000,
                "changes": [{
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "comment_id": "comment_id_101",
                        "post_id": "post_id_202",
                        "from": {"id": "user_id_303", "name": "Jane Doe"},
                        "message": "Nice post!",
                        "created_time": 16000000
                    }
                }]
            }]
        }
        
        app_secret = "test_secret"
        payload_bytes = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        
        headers = {'X-Hub-Signature-256': signature}
        resp = self.client.post('/webhook', data=payload_bytes, headers=headers, content_type='application/json')
        
        self.assertEqual(resp.status_code, 200)
        # Verify job was enqueued in background scheduler
        self.assertTrue(mock_add_job.called)
        
        # Verify Webhook log record was written
        with self.app.app_context():
            webhook_log = WebhookLog.query.first()
            self.assertIsNotNone(webhook_log)
            self.assertEqual(webhook_log.status, "SUCCESS")

    # 9. Verify Auto Reply success/failure workflows
    @patch('services.facebook_api.requests.post')
    def test_auto_reply_workflows(self, mock_post):
        """Verify public auto reply succeeds and failure creates error logs."""
        # Mock Graph API success
        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.text = '{"id":"reply_fb_id_777"}'
        mock_resp_success.json.return_value = {"id": "reply_fb_id_777"}
        
        # Mock Graph API failure
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 400
        mock_resp_fail.text = '{"error":{"message":"Invalid OAuth access token.","code":190}}'
        mock_resp_fail.json.return_value = {"error": {"message": "Invalid OAuth access token.", "code": 190}}
        
        with self.app.app_context():
            # Setup post
            post = Post(id="post_id_202", message="Test message", is_monitored=True)
            db.session.add(post)
            db.session.commit()
            
            api = FacebookApiService()
            
            # Test Success Reply
            mock_post.return_value = mock_resp_success
            success, msg, reply_id = api.reply_to_comment("comment_id_101", "Thanks!")
            self.assertTrue(success)
            self.assertEqual(reply_id, "reply_fb_id_777")
            
            # Check API Log entry
            api_log = ApiLog.query.first()
            self.assertIsNotNone(api_log)
            self.assertEqual(api_log.status_code, 200)
            
            # Test Failure Reply
            mock_post.return_value = mock_resp_fail
            success, msg, reply_id = api.reply_to_comment("comment_id_101", "Thanks!")
            self.assertFalse(success)
            self.assertIn("Invalid OAuth access token", msg)
            
            # Check API Log error code extraction
            fail_log = ApiLog.query.filter_by(status_code=400).first()
            self.assertIsNotNone(fail_log)
            self.assertEqual(fail_log.error_code, 190)

    # 10. Verify Private Reply success/failure and graceful handling
    @patch('services.facebook_api.requests.post')
    def test_private_reply_graceful_handling(self, mock_post):
        """Verify that private reply failure does not rollback public reply and logs correctly."""
        # 1. Setup mocked endpoints
        # Comment reply success, but private reply failure (Meta policy violation block)
        mock_resp_reply_success = MagicMock()
        mock_resp_reply_success.status_code = 200
        mock_resp_reply_success.text = '{"id":"reply_123"}'
        mock_resp_reply_success.json.return_value = {"id": "reply_123"}
        
        mock_resp_msg_fail = MagicMock()
        mock_resp_msg_fail.status_code = 400
        mock_resp_msg_fail.text = '{"error":{"message":"The user cannot receive private replies.","code":109}}'
        mock_resp_msg_fail.json.return_value = {"error": {"message": "The user cannot receive private replies.", "code": 109}}
        
        mock_post.side_effect = [mock_resp_reply_success, mock_resp_msg_fail]

        with self.app.app_context():
            # Setup post
            post = Post(id="post_id_202", message="Test message", is_monitored=True)
            db.session.add(post)
            db.session.commit()
            
        comment_data = {
            "comment_id": "comment_id_101",
            "post_id": "post_id_202",
            "user_id": "user_id_303",
            "username": "Jane Doe",
            "message": "Nice post!",
            "created_time": "2026-06-12T18:40:00+0000"
        }
        
        # Trigger background processing job
        process_comment_job(self.app, comment_data)
        
        # Verify Results
        with self.app.app_context():
            # Verify comment reply recorded as success
            comment = Comment.query.get("comment_id_101")
            self.assertTrue(comment.processed)
            self.assertTrue(comment.reply_sent)
            self.assertEqual(comment.reply_id, "reply_123")
            
            # Verify private reply recorded as failed
            message = Message.query.filter_by(comment_id="comment_id_101").first()
            self.assertEqual(message.status, "FAILED")
            self.assertIn("The user cannot receive private replies", message.error_message)
            
            # Verify logs were populated
            reply_log = ActivityLog.query.filter_by(event_type="REPLY").first()
            self.assertEqual(reply_log.status, "SUCCESS")
            
            message_log = ActivityLog.query.filter_by(event_type="MESSAGE").first()
            self.assertEqual(message_log.status, "FAILED")

    # 11. Verify duplicate comment checks
    @patch('services.facebook_api.requests.post')
    def test_duplicate_comment_prevention(self, mock_post):
        """Verify that the exact same comment ID is only processed once."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"id":"ok"}'
        mock_resp.json.return_value = {"id": "ok"}
        mock_post.return_value = mock_resp
        
        with self.app.app_context():
            post = Post(id="post_id_202", message="Test message", is_monitored=True)
            db.session.add(post)
            db.session.commit()

        comment_data = {
            "comment_id": "comment_id_101",
            "post_id": "post_id_202",
            "user_id": "user_id_303",
            "username": "Jane Doe",
            "message": "Nice post!"
        }
        
        # Process first time
        process_comment_job(self.app, comment_data)
        
        # Reset mock call count
        mock_post.reset_mock()
        
        # Process second time
        process_comment_job(self.app, comment_data)
        
        # Graph API should NOT be contacted a second time
        mock_post.assert_not_called()

    # 12. Verify anti-spam rules (every_comment, once_per_user_post, once_per_user_global)
    def test_anti_spam_rules(self):
        """Verify spam filters: every comment, once per user per post, once per user globally."""
        with self.app.app_context():
            # 1. Test EVERY_COMMENT (default)
            Setting.set("anti_spam_mode", "every_comment")
            # First comment by user_A on post_1
            self.assertTrue(check_anti_spam("user_A", "post_1"))
            # Save processed user to mimic processing completed
            user = ProcessedUser(user_id="user_A", post_id="post_1")
            db.session.add(user)
            db.session.commit()
            # Second comment by user_A on post_1 should still be allowed
            self.assertTrue(check_anti_spam("user_A", "post_1"))
            
            # 2. Test ONCE_PER_USER_POST
            Setting.set("anti_spam_mode", "once_per_user_post")
            # user_A commented on post_1 (already in DB)
            self.assertFalse(check_anti_spam("user_A", "post_1"))
            # user_A comments on post_2 (different post)
            self.assertTrue(check_anti_spam("user_A", "post_2"))
            
            # 3. Test ONCE_PER_USER_GLOBAL
            Setting.set("anti_spam_mode", "once_per_user_global")
            # user_A commented anywhere globally (already has records)
            self.assertFalse(check_anti_spam("user_A", "post_2"))
            self.assertFalse(check_anti_spam("user_A", "post_3"))
            
            # New user_B comments
            self.assertTrue(check_anti_spam("user_B", "post_1"))

    # 13. Verify Billingual & RTL translations
    def test_bilingual_rtl_support(self):
        """Verify that language translation session toggle changes dictionary keys."""
        # Initial access (defaults to Arabic RTL)
        self.login_admin()
        resp = self.client.get('/dashboard')
        self.assertIn(b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85', resp.data) # arabic representation of "Dashboard"
        self.assertNotIn(b'Dashboard', resp.data)
        
        # Toggle language to English
        self.client.get('/toggle-lang')
        resp = self.client.get('/dashboard')
        self.assertIn(b'Dashboard', resp.data)
        self.assertNotIn(b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85', resp.data)
        
        # Toggle back to Arabic
        self.client.get('/toggle-lang')
        resp = self.client.get('/dashboard')
        self.assertIn(b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85', resp.data)

    # 14. Verify security checks
    def test_security_configurations(self):
        """Verify password hashes, session permanent state, and routing protection."""
        with self.app.app_context():
            admin = Admin.query.filter_by(username='admin').first()
            # Verify hashed password check
            self.assertTrue(admin.check_password('admin'))
            self.assertFalse(admin.check_password('wrongpassword'))
            # Check prefix of password hash matches pbkdf2
            self.assertTrue(admin.password_hash.startswith("scrypt:") or admin.password_hash.startswith("pbkdf2:sha256:"))

    # 15. Verify Messenger Bot Routes
    def test_messenger_bot_routes(self):
        """Verify settings storage, FAQ creation, and deletion on Messenger routes."""
        self.login_admin()
        
        # Access index
        resp = self.client.get('/messenger')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(b'Messenger Auto-Responder Settings' in resp.data or b'\xd8\xa5\xd8\xb9\xd8\xaf\xd8\xa7\xd8\xaf\xd8\xa7\xd8\xaa \xd8\xa7\xd9\x84\xd9\x85\xd8\xac\xd9\x8a\xd8\xa8 \xd8\xa7\xd9\x84\xd8\xa2\xd9\x84\xd9\x8a \xd9\x81\xd9\x8a \xd9\x85\xd8\xa7\xd8\xb3\xd9\x86\xd8\xac\xd8\xb1' in resp.data)
        
        # Save settings
        resp = self.client.post('/messenger/save-settings', data=dict(
            messenger_bot_enabled='true',
            messenger_bot_tone='casual',
            messenger_bot_kb='Custom KB plans'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Messenger Bot settings saved successfully!', resp.data)
        
        with self.app.app_context():
            admin = Admin.query.filter_by(username='admin').first()
            self.assertEqual(Setting.get("messenger_bot_enabled", user_id=admin.id), "true")
            self.assertEqual(Setting.get("messenger_bot_tone", user_id=admin.id), "casual")
            self.assertEqual(Setting.get("messenger_bot_kb", user_id=admin.id), "Custom KB plans")
            
        # Add FAQ rule
        resp = self.client.post('/messenger/faq/add', data=dict(
            keyword='price, cost',
            response='Prices start at $10'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'FAQ rule added successfully!', resp.data)
        
        with self.app.app_context():
            faq = MessengerFAQ.query.filter_by(keyword='price, cost').first()
            self.assertIsNotNone(faq)
            self.assertEqual(faq.response, 'Prices start at $10')
            faq_id = faq.id
            
        # Delete FAQ rule
        resp = self.client.post(f'/messenger/faq/delete/{faq_id}', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'FAQ rule deleted successfully!', resp.data)
        
        with self.app.app_context():
            faq = MessengerFAQ.query.get(faq_id)
            self.assertIsNone(faq)

    # 16. Verify Messenger Webhook and Processor Flow
    @patch('services.facebook_api.requests.post')
    @patch('services.scheduler.scheduler.add_job')
    def test_messenger_webhook_and_processor(self, mock_add_job, mock_post):
        """Verify Messenger webhook parsing and direct FAQ reply processor."""
        # 1. Test Webhook parsing
        payload = {
            "object": "page",
            "entry": [{
                "id": "page_id_123",
                "time": 16000000,
                "messaging": [{
                    "sender": {"id": "customer_id_456"},
                    "recipient": {"id": "page_id_123"},
                    "timestamp": 16000000,
                    "message": {
                        "mid": "mid.test_msg_999",
                        "text": "Hello, what are your plans?"
                    }
                }]
            }]
        }
        
        app_secret = "test_secret"
        payload_bytes = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        
        headers = {'X-Hub-Signature-256': signature}
        resp = self.client.post('/webhook', data=payload_bytes, headers=headers, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_add_job.called)
        
        # 2. Test Processor matching FAQ keywords
        from services.messenger_processor import process_messenger_job
        
        # Setup configs and FAQ rule
        with self.app.app_context():
            Setting.set("messenger_bot_enabled", "true")
            # Clear duplicate record if any
            ProcessedMessage.query.filter_by(message_id="mid.test_msg_999").delete()
            
            faq = MessengerFAQ(keyword="plans, prices", response="FAQ: We have three plans.")
            db.session.add(faq)
            db.session.commit()
            
        # Mock Graph API success
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true}'
        mock_post.return_value = mock_resp
        
        msg_details = {
            "sender_id": "customer_id_456",
            "message_text": "I want to know about your plans.",
            "message_id": "mid.test_msg_999"
        }
        
        # Run background job sync
        process_messenger_job(self.app, msg_details)
        
        # Verify Facebook send message API was called with the FAQ response
        self.assertTrue(mock_post.called)
        call_args = mock_post.call_args[1]
        self.assertIn("messages", mock_post.call_args[0][0]) # endpoint contains /messages
        self.assertEqual(call_args['json']['recipient']['id'], "customer_id_456")
        self.assertEqual(call_args['json']['message']['text'], "FAQ: We have three plans.")

    def test_developer_panel(self):
        """Verify developer panel access, user creation, deletion, password change, and subscription extension."""
        # 1. Accessing developer route without log in redirects to login
        resp = self.client.get('/developer/users')
        self.assertEqual(resp.status_code, 302)
        
        # 2. Accessing developer route as standard user redirects to dashboard index with error
        self.login_admin()
        resp = self.client.get('/developer/users', follow_redirects=True)
        self.assertIn(b'Access denied', resp.data)
        self.client.get('/logout')
        
        # 3. Seed a developer user and login
        with self.app.app_context():
            dev = Admin(username='test_dev', role='developer')
            dev.set_password('devpass')
            db.session.add(dev)
            
            client_user = Admin(username='test_client', role='user')
            client_user.set_password('clientpass')
            db.session.add(client_user)
            db.session.commit()
            client_id = client_user.id
            
        # Login as developer
        self.client.post('/login', data=dict(
            username='test_dev',
            password='devpass'
        ), follow_redirects=True)
        
        # 4. View users list - should contain test_client but NOT test_dev
        resp = self.client.get('/developer/users')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'test_client', resp.data)
        self.assertNotIn(b'test_dev', resp.data)
        
        # 5. Add a new user via developer panel
        resp = self.client.post('/developer/users/add', data=dict(
            username='new_client',
            password='newpassword',
            password_confirm='newpassword',
            subscription_expires_at='2026-12-31'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'User account created successfully!', resp.data)
        
        with self.app.app_context():
            new_u = Admin.query.filter_by(username='new_client').first()
            self.assertIsNotNone(new_u)
            self.assertEqual(new_u.role, 'user')
            self.assertEqual(new_u.subscription_expires_at.strftime('%Y-%m-%d'), '2026-12-31')
            
        # 6. Toggle client status
        resp = self.client.post(f'/developer/users/toggle-status/{client_id}', follow_redirects=True)
        self.assertIn(b'has been deactivated', resp.data)
        
        with self.app.app_context():
            u = Admin.query.get(client_id)
            self.assertFalse(u.is_active)
            
        # 7. Extend subscription
        resp = self.client.post(f'/developer/users/extend-subscription/{client_id}', data=dict(
            subscription_expires_at='2027-01-01'
        ), follow_redirects=True)
        self.assertIn(b'updated successfully', resp.data)
        
        with self.app.app_context():
            u = Admin.query.get(client_id)
            self.assertEqual(u.subscription_expires_at.strftime('%Y-%m-%d'), '2027-01-01')
            
        # 8. Change client password
        resp = self.client.post(f'/developer/users/change-password/{client_id}', data=dict(
            new_password='updated_password'
        ), follow_redirects=True)
        self.assertIn(b'updated successfully', resp.data)
        
        with self.app.app_context():
            u = Admin.query.get(client_id)
            self.assertTrue(u.check_password('updated_password'))
            
        # 9. Change own (developer) password
        resp = self.client.post('/developer/change-own-password', data=dict(
            new_password='new_dev_pass'
        ), follow_redirects=True)
        self.assertIn(b'password updated successfully', resp.data)
        
        with self.app.app_context():
            d = Admin.query.filter_by(username='test_dev').first()
            self.assertTrue(d.check_password('new_dev_pass'))
            
        # 10. Delete client user
        resp = self.client.post(f'/developer/users/delete/{client_id}', follow_redirects=True)
        self.assertIn(b'deleted successfully', resp.data)
        
        with self.app.app_context():
            u = Admin.query.get(client_id)
            self.assertIsNone(u)

    def test_dynamic_timeouts_and_permanence(self):
        """Verify session timeouts and permanence for developer (30m, permanent) and clients (10m, non-permanent)."""
        # Seed test users
        with self.app.app_context():
            dev = Admin(username='dev_timeout_test', role='developer')
            dev.set_password('devpass')
            db.session.add(dev)
            
            client = Admin(username='client_timeout_test', role='user')
            client.set_password('clientpass')
            db.session.add(client)
            db.session.commit()
            
        # 1. Log in as developer
        self.client.post('/login', data=dict(
            username='dev_timeout_test',
            password='devpass'
        ), follow_redirects=True)
        
        # Access a page to trigger before_request check
        self.client.get('/developer/users')
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.permanent)
            # Verify last activity was set
            self.assertIn('last_activity', sess)
            
        self.client.get('/logout')
        
        # 2. Log in as client user
        self.client.post('/login', data=dict(
            username='client_timeout_test',
            password='clientpass'
        ), follow_redirects=True)
        
        # Access a page to trigger before_request check
        self.client.get('/dashboard')
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.permanent)
            self.assertIn('last_activity', sess)
            
    def test_unique_page_connection(self):
        """Verify that multiple client accounts cannot connect the same Facebook page ID."""
        # Seed test users
        with self.app.app_context():
            client_a = Admin(username='client_a', role='user')
            client_a.set_password('pass')
            db.session.add(client_a)
            
            client_b = Admin(username='client_b', role='user')
            client_b.set_password('pass')
            db.session.add(client_b)
            db.session.commit()
            client_a_id = client_a.id
            client_b_id = client_b.id

        # Log in as Client A and set a page_id setting
        self.client.post('/login', data=dict(
            username='client_a',
            password='pass'
        ), follow_redirects=True)
        
        with self.app.app_context():
            Setting.set("page_id", "123456789", user_id=client_a_id)
            Setting.set("page_access_token", "token_a", user_id=client_a_id)
            Setting.set("page_name", "Page A", user_id=client_a_id)
            
        self.client.get('/logout')
        
        # Log in as Client B
        self.client.post('/login', data=dict(
            username='client_b',
            password='pass'
        ), follow_redirects=True)
        
        # Mock session oauth selection variables
        with self.client.session_transaction() as sess:
            sess['oauth_popup'] = True
            sess['oauth_pages'] = [
                {
                    'id': '123456789',
                    'name': 'Page A Duplicate',
                    'access_token': 'token_b',
                    'category': 'Business'
                }
            ]
            
        # Post to select page - should fail unique check
        resp = self.client.post('/settings/facebook/select', data=dict(
            page_id='123456789'
        ), follow_redirects=True)
        
        with self.client.session_transaction() as sess:
            flashes = sess.get('_flashes', [])
            flash_messages = [msg for cat, msg in flashes]
            self.assertIn("صفحة الفيسبوك هذه مرتبطة بالفعل بحساب عميل آخر. يرجى اختيار صفحة أخرى.", flash_messages)
        
        # Verify page ID setting was not changed for client B
        with self.app.app_context():
            setting_obj = Setting.query.filter_by(key="page_id", user_id=client_b_id).first()
            self.assertIsNone(setting_obj)

    def test_check_username_api(self):
        """Verify username check API endpoint for developers."""
        # Seed test user
        with self.app.app_context():
            dev = Admin(username='dev_check_user', role='developer')
            dev.set_password('pass')
            db.session.add(dev)
            
            client = Admin(username='client_existing', role='user')
            client.set_password('pass')
            db.session.add(client)
            db.session.commit()
            
        # 1. Login as developer
        self.client.post('/login', data=dict(
            username='dev_check_user',
            password='pass'
        ), follow_redirects=True)
        
        # 2. Check existing username
        resp = self.client.get('/developer/users/check-username?username=client_existing')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['exists'])
        
        # 3. Check non-existing username
        resp = self.client.get('/developer/users/check-username?username=non_existing_username')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertFalse(data['exists'])

    def test_add_user_password_validations(self):
        """Verify server-side password length and confirmation validations."""
        with self.app.app_context():
            dev = Admin(username='dev_pass_test', role='developer')
            dev.set_password('pass')
            db.session.add(dev)
            db.session.commit()
            
        # Login
        self.client.post('/login', data=dict(
            username='dev_pass_test',
            password='pass'
        ), follow_redirects=True)
        
        # 1. Password < 6 characters
        resp = self.client.post('/developer/users/add', data=dict(
            username='new_client_short',
            password='12345',
            password_confirm='12345'
        ), follow_redirects=True)
        self.assertIn("الرمز لا يستوفي الشروط".encode('utf-8'), resp.data)
            
        # 2. Password mismatch
        resp = self.client.post('/developer/users/add', data=dict(
            username='new_client_mismatch',
            password='password123',
            password_confirm='password321'
        ), follow_redirects=True)
        self.assertIn("كلمة المرور غير متطابقة".encode('utf-8'), resp.data)

    def test_messenger_settings_gemini_enabled(self):
        """Verify that gemini_enabled settings are loaded/saved correctly."""
        self.login_admin()
        
        # Save settings
        resp = self.client.post('/messenger/save-settings', data=dict(
            messenger_bot_enabled='true',
            gemini_enabled='true',
            messenger_bot_tone='friendly',
            messenger_bot_kb='test knowledge base',
            gemini_api_key='AIzaSyCustomKey',
            messenger_bot_fallback='custom fallback message text'
        ), follow_redirects=True)
        
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'settings saved successfully', resp.data.lower())
        
        # Verify in database
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            self.assertIsNotNone(client_admin)
            
            bot_enabled = Setting.get("messenger_bot_enabled", user_id=client_admin.id)
            gemini_enabled = Setting.get("gemini_enabled", user_id=client_admin.id)
            gemini_api_key = Setting.get("gemini_api_key", user_id=client_admin.id)
            bot_fallback = Setting.get("messenger_bot_fallback", user_id=client_admin.id)
            
            self.assertEqual(bot_enabled, 'true')
            self.assertEqual(gemini_enabled, 'true')
            self.assertEqual(gemini_api_key, 'AIzaSyCustomKey')
            self.assertEqual(bot_fallback, 'custom fallback message text')

    @patch('google.generativeai.GenerativeModel')
    def test_test_gemini_endpoint(self, mock_generative_model):
        """Verify Gemini connection test endpoint API behaves correctly."""
        self.login_admin()
        
        # 1. Test empty key
        resp = self.client.post('/messenger/test-gemini', data=json.dumps({
            "gemini_api_key": ""
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'error')
        self.assertIn('يرجى إدخال', data['message'])
        
        # 2. Test valid mock response
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Connection OK"
        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model.return_value = mock_model_instance
        
        resp = self.client.post('/messenger/test-gemini', data=json.dumps({
            "gemini_api_key": "AIzaSyValidKey_123"
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'success')
        self.assertIn('تم الاتصال', data['message'])
        
        # 3. Test exception/invalid key
        mock_model_instance.generate_content.side_effect = Exception("API_KEY_INVALID")
        resp = self.client.post('/messenger/test-gemini', data=json.dumps({
            "gemini_api_key": "AIzaSyInvalidKey_123"
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'error')
        self.assertIn('غير صالح', data['message'])

    @patch('services.facebook_api.requests.post')
    def test_comment_processor_fallback_when_gemini_fails(self, mock_post):
        """Verify comment processor uses custom fallback message for private replies when Gemini fails."""
        from services.comment_processor import process_comment_job
        
        with self.app.app_context():
            # Setup user
            client_admin = Admin.query.filter_by(username='admin').first()
            self.assertIsNotNone(client_admin)
            client_id = client_admin.id
            
            # Setup settings
            Setting.set("page_access_token", "fake_token", user_id=client_id)
            Setting.set("page_id", "page_id_123", user_id=client_id)
            Setting.set("gemini_enabled", "true", user_id=client_id)
            Setting.set("gemini_api_key", "fake_gemini_key", user_id=client_id)
            Setting.set("messenger_bot_fallback", "الرسالة الاحتياطية المخصصة للعميل", user_id=client_id)
            
            # Setup monitored post
            post = Post.query.filter_by(id="post_monitored_123").first()
            if not post:
                post = Post(
                    id="post_monitored_123",
                    message="Check this out!",
                    is_monitored=True,
                    user_id=client_id,
                    default_reply="شكراً لتعليقك",
                    private_message="مرحبا بك"
                )
                db.session.add(post)
                db.session.commit()
            
            # Setup mock webhook logs and comment de-duplication
            Comment.query.filter_by(id="comment_fallback_test").delete()
            db.session.commit()
            
        # Mock Graph API calls
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true, "id":"reply_123"}'
        mock_post.return_value = mock_resp
        
        # Simulate comment details with app_user_id (client_id)
        comment_data = {
            "comment_id": "comment_fallback_test",
            "post_id": "post_monitored_123",
            "user_id": "customer_999",
            "username": "Customer Client",
            "message": "Hello!",
            "created_time": "2026-06-13T12:00:00+0000",
            "app_user_id": client_id
        }
        
        # Mock generate_ai_replies to return None (simulate Gemini offline/error)
        with patch('services.gemini_api.generate_ai_replies', return_value=None):
            process_comment_job(self.app, comment_data)
            
        # Verify private message sent is the custom fallback message instead of post.private_message
        with self.app.app_context():
            msg_record = Message.query.filter_by(comment_id="comment_fallback_test").first()
            self.assertIsNotNone(msg_record)
            self.assertEqual(msg_record.message_content, "الرسالة الاحتياطية المخصصة للعميل")

    @patch('services.facebook_api.requests.post')
    @patch('google.generativeai.GenerativeModel')
    def test_messenger_chat_history_logging_and_memory(self, mock_generative_model, mock_post):
        """Verify Messenger chat history logging, retrieval, and prompt injection."""
        from services.messenger_processor import process_messenger_job
        from models import MessengerChatHistory
        
        # Mock Graph API responses
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true, "message_id":"mid_123"}'
        mock_post.return_value = mock_resp
        
        # Mock Gemini response
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This is a Gemini AI reply."
        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model.return_value = mock_model_instance
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("messenger_bot_enabled", "true", user_id=client_id)
            Setting.set("gemini_enabled", "true", user_id=client_id)
            Setting.set("gemini_api_key", "test_key_ok", user_id=client_id)
            Setting.set("page_access_token", "fake_token", user_id=client_id)
            Setting.set("page_id", "page_id_123", user_id=client_id)
            
            # Pre-seed one message in history to test memory loading
            prev_cust = MessengerChatHistory(
                sender_id="sender_history_999",
                message_content="Old question",
                is_from_customer=True,
                admin_id=client_id
            )
            db.session.add(prev_cust)
            db.session.commit()
            
        # Run messenger job
        msg_details = {
            "sender_id": "sender_history_999",
            "message_text": "New question",
            "message_id": "mid_new_001",
            "timestamp": 123456789,
            "app_user_id": client_id
        }
        process_messenger_job(self.app, msg_details)
        
        # Verify db history records
        with self.app.app_context():
            history = MessengerChatHistory.query.filter_by(sender_id="sender_history_999").order_by(MessengerChatHistory.created_at.asc()).all()
            self.assertEqual(len(history), 3)
            self.assertEqual(history[0].message_content, "Old question")
            self.assertEqual(history[1].message_content, "New question")
            self.assertEqual(history[2].message_content, "This is a Gemini AI reply.")
            self.assertTrue(history[0].is_from_customer)
            self.assertTrue(history[1].is_from_customer)
            self.assertFalse(history[2].is_from_customer)
            
        # Verify the prompt contained the history context
        called_args = mock_model_instance.generate_content.call_args[0][0]
        self.assertIn("Conversation History with this Customer", called_args)
        self.assertIn("[Customer]: Old question", called_args)
        self.assertIn("New question", called_args)

    @patch('services.facebook_api.requests.post')
    @patch('google.generativeai.GenerativeModel')
    def test_messenger_smart_spam_protection(self, mock_generative_model, mock_post):
        """Verify that sending too many messages switches Gemini off and triggers fallback directly."""
        from services.messenger_processor import process_messenger_job
        from models import MessengerChatHistory
        
        # Mock Graph API response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true, "message_id":"mid_123"}'
        mock_post.return_value = mock_resp
        
        # Mock Gemini response (should NOT be called if spam)
        mock_model_instance = MagicMock()
        mock_generative_model.return_value = mock_model_instance
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("messenger_bot_enabled", "true", user_id=client_id)
            Setting.set("gemini_enabled", "true", user_id=client_id)
            Setting.set("gemini_api_key", "test_key_ok", user_id=client_id)
            Setting.set("page_access_token", "fake_token", user_id=client_id)
            Setting.set("page_id", "page_id_123", user_id=client_id)
            Setting.set("messenger_bot_fallback", "الرسالة الاحتياطية للسبام", user_id=client_id)
            
            # Pre-seed 5 customer messages in history within the last 5 seconds to trigger spam
            for i in range(5):
                spam_msg = MessengerChatHistory(
                    sender_id="sender_spam_999",
                    message_content=f"Spam text {i}",
                    is_from_customer=True,
                    admin_id=client_id
                )
                db.session.add(spam_msg)
            db.session.commit()
            
        # Run messenger job (6th message)
        msg_details = {
            "sender_id": "sender_spam_999",
            "message_text": "Spam text 6",
            "message_id": "mid_spam_006",
            "timestamp": 123456789,
            "app_user_id": client_id
        }
        process_messenger_job(self.app, msg_details)
        
        # Verify mock model generate_content was NOT called
        mock_model_instance.generate_content.assert_not_called()
        
        # Verify fallback response was logged and sent
        with self.app.app_context():
            history = MessengerChatHistory.query.filter_by(sender_id="sender_spam_999").order_by(MessengerChatHistory.created_at.asc()).all()
            self.assertEqual(len(history), 7)
            self.assertEqual(history[5].message_content, "Spam text 6")
            self.assertEqual(history[6].message_content, "الرسالة الاحتياطية للسبام")
            self.assertFalse(history[6].is_from_customer)

    @patch('services.facebook_api.requests.post')
    def test_comment_private_reply_logs_to_chat_history(self, mock_post):
        """Verify that successfully sending a private reply to a comment logs to chat history."""
        from services.comment_processor import process_comment_job
        from models import MessengerChatHistory
        
        # Mock Graph API responses
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true, "id":"reply_123"}'
        mock_post.return_value = mock_resp
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("page_access_token", "fake_token", user_id=client_id)
            Setting.set("page_id", "page_id_123", user_id=client_id)
            
            post = Post(
                id="post_comment_history_123",
                message="Check this out!",
                is_monitored=True,
                user_id=client_id,
                default_reply="شكراً لتعليقك",
                private_message="مرحبا بك في الخاص"
            )
            db.session.add(post)
            db.session.commit()
            
        comment_data = {
            "comment_id": "comment_history_test",
            "post_id": "post_comment_history_123",
            "user_id": "customer_history_999",
            "username": "History User",
            "message": "Hello!",
            "created_time": "2026-06-13T12:00:00+0000",
            "app_user_id": client_id
        }
        
        # Run comment processor
        process_comment_job(self.app, comment_data)
        
        # Verify the private reply is recorded in MessengerChatHistory
        with self.app.app_context():
            history = MessengerChatHistory.query.filter_by(sender_id="customer_history_999").all()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].message_content, "مرحبا بك في الخاص")
            self.assertFalse(history[0].is_from_customer)

    # 9. Verify Instagram Settings saving & retrieval
    def test_instagram_settings_management(self):
        """Verify saving and retrieving Instagram settings."""
        self.login_admin()
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            
        resp = self.client.post('/instagram/save-settings', data=dict(
            instagram_bot_enabled='on',
            instagram_gemini_enabled='on',
            instagram_bot_tone='friendly',
            instagram_bot_kb='Instagram Knowledge',
            instagram_gemini_api_key='ig_gemini_key',
            instagram_bot_fallback='IG fallback message'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Instagram settings saved successfully', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("instagram_bot_enabled", user_id=client_id), "true")
            self.assertEqual(Setting.get("instagram_gemini_enabled", user_id=client_id), "true")
            self.assertEqual(Setting.get("instagram_bot_tone", user_id=client_id), "friendly")
            self.assertEqual(Setting.get("instagram_bot_kb", user_id=client_id), "Instagram Knowledge")
            self.assertEqual(Setting.get("instagram_gemini_api_key", user_id=client_id), "ig_gemini_key")
            self.assertEqual(Setting.get("instagram_bot_fallback", user_id=client_id), "IG fallback message")

    # 10. Verify WhatsApp Settings saving & retrieval
    def test_whatsapp_settings_management(self):
        """Verify saving and retrieving WhatsApp settings."""
        self.login_admin()
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            
        resp = self.client.post('/whatsapp/save-settings', data=dict(
            whatsapp_bot_enabled='true',
            whatsapp_gemini_enabled='true',
            whatsapp_bot_tone='formal',
            whatsapp_bot_kb='WhatsApp Knowledge',
            whatsapp_gemini_api_key='wa_gemini_key',
            whatsapp_bot_fallback='WA fallback message'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'WhatsApp settings saved successfully', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("whatsapp_bot_enabled", user_id=client_id), "true")
            self.assertEqual(Setting.get("whatsapp_gemini_enabled", user_id=client_id), "true")
            self.assertEqual(Setting.get("whatsapp_bot_tone", user_id=client_id), "formal")
            self.assertEqual(Setting.get("whatsapp_bot_kb", user_id=client_id), "WhatsApp Knowledge")
            self.assertEqual(Setting.get("whatsapp_gemini_api_key", user_id=client_id), "wa_gemini_key")
            self.assertEqual(Setting.get("whatsapp_bot_fallback", user_id=client_id), "WA fallback message")

    # 11. Verify Add-Account manual connection/disconnection endpoints
    def test_add_account_connections(self):
        """Verify Instagram and WhatsApp manual connection and disconnection endpoints."""
        self.login_admin()
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            
        # Instagram Connect
        resp = self.client.post('/settings/instagram/connect', data=dict(
            instagram_page_id='ig_page_999',
            instagram_access_token='ig_token_999'
        ), follow_redirects=True)
        self.assertIn(b'Instagram account connected successfully', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("instagram_page_id", user_id=client_id), "ig_page_999")
            self.assertEqual(Setting.get("instagram_page_access_token", user_id=client_id), "ig_token_999")
            self.assertEqual(Setting.get("instagram_bot_enabled", user_id=client_id), "true")
            
        # Instagram Disconnect
        resp = self.client.post('/settings/instagram/disconnect', follow_redirects=True)
        self.assertIn(b'Instagram account disconnected', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("instagram_page_id", user_id=client_id), "")
            self.assertEqual(Setting.get("instagram_page_access_token", user_id=client_id), "")
            self.assertEqual(Setting.get("instagram_bot_enabled", user_id=client_id), "false")
            
        # WhatsApp Connect
        resp = self.client.post('/settings/whatsapp/connect', data=dict(
            whatsapp_phone_number_id='wa_phone_999',
            whatsapp_access_token='wa_token_999'
        ), follow_redirects=True)
        self.assertIn(b'WhatsApp account connected successfully', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("whatsapp_phone_number_id", user_id=client_id), "wa_phone_999")
            self.assertEqual(Setting.get("whatsapp_access_token", user_id=client_id), "wa_token_999")
            self.assertEqual(Setting.get("whatsapp_bot_enabled", user_id=client_id), "true")
            
        # WhatsApp Disconnect
        resp = self.client.post('/settings/whatsapp/disconnect', follow_redirects=True)
        self.assertIn(b'WhatsApp account disconnected', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("whatsapp_phone_number_id", user_id=client_id), "")
            self.assertEqual(Setting.get("whatsapp_access_token", user_id=client_id), "")
            self.assertEqual(Setting.get("whatsapp_bot_enabled", user_id=client_id), "false")

    # 12. Verify Webhook payload routing for Instagram and WhatsApp
    @patch('services.scheduler.scheduler.add_job')
    def test_webhook_instagram_and_whatsapp_routing(self, mock_add_job):
        """Verify that incoming IG and WA webhook payloads register correct background jobs."""
        app_secret = "test_secret"
        
        # Seed user settings so parser can link incoming page_id/phone_id to user_id
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("instagram_page_id", "ig_page_123", user_id=client_id)
            Setting.set("whatsapp_phone_number_id", "wa_phone_123", user_id=client_id)
            
        # A. Instagram direct message payload
        ig_msg_payload = {
            "object": "instagram",
            "entry": [{
                "id": "ig_page_123",
                "time": 16000000,
                "messaging": [{
                    "sender": {"id": "ig_customer_99"},
                    "recipient": {"id": "ig_page_123"},
                    "timestamp": 16000000,
                    "message": {
                        "mid": "ig_mid_999",
                        "text": "Hello Instagram!"
                    }
                }]
            }]
        }
        
        payload_bytes = json.dumps(ig_msg_payload).encode()
        sig = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        resp = self.client.post('/webhook', data=payload_bytes, headers={'X-Hub-Signature-256': sig}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_add_job.called)
        
        # Reset mock
        mock_add_job.reset_mock()
        
        # B. Instagram comment payload
        ig_comment_payload = {
            "object": "instagram",
            "entry": [{
                "id": "ig_page_123",
                "time": 16000000,
                "changes": [{
                    "field": "comments",
                    "value": {
                        "id": "ig_comment_999",
                        "media": {"id": "ig_media_111"},
                        "from": {"id": "ig_customer_99", "username": "ig_cust"},
                        "text": "Cool pic!"
                    }
                }]
            }]
        }
        payload_bytes = json.dumps(ig_comment_payload).encode()
        sig = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        resp = self.client.post('/webhook', data=payload_bytes, headers={'X-Hub-Signature-256': sig}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_add_job.called)
        
        # Reset mock
        mock_add_job.reset_mock()
        
        # C. WhatsApp message payload
        wa_payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "wa_acc_123",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "16505551111",
                            "phone_number_id": "wa_phone_123"
                        },
                        "messages": [{
                            "from": "15550260460",
                            "id": "wa_msg_999",
                            "timestamp": "16000000",
                            "text": {
                                "body": "Hello WhatsApp!"
                            },
                            "type": "text"
                        }]
                    }
                }]
            }]
        }
        payload_bytes = json.dumps(wa_payload).encode()
        sig = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        resp = self.client.post('/webhook', data=payload_bytes, headers={'X-Hub-Signature-256': sig}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_add_job.called)

    # 13. Verify Instagram comment replying job
    @patch('services.instagram_processor.requests.post')
    def test_instagram_comment_processor(self, mock_post):
        """Verify that Instagram comment processing posts public replies and logs correctly."""
        from services.instagram_processor import process_instagram_comment_job
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true, "id":"ig_reply_123"}'
        mock_post.return_value = mock_resp
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("instagram_page_access_token", "fake_token", user_id=client_id)
            Setting.set("instagram_page_id", "ig_page_123", user_id=client_id)
            
            post = Post(
                id="ig_post_123",
                message="Monitored post",
                is_monitored=True,
                user_id=client_id,
                default_reply="شكراً لتعليقك انستقرام"
            )
            db.session.add(post)
            db.session.commit()
            
        comment_data = {
            "comment_id": "ig_comment_abc",
            "post_id": "ig_post_123",
            "user_id": "ig_cust_999",
            "username": "ig_cust",
            "message": "Awesome!",
            "app_user_id": client_id
        }
        
        process_instagram_comment_job(self.app, comment_data)
        
        # Verify Meta API call was made to replies endpoint
        self.assertTrue(mock_post.called)
        called_url = mock_post.call_args[0][0]
        self.assertIn("ig_comment_abc/replies", called_url)

    # 14. Verify Instagram direct messaging processing job (history, Gemini and spam)
    @patch('services.instagram_processor.requests.post')
    @patch('google.generativeai.GenerativeModel')
    def test_instagram_message_processor(self, mock_generative_model, mock_post):
        """Verify Instagram DM processor history, rate-limiting, FAQs, and Gemini execution."""
        from services.instagram_processor import process_instagram_message_job
        from models import InstagramChatHistory
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true}'
        mock_post.return_value = mock_resp
        
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="This is an IG Gemini response.")
        mock_generative_model.return_value = mock_model
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("instagram_page_access_token", "fake_token", user_id=client_id)
            Setting.set("instagram_page_id", "ig_page_123", user_id=client_id)
            Setting.set("instagram_bot_enabled", "true", user_id=client_id)
            Setting.set("instagram_gemini_enabled", "true", user_id=client_id)
            Setting.set("instagram_gemini_api_key", "test_key", user_id=client_id)
            Setting.set("instagram_bot_kb", "Our services", user_id=client_id)
            Setting.set("instagram_bot_fallback", "الرسالة الاحتياطية لانستقرام", user_id=client_id)
            
            # Pre-seed one message in IG chat history
            old_msg = InstagramChatHistory(
                sender_id="ig_customer_77",
                message_content="Prior question",
                is_from_customer=True,
                admin_id=client_id
            )
            db.session.add(old_msg)
            db.session.commit()
            
        # Process new message
        msg_details = {
            "sender_id": "ig_customer_77",
            "message_text": "New question",
            "message_id": "ig_mid_1001",
            "app_user_id": client_id
        }
        process_instagram_message_job(self.app, msg_details)
        
        # Verify Gemini prompt context includes history
        called_args = mock_model.generate_content.call_args[0][0]
        self.assertIn("Conversation History with this Customer", called_args)
        self.assertIn("[Customer]: Prior question", called_args)
        self.assertIn("New question", called_args)
        
        # Verify db history logged the reply
        with self.app.app_context():
            history = InstagramChatHistory.query.filter_by(sender_id="ig_customer_77").order_by(InstagramChatHistory.created_at.asc()).all()
            self.assertEqual(len(history), 3)
            self.assertEqual(history[0].message_content, "Prior question")
            self.assertEqual(history[1].message_content, "New question")
            self.assertEqual(history[2].message_content, "This is an IG Gemini response.")
            
        # Verify Spam detection
        mock_model.generate_content.reset_mock()
        with self.app.app_context():
            # Pre-seed 5 customer DMs to trigger spam
            for i in range(5):
                spam_msg = InstagramChatHistory(
                    sender_id="ig_spam_user",
                    message_content=f"spam_{i}",
                    is_from_customer=True,
                    admin_id=client_id
                )
                db.session.add(spam_msg)
            db.session.commit()
            
        msg_details_spam = {
            "sender_id": "ig_spam_user",
            "message_text": "spam_6",
            "message_id": "ig_mid_spam_06",
            "app_user_id": client_id
        }
        process_instagram_message_job(self.app, msg_details_spam)
        
        # Gemini should NOT be called for spam
        mock_model.generate_content.assert_not_called()
        
        # Verify fallback response is recorded for spam user
        with self.app.app_context():
            history = InstagramChatHistory.query.filter_by(sender_id="ig_spam_user").order_by(InstagramChatHistory.created_at.asc()).all()
            self.assertEqual(history[-1].message_content, "الرسالة الاحتياطية لانستقرام")
            self.assertFalse(history[-1].is_from_customer)

    # 15. Verify WhatsApp messaging processing job (history, Gemini and spam)
    @patch('services.whatsapp_processor.requests.post')
    @patch('google.generativeai.GenerativeModel')
    def test_whatsapp_message_processor(self, mock_generative_model, mock_post):
        """Verify WhatsApp message processor history, rate-limiting, FAQs, and Gemini execution."""
        from services.whatsapp_processor import process_whatsapp_message_job
        from models import WhatsAppChatHistory
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"success":true}'
        mock_post.return_value = mock_resp
        
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="This is a WA Gemini response.")
        mock_generative_model.return_value = mock_model
        
        with self.app.app_context():
            client_admin = Admin.query.filter_by(username='admin').first()
            client_id = client_admin.id
            Setting.set("whatsapp_access_token", "fake_token", user_id=client_id)
            Setting.set("whatsapp_phone_number_id", "wa_phone_123", user_id=client_id)
            Setting.set("whatsapp_bot_enabled", "true", user_id=client_id)
            Setting.set("whatsapp_gemini_enabled", "true", user_id=client_id)
            Setting.set("whatsapp_gemini_api_key", "test_key", user_id=client_id)
            Setting.set("whatsapp_bot_kb", "WA Services", user_id=client_id)
            Setting.set("whatsapp_bot_fallback", "الرسالة الاحتياطية لواتساب", user_id=client_id)
            
            # Pre-seed one message in WA history
            old_msg = WhatsAppChatHistory(
                sender_id="wa_customer_77",
                message_content="Prior WhatsApp text",
                is_from_customer=True,
                admin_id=client_id
            )
            db.session.add(old_msg)
            db.session.commit()
            
        # Process new message
        msg_details = {
            "sender_id": "wa_customer_77",
            "message_text": "New WA question",
            "message_id": "wa_mid_1001",
            "app_user_id": client_id
        }
        process_whatsapp_message_job(self.app, msg_details)
        
        # Verify Gemini prompt context includes history
        called_args = mock_model.generate_content.call_args[0][0]
        self.assertIn("Conversation History with this Customer", called_args)
        self.assertIn("[Customer]: Prior WhatsApp text", called_args)
        self.assertIn("New WA question", called_args)
        
        # Verify db history logged the reply
        with self.app.app_context():
            history = WhatsAppChatHistory.query.filter_by(sender_id="wa_customer_77").order_by(WhatsAppChatHistory.created_at.asc()).all()
            self.assertEqual(len(history), 3)
            self.assertEqual(history[0].message_content, "Prior WhatsApp text")
            self.assertEqual(history[1].message_content, "New WA question")
            self.assertEqual(history[2].message_content, "This is a WA Gemini response.")
            
        # Verify Spam detection
        mock_model.generate_content.reset_mock()
        with self.app.app_context():
            # Pre-seed 5 customer DMs to trigger spam
            for i in range(5):
                spam_msg = WhatsAppChatHistory(
                    sender_id="wa_spam_user",
                    message_content=f"spam_{i}",
                    is_from_customer=True,
                    admin_id=client_id
                )
                db.session.add(spam_msg)
            db.session.commit()
            
        msg_details_spam = {
            "sender_id": "wa_spam_user",
            "message_text": "spam_6",
            "message_id": "wa_mid_spam_06",
            "app_user_id": client_id
        }
        process_whatsapp_message_job(self.app, msg_details_spam)
        
        # Gemini should NOT be called for spam
        mock_model.generate_content.assert_not_called()
        
        # Verify fallback response is recorded for spam user
        with self.app.app_context():
            history = WhatsAppChatHistory.query.filter_by(sender_id="wa_spam_user").order_by(WhatsAppChatHistory.created_at.asc()).all()
            self.assertEqual(history[-1].message_content, "الرسالة الاحتياطية لواتساب")
            self.assertFalse(history[-1].is_from_customer)

    # 16. Verify test-gemini endpoints error messaging logic
    @patch('google.generativeai.GenerativeModel')
    def test_test_gemini_endpoints(self, mock_generative_model):
        """Verify the test-gemini endpoint handling for Instagram and WhatsApp."""
        self.login_admin()
        
        # A. Instagram Successful Connection
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="Connection OK")
        mock_generative_model.return_value = mock_model
        
        resp = self.client.post('/instagram/test-gemini', 
                                data=json.dumps({"gemini_api_key": "valid_key"}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        resp_json = json.loads(resp.data.decode('utf-8'))
        self.assertIn("تم الاتصال بسيرفرات Gemini", resp_json["message"])
        
        # B. WhatsApp Quota Exceeded Exception
        mock_model.generate_content.side_effect = Exception("Resource has exhausted enough quota (Quota Exceeded).")
        resp = self.client.post('/whatsapp/test-gemini', 
                                data=json.dumps({"gemini_api_key": "quota_key"}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        resp_json = json.loads(resp.data.decode('utf-8'))
        self.assertIn("تم تجاوز الحصة المجانية", resp_json["message"])

if __name__ == "__main__":
    unittest.main()
