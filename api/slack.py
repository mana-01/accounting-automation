"""Vercel serverless function entry point for Slack."""

import os
import json
from flask import Flask, request, Response
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk import WebClient

# Flask app
app = Flask(__name__)

# Slack App
slack_app = SlackApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

slack_handler = SlackRequestHandler(slack_app)


# === Slack Commands ===

@slack_app.command("/accounting-help")
def handle_help(ack, respond):
    """ヘルプを表示"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": """*経理自動化Bot ヘルプ*

*コマンド一覧:*
• `/accounting-help` - このヘルプを表示
• `/accounting-status` - 今月の経理状況を確認
• `/accounting-subscriptions` - サブスク一覧を表示
• `/accounting-add-subscription` - 新しいサブスクを登録

*使い方:*
1. カード/銀行のCSV明細をこのチャンネルにアップロード
2. 自動で照会が実行されます
3. 不足している請求書がリストアップされます

*請求書の保存:*
PDFファイルをアップロードすると、Google Driveに保存できます。"""
    })


@slack_app.command("/accounting-status")
def handle_status(ack, respond):
    """状況を表示"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📊 *経理状況*\n\n現在セットアップ中です。CSVをアップロードして照会を開始してください。"
    })


@slack_app.command("/accounting-subscriptions")
def handle_subscriptions(ack, respond):
    """サブスク一覧"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📋 *サブスクリプション一覧*\n\nまだ登録されていません。`/accounting-add-subscription` で登録してください。"
    })


@slack_app.command("/accounting-add-subscription")
def handle_add_subscription(ack, client, body):
    """サブスク登録モーダル"""
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "add_subscription_modal",
            "title": {"type": "plain_text", "text": "サブスク登録"},
            "submit": {"type": "plain_text", "text": "登録"},
            "close": {"type": "plain_text", "text": "キャンセル"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "name_block",
                    "label": {"type": "plain_text", "text": "サービス名"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "name_input",
                        "placeholder": {"type": "plain_text", "text": "例: AWS, GitHub, Notion"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "amount_block",
                    "label": {"type": "plain_text", "text": "月額 (円)"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "amount_input",
                        "placeholder": {"type": "plain_text", "text": "例: 10000"}
                    }
                }
            ]
        }
    )


@slack_app.command("/accounting-invoices")
def handle_invoices(ack, respond):
    """請求書一覧"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📄 *請求書一覧*\n\nまだ請求書がありません。PDFをアップロードするか、メールから自動取得してください。"
    })


@slack_app.command("/accounting-fetch-invoices")
def handle_fetch_invoices(ack, respond):
    """請求書取得"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📥 *請求書取得*\n\nメールからの自動取得機能は現在準備中です。"
    })


# === Slack Events ===

@slack_app.event("file_shared")
def handle_file_shared(event, client, say):
    """ファイルアップロード時の処理"""
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    try:
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "")
        file_type = file_data.get("filetype", "")

        if file_type == "csv" or file_name.endswith(".csv"):
            say(
                channel=channel_id,
                text=f"📄 CSV ファイル `{file_name}` を検出しました。\n照会処理を開始します..."
            )
            # TODO: CSV処理を実装
            say(
                channel=channel_id,
                text="✅ 照会が完了しました！（デモ版）"
            )
        elif file_type == "pdf" or file_name.endswith(".pdf"):
            say(
                channel=channel_id,
                text=f"📎 PDF ファイル `{file_name}` を検出しました。\n請求書として保存しますか？",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"📎 PDF ファイル `{file_name}` を検出しました。"}
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "請求書として保存"},
                                "style": "primary",
                                "action_id": "save_invoice_pdf",
                                "value": file_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "スキップ"},
                                "action_id": "skip_file",
                                "value": file_id
                            }
                        ]
                    }
                ]
            )
    except Exception as e:
        print(f"Error handling file: {e}")


# === Flask Routes ===

@app.route("/", methods=["GET"])
@app.route("/api/slack", methods=["GET"])
def health():
    """Health check"""
    return "Accounting Bot is running!"


@app.route("/", methods=["POST"])
@app.route("/api/slack", methods=["POST"])
def slack_events():
    """Handle Slack events"""
    return slack_handler.handle(request)
