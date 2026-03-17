import base64
import json
import firebase_admin
from firebase_admin import credentials, storage
from ..core.config import settings
import logging

logger = logging.getLogger("ig-automator.firebase")

_firebase_app = None

def get_firebase_app():
    global _firebase_app
    if _firebase_app:
        return _firebase_app

    if not settings.FIREBASE_SERVICE_ACCOUNT_BASE64:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_BASE64 not set!")
        return None

    try:
        service_account_info = json.loads(
            base64.b64decode(settings.FIREBASE_SERVICE_ACCOUNT_BASE64).decode("utf-8")
        )
        cred = credentials.Certificate(service_account_info)
        _firebase_app = firebase_admin.initialize_app(cred, {
            'storageBucket': settings.FIREBASE_STORAGE_BUCKET
        })
        return _firebase_app
    except Exception as e:
        logger.error("Failed to initialize Firebase: %s", e)
        return None

async def upload_image(file_content: bytes, filename: str) -> str:
    """Uploads an image to Firebase Storage and returns the public URL."""
    app = get_firebase_app()
    if not app:
        raise Exception("Firebase not initialized")

    bucket = storage.bucket(app=app)
    blob = bucket.blob(f"templates/{filename}")

    # In a real async environment with motor/fastapi,
    # we might want to run this in a threadpool if it's blocking
    blob.upload_from_string(file_content, content_type='image/jpeg')

    # When uniform bucket-level access is enabled, make_public() fails.
    # Instead, we construct the standard public URL ourselves if permissions are set correctly.
    # Alternatively, use signed URLs if public-by-default is not possible.
    # For Firebase, this is the standard URL format:
    # return blob.public_url

    # We remove blob.make_public() to fix the 400 GET ACL error.
    # blob.make_public()

    # Public URL format for Google Cloud Storage:
    # https://storage.googleapis.com/{bucket_name}/{blob_name}
    # Note: For this to work without make_public(), the bucket should have
    # 'Storage Object Viewer' permission for 'allUsers' if public access is desired.
    return f"https://storage.googleapis.com/{bucket.name}/{blob.name}"
