"""Vercel serverless function entry point for Slack - Minimal version."""

from flask import Flask, request, Response

# Flask app
app = Flask(__name__)


@app.route("/", methods=["GET"])
@app.route("/api/slack", methods=["GET"])
def health():
    """Health check"""
    return "Accounting Bot is running!"


@app.route("/", methods=["POST"])
@app.route("/api/slack", methods=["POST"])
def slack_events():
    """Handle Slack events"""
    # URL verification challenge
    if request.content_type and "application/json" in request.content_type:
        try:
            body = request.get_json(silent=True)
            if body and body.get("type") == "url_verification":
                return Response(
                    body.get("challenge", ""),
                    mimetype="text/plain"
                )
            # Echo back for testing
            return {"ok": True, "received": body}
        except Exception as e:
            return {"error": str(e)}, 500

    return {"ok": True}
