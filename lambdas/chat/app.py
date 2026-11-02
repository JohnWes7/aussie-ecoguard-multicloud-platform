"""POST /chat: send the user's text to the Lex V2 bot and return its reply.

Body: {"text": "show me fox photos from yesterday", "sessionId": "abc123"}
Reply: {"message": "...", "intent": "FindSightings",
        "results": [{"file_id", "filename", "file_type", "created_at",
                     "thumbnail_url", "file_url"}]}

The bot's fulfilment Lambda (lambdas/search, handle_lex_request) puts the
matching files in the session attribute "results" as S3 keys. This function
signs them, so the website can show clickable thumbnails in the chat.
"""

import json
import os
import re

import boto3
from botocore.config import Config

lex_client = boto3.client("lexv2-runtime")
# Regional endpoint for presigned URLs (bucket.s3.<region>.amazonaws.com).
s3_client = boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))

BOT_ID = os.environ.get("BOT_ID")
BOT_ALIAS_ID = os.environ.get("BOT_ALIAS_ID")
BUCKET_NAME = os.environ.get("BUCKET_NAME")
LOCALE_ID = "en_US"
TIME_ZONE = "Australia/Melbourne"  # so Lex reads "yesterday" as the user's yesterday
MAX_TEXT = 1024  # Lex's limit for RecognizeText
URL_EXPIRES = 3600
SESSION_RE = re.compile(r"^[0-9a-zA-Z._:-]{2,100}$")  # Lex's sessionId rule
FALLBACK = "Sorry, I didn't understand that. Try \"show me fox photos from yesterday\" or \"help me find a lost ginger cat\"."


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, json.JSONDecodeError):
        return response(400, {"error": "Body must be JSON"})
    if not isinstance(body, dict):
        return response(400, {"error": "Body must be a JSON object"})

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return response(400, {"error": "Type a message first"})
    if len(text) > MAX_TEXT:
        return response(400, {"error": f"Messages can be at most {MAX_TEXT} characters"})

    # One Lex conversation per browser tab, kept apart per user.
    sub = (((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}).get("sub", "")
    session_id = str(body.get("sessionId") or "")
    if sub:
        session_id = f"{sub}:{session_id}"[:100] if session_id else sub
    if not SESSION_RE.match(session_id):
        session_id = re.sub(r"[^0-9a-zA-Z._:-]", "", session_id)[:100] or "default-session"
        if len(session_id) < 2:
            session_id = "default-session"

    try:
        reply = lex_client.recognize_text(
            botId=BOT_ID,
            botAliasId=BOT_ALIAS_ID,
            localeId=LOCALE_ID,
            sessionId=session_id,
            text=text.strip(),
            requestAttributes={"x-amz-lex:time-zone": TIME_ZONE},
        )
    except Exception as err:
        print(f"Lex RecognizeText failed: {err!r}")  # details go to CloudWatch, not the browser
        return response(502, {"error": "The chatbot is not available right now. Please try again in a moment."})

    messages = [m.get("content", "") for m in reply.get("messages") or [] if m.get("content")]
    state = reply.get("sessionState") or {}
    return response(200, {
        "message": "\n".join(messages) or FALLBACK,
        "intent": (state.get("intent") or {}).get("name"),
        "results": signed_results((state.get("sessionAttributes") or {}).get("results")),
    })


def signed_results(raw):
    """The fulfilment Lambda's file list, with the S3 keys turned into signed URLs."""
    try:
        files = json.loads(raw) if raw else []
    except (TypeError, json.JSONDecodeError):
        return []
    results = []
    for f in files if isinstance(files, list) else []:
        if not isinstance(f, dict) or not f.get("file_key"):
            continue
        results.append({
            "file_id": f.get("file_id"),
            "filename": f.get("filename"),
            "file_type": f.get("file_type"),
            "created_at": f.get("created_at"),
            "thumbnail_url": sign(f["thumb_key"]) if f.get("thumb_key") else None,
            "file_url": sign(f["file_key"]),
        })
    return results


def sign(key):
    return s3_client.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=URL_EXPIRES
    )


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"},
        "body": json.dumps(body),
    }
