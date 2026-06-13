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
        self.assertIn(b'Dashboard', resp.data)
        
        # Test logout clears session
        resp = self.client.get('/logout', follow_redirects=True)
        self.assertIn(b'Admin Login', resp.data)
        
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
        # Initial access (defaults to English LTR)
        self.login_admin()
        resp = self.client.get('/dashboard')
        self.assertIn(b'Dashboard', resp.data)
        self.assertNotIn(b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85', resp.data) # arabic representation of "Dashboard"
        
        # Toggle language to Arabic
        self.client.get('/toggle-lang')
        resp = self.client.get('/dashboard')
        # Arabic representation check
        self.assertIn(b'\xd9\x84\xd9\x88\xd8\xad\xd8\xa9 \xd8\xa7\xd9\x84\xd8\xaa\xd8\xad\xd9\x83\xd9\x85', resp.data) # arabic title checks
        
        # Toggle back to English
        self.client.get('/toggle-lang')
        resp = self.client.get('/dashboard')
        self.assertIn(b'Dashboard', resp.data)

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
        self.assertIn(b'Messenger Auto-Responder Settings', resp.data)
        
        # Save settings
        resp = self.client.post('/messenger/save-settings', data=dict(
            messenger_bot_enabled='true',
            messenger_bot_tone='casual',
            messenger_bot_kb='Custom KB plans'
        ), follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Messenger Bot settings saved successfully!', resp.data)
        
        with self.app.app_context():
            self.assertEqual(Setting.get("messenger_bot_enabled"), "true")
            self.assertEqual(Setting.get("messenger_bot_tone"), "casual")
            self.assertEqual(Setting.get("messenger_bot_kb"), "Custom KB plans")
            
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

if __name__ == "__main__":
    unittest.main()
