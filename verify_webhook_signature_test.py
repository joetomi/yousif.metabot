import hmac
import hashlib
import sys
from routes.webhook import verify_signature

def run_tests():
    print("Starting HMAC-SHA256 signature verification tests...")
    
    secret = "my_fb_app_secret_123456"
    payload = b'{"object":"page","entry":[{"id":"123","time":16000000,"changes":[{"field":"feed","value":{"item":"comment","verb":"add"}}]}]}'
    
    # 1. Correct Signature Test
    computed_sig = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    signature_header = f"sha256={computed_sig}"
    
    assert verify_signature(payload, signature_header, secret) == True, "Failed: Correct signature rejected!"
    print("Success: Correct signature validated successfully.")

    # 2. Incorrect Signature Test
    wrong_signature_header = "sha256=wrongsignaturevalue1234567890abcdef"
    assert verify_signature(payload, wrong_signature_header, secret) == False, "Failed: Incorrect signature accepted!"
    print("Success: Incorrect signature rejected successfully.")

    # 3. Missing/Malformed Signature Test
    assert verify_signature(payload, "malformed_signature_without_prefix", secret) == False, "Failed: Malformed header accepted!"
    assert verify_signature(payload, None, secret) == False, "Failed: Missing signature accepted!"
    print("Success: Malformed and missing signatures rejected successfully.")

    # 4. Missing secret test (disabled state)
    assert verify_signature(payload, signature_header, None) == False, "Failed: Verification passed with no configured secret!"
    print("Success: Empty secret handles validation safely by rejecting.")

    print("All signature tests passed successfully!")

if __name__ == "__main__":
    try:
        run_tests()
    except AssertionError as e:
        print(f"Test assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error running tests: {e}", file=sys.stderr)
        sys.exit(1)
