"""Vercel serverless function entry point for Slack."""

import os
import sys

# srcディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, Response
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler

# Slack Appを作成
slack_app = SlackApp(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

# ハンドラーを登録
from src.handlers import register_commands, register_events, register_actions
register_commands(slack_app)
register_events(slack_app)
register_actions(slack_app)

# Flask app (Vercelが認識する名前は 'app')
app = Flask(__name__)
slack_handler = SlackRequestHandler(slack_app)


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
        except:
            pass

    return slack_handler.handle(request)
