"""Vercel serverless function entry point."""

import os
import json
from dotenv import load_dotenv

load_dotenv()

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# Slack Appを作成
app = App(
    token=os.getenv("SLACK_BOT_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    process_before_response=True,  # サーバーレスでは必須
)

# ハンドラーを登録
from src.handlers import register_commands, register_events, register_actions
register_commands(app)
register_events(app)
register_actions(app)


def handler(request, context=None):
    """Vercel serverless handler"""
    # Vercelのリクエスト形式をSlack Bolt形式に変換

    if request.method == "GET":
        return {
            "statusCode": 200,
            "body": "Accounting Bot is running!"
        }

    # POSTリクエストの処理
    body = request.body.decode("utf-8") if hasattr(request, 'body') else ""

    # URL verification challenge
    if body:
        try:
            body_json = json.loads(body)
            if body_json.get("type") == "url_verification":
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "text/plain"},
                    "body": body_json.get("challenge", "")
                }
        except json.JSONDecodeError:
            pass

    # Slack Boltで処理
    slack_handler = SlackRequestHandler(app)

    # AWS Lambda形式のイベントを構築
    event = {
        "body": body,
        "headers": dict(request.headers) if hasattr(request, 'headers') else {},
        "httpMethod": request.method,
        "isBase64Encoded": False,
    }

    response = slack_handler.handle(event, context or {})

    return {
        "statusCode": response.get("statusCode", 200),
        "headers": response.get("headers", {}),
        "body": response.get("body", "")
    }
